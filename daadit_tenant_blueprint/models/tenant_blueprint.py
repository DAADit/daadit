# -*- coding: utf-8 -*-
"""Export a vestiging's agent configuration as a reproducible file.

The point is repeatability: a new customer (uitzendkracht.ai,
uitzendwerk.ai, a second vestiging) should start from a known-good set
of colleagues, topics, schedules, limits and plan settings instead of
someone rebuilding it from memory in the UI.

Three rules make it safe to hand around:

1. **No secrets, ever.** Every section is built from an explicit
   allowlist of field names, and the finished blueprint is scanned once
   more: a key whose name suggests a credential aborts the export. The
   failure mode is a missing setting, never a leaked API key.
2. **No customer data.** The blueprint carries the rules — which models
   a colleague may read, which domain narrows that — not the records
   those rules point at, and no tokens, urls, members or audit trail.
3. **No ids.** Records are matched by name (a tenant by its slug),
   because ids from one database mean nothing in another.

What an import may do is deliberately narrow. It writes configuration on
records that already exist and it creates what cannot do harm on its own
(topics, scope lines, inactive schedules). It never creates a tenant —
that needs an endpoint and credentials, which belong to the environment —
and it never switches a schedule on: an agent that starts running because
a file was imported is an agent nobody approved.
"""
import json
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# v1 described colleagues, topics, schedules and parameters. v2 adds the
# governance around them: read/activity/repair scopes per colleague, the
# model allow- and blocklists, and the MCP tenants with their
# environment, capabilities and limits. v1 files stay readable, because
# the stored configuration snapshots (daadit.config.snapshot) contain
# them and have to remain restorable.
BLUEPRINT_VERSION = 2
SUPPORTED_VERSIONS = (1, 2)

# Config parameters that describe HOW the product behaves for a
# customer. Anything outside this list is skipped, so adding a new
# secret elsewhere can never silently start travelling along.
EXPORTED_PARAM_PREFIXES = (
    "daadit_ai_agent_schedule.plan_",
    "daadit_ai_agent_schedule.fair_use_",
    "daadit_ai_agent_schedule.unit_",
    "daadit_ai_mistral.daily_cost_cap_usd",
    "daadit_ai_mistral.fallback_provider",
    "daadit_ai_mistral.fallback_model",
    "daadit_ai_claude.max_tool_iterations",
    "daadit_mcp_multi_tenant.audit_arguments_retention_days",
    "daadit_tenant_blueprint.snapshot_retention_days",
)

# Regels die een mens moet oppakken worden gemarkeerd, zodat het
# verslag ze als checklist kan afsplitsen zonder op woorden te raden.
# Wie een nieuwe melding toevoegt die om een mens vraagt, zet hem via
# ``_manual`` en hij staat vanzelf in de checklist.
MANUAL_MARK = "\u25a1 "

# De sleutelparameter per provider, om aan de naam van een taalmodel te
# koppelen. Alleen de aanwezigheid wordt gelezen, nooit de waarde: een
# rapport dat een sleutel citeert is zelf een lek.
PROVIDER_KEY_PARAMS = (
    ("mistral", "daadit_ai_mistral.mistral_key"),
    ("claude", "daadit_ai_claude.claude_key"),
    ("anthropic", "daadit_ai_claude.claude_key"),
    ("loes", "daadit_ai_loes.loes_key"),
)

# Second net, independent of the allowlist above. Matches the way
# credentials are actually named in this codebase.
_SECRET_RE = re.compile(
    r"(key|token|secret|password|passwd|credential|bearer|cookie|"
    r"signature|salt|dsn|webhook)",
    re.IGNORECASE,
)

# Scalar fields per record type. Exported only when the field exists in
# this database, so a tenant without the schedule- or MCP-module simply
# describes less instead of failing.
AGENT_VALUE_FIELDS = (
    "active",
    "llm_model",
    "subtitle",
    "system_prompt",
    "daadit_field_blocklist",
    "daadit_daily_cost_cap_eur",
    "daadit_monthly_cost_cap_eur",
    "daadit_office",
    "x_role",
    "x_rechten",
)

# The prompt travels along so a reviewer can read what a colleague was
# told, but it is not written back: the Knowledge-registry is the source
# of truth for prompts and syncs them hourly. An import that wrote
# prompts would fight that sync.
AGENT_READONLY_FIELDS = ("system_prompt",)

TOPIC_VALUE_FIELDS = ("description", "instructions")

SCHEDULE_VALUE_FIELDS = (
    "prompt",
    "interval_number",
    "interval_type",
    "daily_cost_cap_eur",
    "monthly_cost_cap_eur",
    "failure_threshold",
)

# Tenant configuration on mcp.instance. Everything that identifies the
# endpoint (url, db, username), authenticates against it (api key,
# bearer token, webhook secret), or belongs to the customer rather than
# the configuration (owner, members, acknowledgement, DPA, audit trail,
# counters) is absent by construction: it is not on this list. The
# display name is absent too: "Acme Productie" names a customer, and a
# blueprint travels between customers.
TENANT_VALUE_FIELDS = (
    "active",
    "environment",
    "endpoint_mode",
    "plan",
    "tenant_version",
    "rate_limit_per_minute",
    "enforce_daily_quota",
    "daily_quota_cap",
    "enable_audit_log",
    "caller_ip_redact_days",
    "field_blocklist",
    "execute_kw_allowed_methods",
    "capability_rate_overrides",
    "failed_auth_alert_threshold",
    "failed_auth_alert_window_minutes",
    "agent_work_package_allowed",
)


def _looks_secret(key):
    """True when a parameter or field name suggests it carries a credential."""
    return bool(_SECRET_RE.search(key or ""))


def _manual(message):
    """Markeer een regel als werk voor een mens."""
    return "%s%s" % (MANUAL_MARK, message)


class TenantBlueprint(models.TransientModel):
    """Wizard: read the current configuration out, or write one in."""

    _name = "daadit.tenant.blueprint"
    _description = "Vestigingsblueprint (export/import zonder secrets)"

    name = fields.Char(default="blueprint.json", readonly=True)
    payload = fields.Text(
        string="Blueprint",
        help="De configuratie als JSON. Plak hier een bestaande "
        "blueprint om hem toe te passen.",
    )
    summary = fields.Text(readonly=True)

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------
    @api.model
    def _present_fields(self, model_name, candidates):
        """The subset of ``candidates`` that this database actually has.

        Studio fields (``x_...``) and fields from modules that are not
        installed here differ per database; a blueprint describes what
        exists instead of refusing to describe anything.
        """
        model_fields = self.env[model_name]._fields
        return tuple(name for name in candidates if name in model_fields)

    @api.model
    def _record_values(self, record, field_names):
        """Scalar values of one record, many2one flattened to its name."""
        out = {}
        for name in field_names:
            field = record._fields[name]
            value = record[name]
            if field.type == "many2one":
                out[name] = value.display_name or "" if value else ""
            elif field.type in ("char", "text", "selection"):
                out[name] = value or ""
            elif field.type == "boolean":
                out[name] = bool(value)
            else:
                out[name] = value
        return out

    @api.model
    def _export_params(self):
        icp = self.env["ir.config_parameter"].sudo()
        out = {}
        for param in icp.search([]):
            key = param.key or ""
            if not key.startswith(EXPORTED_PARAM_PREFIXES):
                continue
            if _looks_secret(key):
                _logger.info(
                    "blueprint: %s overgeslagen \u2014 de naam wijst op een "
                    "geheim", key,
                )
                continue
            out[key] = param.value or ""
        return out

    @api.model
    def _export_agent_scopes(self, agent, spec):
        """De harde grenzen van één collega, per modelnaam.

        Zonder deze regels beschrijft een blueprint alleen het gedrag van
        een collega en niet zijn bevoegdheid: een gegroeide leesscope
        (Bram kreeg project.task, Eva account.move.line) zou op een
        tweede omgeving stilzwijgend ontbreken \u2014 of, erger, ontbreken
        zonder dat iemand het merkt.
        """
        fields_present = agent._fields
        if "daadit_read_scope_ids" in fields_present:
            spec["read_scopes"] = [
                {
                    "model": line.model_name or "",
                    "domain": line.domain or "[]",
                    "active": bool(line.active),
                }
                for line in agent.with_context(
                    active_test=False,
                ).daadit_read_scope_ids.sorted(
                    lambda line: (line.model_name or "")
                )
            ]
        if "daadit_activity_scope_ids" in fields_present:
            spec["activity_scopes"] = [
                {
                    "model": line.model_name or "",
                    "record_domain": line.record_domain or "[]",
                    "active": bool(line.active),
                }
                for line in agent.with_context(
                    active_test=False,
                ).daadit_activity_scope_ids.sorted(
                    lambda line: (line.model_name or "")
                )
            ]
        if "daadit_repair_scope_ids" in fields_present:
            spec["repair_scopes"] = [
                {
                    "target_agent": line.target_agent_id.name or "",
                    "article_ref": line.article_ref or 0,
                    "note": line.note or "",
                    "active": bool(line.active),
                }
                for line in agent.with_context(
                    active_test=False,
                ).daadit_repair_scope_ids.sorted(
                    lambda line: (line.target_agent_id.name or "")
                )
            ]

    @api.model
    def _export_agents(self):
        Agent = self.env["ai.agent"].sudo().with_context(active_test=False)
        value_fields = self._present_fields("ai.agent", AGENT_VALUE_FIELDS)
        scoped = "daadit_allowed_model_ids" in Agent._fields
        out = []
        for agent in Agent.search([]):
            spec = {"name": agent.name}
            spec.update(self._record_values(agent, value_fields))
            spec["topics"] = sorted(agent.topic_ids.mapped("name"))
            if scoped:
                spec["read_scope"] = sorted(
                    agent.daadit_allowed_model_ids.mapped("model")
                )
                spec["blocked_models"] = sorted(
                    agent.daadit_blocked_model_ids.mapped("model")
                )
            self._export_agent_scopes(agent, spec)
            out.append(spec)
        return out

    @api.model
    def _export_topics(self):
        value_fields = self._present_fields("ai.topic", TOPIC_VALUE_FIELDS)
        out = []
        for topic in self.env["ai.topic"].sudo().search([]):
            spec = {"name": topic.name}
            spec.update(self._record_values(topic, value_fields))
            # Server actions are code and live in the module, not in
            # the blueprint. Names are enough to re-link them.
            spec["tools"] = sorted(topic.tool_ids.mapped("name"))
            out.append(spec)
        return out

    @api.model
    def _export_schedules(self):
        if "daadit.ai.agent.schedule" not in self.env:
            # The schedule module is not a dependency: a tenant that
            # only chats has no schedules to describe.
            return []
        Schedule = self.env["daadit.ai.agent.schedule"]
        value_fields = self._present_fields(
            "daadit.ai.agent.schedule", SCHEDULE_VALUE_FIELDS,
        )
        out = []
        for sched in Schedule.sudo().with_context(
            active_test=False,
        ).search([]):
            spec = {
                "name": sched.name,
                "active": bool(sched.active),
                "agent": sched.agent_id.name or "",
            }
            spec.update(self._record_values(sched, value_fields))
            out.append(spec)
        return out

    @api.model
    def _export_tenants(self):
        """De klantomgevingen met hun grenzen, zonder endpoint of sleutel.

        Een blueprint moet kunnen aantonen dat een tweede omgeving
        dezelfde grenzen krijgt: welke capabilities aanstaan, welke
        modellen mogen, welk plan en welke limieten gelden, en of het om
        dev, staging of productie gaat. Wat de omgeving *is* \u2014 url,
        database, gebruiker, sleutel, token \u2014 hoort daar niet bij en
        staat niet op de allowlist.
        """
        if "mcp.instance" not in self.env:
            return []
        Instance = self.env["mcp.instance"].sudo().with_context(
            active_test=False,
        )
        value_fields = self._present_fields("mcp.instance", TENANT_VALUE_FIELDS)
        out = []
        for instance in Instance.search([]):
            spec = {"slug": instance.slug or ""}
            spec.update(self._record_values(instance, value_fields))
            spec["capabilities"] = sorted(
                instance.capability_ids.mapped("code")
            )
            spec["restricted_capabilities"] = sorted(
                instance.restricted_capability_ids.mapped("code")
            )
            spec["allowed_models"] = sorted(
                instance.allowed_model_ids.mapped("model")
            )
            spec["blocked_models"] = sorted(
                instance.blocked_model_ids.mapped("model")
            )
            out.append(spec)
        return out

    # ------------------------------------------------------------------
    @api.model
    def _assert_no_secrets(self, payload, path="blueprint"):
        """Laatste controle: geen enkele sleutelnaam mag op een geheim wijzen.

        De allowlists hierboven zijn de echte bescherming; deze controle
        is de vangrail eronder. Wie later een veld toevoegt dat
        ``token`` of ``key`` heet, krijgt een harde fout in plaats van
        een export die het geheim meeneemt.
        """
        if isinstance(payload, dict):
            for key, value in payload.items():
                if _looks_secret(key):
                    raise UserError(_(
                        "De export is afgebroken: %(path)s bevat de sleutel "
                        "%(key)s, en die naam wijst op een geheim. Voeg het "
                        "veld niet toe aan de blueprint, of geef het een "
                        "naam die geen geheim beschrijft.",
                        path=path, key=key,
                    ))
                self._assert_no_secrets(value, "%s.%s" % (path, key))
        elif isinstance(payload, list):
            for index, value in enumerate(payload):
                self._assert_no_secrets(value, "%s[%d]" % (path, index))

    @api.model
    def build_blueprint(self):
        """The whole configuration as a plain dict."""
        blueprint = {
            "blueprint_version": BLUEPRINT_VERSION,
            "exported_at": fields.Datetime.to_string(
                fields.Datetime.now(),
            ),
            "source_db": self.env.cr.dbname,
            "topics": self._export_topics(),
            "agents": self._export_agents(),
            "schedules": self._export_schedules(),
            "tenants": self._export_tenants(),
            "parameters": self._export_params(),
        }
        self._assert_no_secrets(blueprint)
        return blueprint

    def action_export(self):
        self.ensure_one()
        blueprint = self.build_blueprint()
        self.payload = json.dumps(blueprint, indent=2, ensure_ascii=False)
        self.summary = _(
            "%(agents)s collega's, %(topics)s onderwerpen, "
            "%(schedules)s planningen, %(tenants)s klantomgevingen, "
            "%(params)s instellingen. "
            "Geen sleutels, tokens of wachtwoorden \u2014 die horen bij de "
            "omgeving, niet bij de configuratie.",
            agents=len(blueprint["agents"]),
            topics=len(blueprint["topics"]),
            schedules=len(blueprint["schedules"]),
            tenants=len(blueprint["tenants"]),
            params=len(blueprint["parameters"]),
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_export_package(self):
        """Bewaar de huidige configuratie als genummerd pakket.

        Een blueprint in een tekstveld is een bestand dat iemand moet
        bewaren; een pakket is een rij met een nummer, een tijdstip en een
        maker, en dus iets om naar terug te rollen. Is de configuratie
        gelijk aan het laatste pakket, dan komt er geen tweede bij: dat
        pakket ís de stand van nu.
        """
        self.ensure_one()
        Package = self.env["daadit.config.snapshot"]
        latest = Package.latest_package()
        package = Package.capture(trigger="manual")
        if not package:
            self.summary = _(
                "De configuratie is gelijk aan pakket %(number)s "
                "(%(moment)s); er is niets nieuws bewaard. Terugrollen naar "
                "de stand van nu is dus dat pakket.",
                number=latest.package_number if latest else 0,
                moment=latest.create_date if latest else "",
            )
        else:
            self.payload = package.payload
            self.summary = _(
                "Bewaard als %(name)s. Terugrollen kan via "
                "Instellingen \u2192 Configuratiepakketten: eerst de proef, "
                "dan terugzetten.",
                name=package.name,
            )
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------
    @api.model
    def _apply_values(self, record, spec, field_names, dry_run):
        """Schrijf de toegestane velden en meld wat er verandert."""
        vals = {}
        for name in field_names:
            if name not in spec:
                continue
            new = spec[name]
            field = record._fields[name]
            if field.type == "boolean":
                new = bool(new)
            current = record[name]
            if field.type in ("char", "text", "selection"):
                current = current or ""
                new = new or ""
            if current == new:
                continue
            vals[name] = new
        if vals and not dry_run:
            record.write(vals)
        return sorted(vals)

    @api.model
    def _resolve_models(self, model_names):
        """ir.model-records bij technische namen, plus wat hier ontbreekt."""
        wanted = [name for name in (model_names or []) if name]
        found = self.env["ir.model"].sudo().search([("model", "in", wanted)])
        missing = sorted(set(wanted) - set(found.mapped("model")))
        return found, missing

    @api.model
    def _apply_model_set(self, record, field_name, model_names, dry_run):
        found, missing = self._resolve_models(model_names)
        lines = []
        if set(found.ids) != set(record[field_name].ids):
            if not dry_run:
                record.write({field_name: [(6, 0, found.ids)]})
            lines.append(_(
                "%(field)s: %(count)s model(len)",
                field=field_name, count=len(found),
            ))
        for name in missing:
            lines.append(_manual(_(
                "model %(model)s bestaat hier niet en is dus niet in "
                "%(field)s gezet \u2014 installeer de app en pas de blueprint "
                "opnieuw toe",
                model=name, field=field_name,
            )))
        return lines

    @api.model
    def _apply_parameters(self, blueprint, dry_run):
        icp = self.env["ir.config_parameter"].sudo()
        lines = []
        for key, value in (blueprint.get("parameters") or {}).items():
            if not key.startswith(EXPORTED_PARAM_PREFIXES):
                lines.append(_("overgeslagen (niet toegestaan): %s", key))
                continue
            if _looks_secret(key):
                lines.append(_("overgeslagen (lijkt een geheim): %s", key))
                continue
            if icp.get_param(key) == value:
                continue
            if not dry_run:
                icp.set_param(key, value)
            lines.append(_("instelling %(key)s = %(value)s",
                           key=key, value=value))
        return lines

    @api.model
    def _apply_topics(self, blueprint, dry_run):
        """Onderwerpen bijwerken of aanmaken, tools alleen als ze bestaan.

        Een onderwerp is configuratie en mag hier ontstaan. De tools
        erachter zijn serveracties uit een module: die worden gelinkt op
        naam en nooit verzonnen. Ontbreekt er een, dan staat dat in het
        verslag \u2014 een onderwerp met halve tools doet minder dan het lijkt.
        """
        Topic = self.env["ai.topic"].sudo()
        Action = self.env["ir.actions.server"].sudo()
        value_fields = self._present_fields("ai.topic", TOPIC_VALUE_FIELDS)
        lines = []
        for spec in (blueprint.get("topics") or []):
            name = spec.get("name")
            if not name:
                continue
            topic = Topic.search([("name", "=", name)], limit=1)
            if not topic:
                if dry_run:
                    lines.append(_("onderwerp %s zou worden aangemaakt", name))
                    continue
                topic = Topic.create({"name": name})
                lines.append(_("onderwerp %s aangemaakt", name))
            changed = self._apply_values(topic, spec, value_fields, dry_run)
            if changed:
                lines.append(_("onderwerp %(name)s: %(changes)s",
                               name=name, changes=", ".join(changed)))
            tool_names = [tool for tool in (spec.get("tools") or []) if tool]
            if not tool_names:
                continue
            tools = Action.search([("name", "in", tool_names)])
            missing = sorted(set(tool_names) - set(tools.mapped("name")))
            if set(tools.ids) != set(topic.tool_ids.ids):
                if not dry_run:
                    topic.write({"tool_ids": [(6, 0, tools.ids)]})
                lines.append(_(
                    "onderwerp %(name)s: %(count)s tool(s) gekoppeld",
                    name=name, count=len(tools),
                ))
            for tool in missing:
                lines.append(_manual(_(
                    "tool %(tool)s bestaat hier niet en is niet aan "
                    "onderwerp %(name)s gekoppeld \u2014 die serveractie komt "
                    "uit een module, niet uit de blueprint",
                    tool=tool, name=name,
                )))
        return lines

    @api.model
    def _apply_read_scopes(self, agent, specs, dry_run):
        """Leesscope per model bijwerken; wat niet in de blueprint staat, gaat uit."""
        if "daadit_read_scope_ids" not in agent._fields:
            return []
        Scope = self.env["daadit.ai.agent.read.scope"].sudo().with_context(
            active_test=False,
        )
        lines = []
        seen = set()
        for spec in (specs or []):
            model_name = spec.get("model")
            if not model_name:
                continue
            model = self.env["ir.model"].sudo().search(
                [("model", "=", model_name)], limit=1,
            )
            if not model:
                lines.append(_manual(_(
                    "leesscope %(model)s van %(agent)s overgeslagen: dat "
                    "model bestaat hier niet",
                    model=model_name, agent=agent.name,
                )))
                continue
            seen.add(model_name)
            existing = Scope.search([
                ("agent_id", "=", agent.id), ("model_id", "=", model.id),
            ], limit=1)
            vals = {
                "domain": spec.get("domain") or "[]",
                "active": bool(spec.get("active", True)),
            }
            if existing:
                if all(existing[key] == value for key, value in vals.items()):
                    continue
                if not dry_run:
                    existing.write(vals)
                lines.append(_(
                    "leesscope %(agent)s/%(model)s bijgewerkt",
                    agent=agent.name, model=model_name,
                ))
                continue
            if not dry_run:
                Scope.create(dict(
                    vals, agent_id=agent.id, model_id=model.id,
                ))
            lines.append(_(
                "leesscope %(agent)s/%(model)s toegevoegd",
                agent=agent.name, model=model_name,
            ))
        extra = Scope.search([
            ("agent_id", "=", agent.id),
            ("model_name", "not in", sorted(seen)),
            ("active", "=", True),
        ])
        if extra:
            if not dry_run:
                extra.write({"active": False})
            lines.append(_(
                "leesscope van %(agent)s ingeperkt: %(models)s staat niet in "
                "de blueprint en is uitgezet",
                agent=agent.name,
                models=", ".join(sorted(extra.mapped("model_name"))),
            ))
        return lines

    @api.model
    def _apply_activity_scopes(self, agent, specs, dry_run):
        if "daadit_activity_scope_ids" not in agent._fields:
            return []
        Scope = self.env[
            "daadit.ai.agent.activity.scope"
        ].sudo().with_context(active_test=False)
        lines = []
        seen = set()
        for spec in (specs or []):
            model_name = spec.get("model")
            if not model_name:
                continue
            seen.add(model_name)
            vals = {
                "record_domain": spec.get("record_domain") or "[]",
                "active": bool(spec.get("active", True)),
            }
            existing = Scope.search([
                ("agent_id", "=", agent.id), ("model_name", "=", model_name),
            ], limit=1)
            if existing:
                if all(existing[key] == value for key, value in vals.items()):
                    continue
                if not dry_run:
                    existing.write(vals)
                lines.append(_(
                    "activiteitscope %(agent)s/%(model)s bijgewerkt",
                    agent=agent.name, model=model_name,
                ))
                continue
            if not dry_run:
                Scope.create(dict(
                    vals, agent_id=agent.id, model_name=model_name,
                ))
            lines.append(_(
                "activiteitscope %(agent)s/%(model)s toegevoegd",
                agent=agent.name, model=model_name,
            ))
        extra = Scope.search([
            ("agent_id", "=", agent.id),
            ("model_name", "not in", sorted(seen)),
            ("active", "=", True),
        ])
        if extra:
            if not dry_run:
                extra.write({"active": False})
            lines.append(_(
                "activiteitscope van %(agent)s ingeperkt: %(models)s staat "
                "niet in de blueprint en is uitgezet",
                agent=agent.name,
                models=", ".join(sorted(extra.mapped("model_name"))),
            ))
        return lines

    @api.model
    def _apply_repair_scopes(self, agent, specs, dry_run):
        """Reparatiebereik overnemen, maar nooit met de goedkeuring erbij.

        ``approved_by_id`` is de mens die het bereik heeft aangezet. Die
        goedkeuring geldt in die database en voor die persoon; hem
        meekopiëren zou een agent op een tweede omgeving bevoegdheid
        geven die niemand daar heeft gegeven. De regel komt er dus wel,
        maar staat op 'werkt niet' tot een mens hem aanzet.
        """
        if "daadit_repair_scope_ids" not in agent._fields:
            return []
        Agent = self.env["ai.agent"].sudo().with_context(active_test=False)
        Scope = self.env["daadit.ai.agent.repair.scope"].sudo().with_context(
            active_test=False,
        )
        lines = []
        for spec in (specs or []):
            target_name = spec.get("target_agent")
            if not target_name:
                continue
            target = Agent.search([("name", "=", target_name)], limit=1)
            if not target:
                lines.append(_manual(_(
                    "reparatiebereik %(agent)s \u2192 %(target)s overgeslagen: "
                    "die collega bestaat hier niet",
                    agent=agent.name, target=target_name,
                )))
                continue
            existing = Scope.search([
                ("agent_id", "=", agent.id),
                ("target_agent_id", "=", target.id),
            ], limit=1)
            if existing:
                continue
            if not dry_run:
                Scope.create({
                    "agent_id": agent.id,
                    "target_agent_id": target.id,
                    "article_ref": spec.get("article_ref") or 0,
                    "note": spec.get("note") or "",
                    "active": bool(spec.get("active", True)),
                })
            lines.append(_manual(_(
                "reparatiebereik %(agent)s \u2192 %(target)s toegevoegd, nog "
                "zonder goedkeuring: een mens moet het aanzetten",
                agent=agent.name, target=target_name,
            )))
        return lines

    @api.model
    def _ambiguous_names(self, Model, specs):
        """Namen die hier of in de blueprint meer dan eens voorkomen.

        Toepassen matcht op naam; bij een dubbele naam zou de waarde van
        het ene record op het andere terechtkomen. De waarde is ``True``
        tot de regel in het verslag staat.
        """
        names = [spec.get("name") for spec in specs if spec.get("name")]
        doubles = {name for name in names if names.count(name) > 1}
        if names:
            groups = Model._read_group(
                [("name", "in", list(set(names)))], ["name"], ["__count"],
            )
            doubles |= {name for name, count in groups if count > 1}
        return dict.fromkeys(doubles, True)

    @api.model
    def _apply_agents(self, blueprint, dry_run):
        Agent = self.env["ai.agent"].sudo().with_context(active_test=False)
        value_fields = tuple(
            name
            for name in self._present_fields("ai.agent", AGENT_VALUE_FIELDS)
            if name not in AGENT_READONLY_FIELDS
        )
        Topic = self.env["ai.topic"].sudo()
        lines = []
        specs = blueprint.get("agents") or []
        ambiguous = self._ambiguous_names(Agent, specs)
        for spec in specs:
            if spec.get("name") in ambiguous:
                if ambiguous[spec.get("name")]:
                    ambiguous[spec.get("name")] = False
                    lines.append(_manual(_(
                        "collega %s overgeslagen: die naam komt meer dan "
                        "één keer voor, dus is niet te zeggen welke bedoeld "
                        "is", spec.get("name"),
                    )))
                continue
            agent = Agent.search([("name", "=", spec.get("name"))], limit=1)
            if not agent:
                lines.append(_manual(_(
                    "collega %s bestaat hier niet \u2014 niet aangemaakt, want "
                    "zonder zijn tools zou hij alleen lijken te werken",
                    spec.get("name"),
                )))
                continue
            changed = self._apply_values(agent, spec, value_fields, dry_run)
            if changed:
                lines.append(_("collega %(name)s: %(changes)s",
                               name=agent.name, changes=", ".join(changed)))
            topic_names = [name for name in (spec.get("topics") or []) if name]
            if topic_names:
                topics = Topic.search([("name", "in", topic_names)])
                missing = sorted(set(topic_names) - set(topics.mapped("name")))
                if set(topics.ids) != set(agent.topic_ids.ids):
                    if not dry_run:
                        agent.write({"topic_ids": [(6, 0, topics.ids)]})
                    lines.append(_(
                        "collega %(name)s: %(count)s onderwerp(en) gekoppeld",
                        name=agent.name, count=len(topics),
                    ))
                for name in missing:
                    lines.append(_manual(_(
                        "onderwerp %(topic)s ontbreekt en is niet aan "
                        "%(name)s gekoppeld",
                        topic=name, name=agent.name,
                    )))
            if "daadit_allowed_model_ids" in agent._fields:
                if "read_scope" in spec:
                    lines += self._apply_model_set(
                        agent, "daadit_allowed_model_ids",
                        spec.get("read_scope"), dry_run,
                    )
                if "blocked_models" in spec:
                    lines += self._apply_model_set(
                        agent, "daadit_blocked_model_ids",
                        spec.get("blocked_models"), dry_run,
                    )
            if "read_scopes" in spec:
                lines += self._apply_read_scopes(
                    agent, spec.get("read_scopes"), dry_run,
                )
            if "activity_scopes" in spec:
                lines += self._apply_activity_scopes(
                    agent, spec.get("activity_scopes"), dry_run,
                )
            if "repair_scopes" in spec:
                lines += self._apply_repair_scopes(
                    agent, spec.get("repair_scopes"), dry_run,
                )
        return lines

    @api.model
    def _apply_schedules(self, blueprint, dry_run):
        """Planningen bijwerken of aanmaken \u2014 altijd uitgeschakeld.

        Een planning die door een import meteen begint te draaien is een
        agent die niemand heeft aangezet, met kosten en schrijfacties op
        een omgeving die nog niet is nagelopen. De blueprint zet de
        planning dus klaar; een mens zet hem aan.
        """
        if "daadit.ai.agent.schedule" not in self.env:
            return []
        Schedule = self.env["daadit.ai.agent.schedule"].sudo().with_context(
            active_test=False,
        )
        Agent = self.env["ai.agent"].sudo().with_context(active_test=False)
        value_fields = self._present_fields(
            "daadit.ai.agent.schedule", SCHEDULE_VALUE_FIELDS,
        )
        lines = []
        specs = blueprint.get("schedules") or []
        ambiguous = self._ambiguous_names(Schedule, specs)
        for spec in specs:
            name = spec.get("name")
            if not name:
                continue
            if name in ambiguous:
                if ambiguous[name]:
                    ambiguous[name] = False
                    lines.append(_manual(_(
                        "planning %s overgeslagen: die naam komt meer dan "
                        "één keer voor, dus is niet te zeggen welke bedoeld "
                        "is", name,
                    )))
                continue
            schedule = Schedule.search([("name", "=", name)], limit=1)
            if not schedule:
                agent = Agent.search(
                    [("name", "=", spec.get("agent"))], limit=1,
                )
                if not agent:
                    lines.append(_manual(_(
                        "planning %(name)s overgeslagen: collega %(agent)s "
                        "bestaat hier niet",
                        name=name, agent=spec.get("agent"),
                    )))
                    continue
                vals = {"name": name, "agent_id": agent.id, "active": False}
                for field_name in value_fields:
                    if field_name in spec:
                        vals[field_name] = spec[field_name]
                if not dry_run:
                    Schedule.create(vals)
                lines.append(_manual(_(
                    "planning %s aangemaakt, uitgeschakeld \u2014 een mens zet "
                    "hem aan", name,
                )))
                continue
            changed = self._apply_values(
                schedule, spec, value_fields, dry_run,
            )
            if changed:
                lines.append(_("planning %(name)s: %(changes)s",
                               name=name, changes=", ".join(changed)))
        return lines

    @api.model
    def _apply_tenants(self, blueprint, dry_run):
        """Grenzen van een klantomgeving overnemen op een bestaande tenant.

        Aanmaken kan hier niet: een tenant heeft een endpoint, een
        database, een gebruiker en een sleutel, en die horen bij de
        omgeving en niet bij de blueprint. Ontbreekt de tenant, dan zegt
        het verslag dat \u2014 en na het aanmaken past een tweede keer
        toepassen dezelfde grenzen toe.
        """
        specs = blueprint.get("tenants") or []
        if not specs:
            return []
        if "mcp.instance" not in self.env:
            return [_manual(_(
                "klantomgevingen overgeslagen: de MCP-module is hier niet "
                "geïnstalleerd"
            ))]
        Instance = self.env["mcp.instance"].sudo().with_context(
            active_test=False,
        )
        Capability = self.env["mcp.capability"].sudo()
        value_fields = self._present_fields("mcp.instance", TENANT_VALUE_FIELDS)
        lines = []
        for spec in specs:
            slug = spec.get("slug")
            if not slug:
                continue
            instance = Instance.search([("slug", "=", slug)], limit=1)
            if not instance:
                lines.append(_manual(_(
                    "klantomgeving %s bestaat hier niet \u2014 niet aangemaakt, "
                    "want endpoint, database en sleutel horen bij de "
                    "omgeving; maak de tenant aan en pas de blueprint "
                    "daarna opnieuw toe", slug,
                )))
                continue
            changed = self._apply_values(
                instance, spec, value_fields, dry_run,
            )
            if changed:
                lines.append(_("klantomgeving %(slug)s: %(changes)s",
                               slug=slug, changes=", ".join(changed)))
            for key, field_name in (
                ("capabilities", "capability_ids"),
                ("restricted_capabilities", "restricted_capability_ids"),
            ):
                if key not in spec:
                    continue
                codes = [code for code in (spec.get(key) or []) if code]
                caps = Capability.search([("code", "in", codes)])
                missing = sorted(set(codes) - set(caps.mapped("code")))
                if set(caps.ids) != set(instance[field_name].ids):
                    if not dry_run:
                        instance.write({field_name: [(6, 0, caps.ids)]})
                    lines.append(_(
                        "klantomgeving %(slug)s: %(count)s capability(s) in "
                        "%(field)s",
                        slug=slug, count=len(caps), field=field_name,
                    ))
                for code in missing:
                    lines.append(_manual(_(
                        "capability %(code)s bestaat hier niet en is niet "
                        "aangezet op %(slug)s",
                        code=code, slug=slug,
                    )))
            for key, field_name in (
                ("allowed_models", "allowed_model_ids"),
                ("blocked_models", "blocked_model_ids"),
            ):
                if key in spec:
                    lines += self._apply_model_set(
                        instance, field_name, spec.get(key), dry_run,
                    )
        return lines

    @api.model
    def apply_blueprint(self, blueprint, dry_run=False):
        """Apply a blueprint to this database, matching records by name.

        Returns a list of human-readable lines describing what changed
        (or would change, with ``dry_run``). Unknown colleagues and
        tenants are reported, never invented: a colleague without his
        server actions, or a tenant without its endpoint, would look
        installed and quietly do nothing.
        """
        if not isinstance(blueprint, dict):
            raise UserError(_("De blueprint is geen geldig JSON-object."))
        version = blueprint.get("blueprint_version")
        if version not in SUPPORTED_VERSIONS:
            raise UserError(_(
                "Deze blueprint heeft versie %(found)s, deze module leest "
                "versie %(expected)s.",
                found=version,
                expected=", ".join(str(v) for v in SUPPORTED_VERSIONS),
            ))

        lines = self._apply_parameters(blueprint, dry_run)
        lines += self._apply_topics(blueprint, dry_run)
        lines += self._apply_agents(blueprint, dry_run)
        lines += self._apply_schedules(blueprint, dry_run)
        lines += self._apply_tenants(blueprint, dry_run)
        return lines

    # ------------------------------------------------------------------
    # Rapport: wat er staat, en wat een import per definitie niet kan
    # ------------------------------------------------------------------
    @api.model
    def _environment_findings(self, blueprint):
        """Wat er in déze database ontbreekt voordat de vestiging werkt.

        Een pakket draagt de inrichting, niet de omgeving. Een collega
        zonder providersleutel en een klantomgeving zonder endpoint zien
        er ingericht uit en doen niets \u2014 precies de stille fout die dit
        rapport moet voorkomen. Alleen de aanwezigheid van een sleutel
        wordt gelezen, nooit zijn waarde: een rapport dat een sleutel
        citeert is zelf een lek.
        """
        icp = self.env["ir.config_parameter"].sudo()
        lines = []
        wanted = {}
        for spec in (blueprint.get("agents") or []):
            llm = (spec.get("llm_model") or "").lower()
            for needle, param in PROVIDER_KEY_PARAMS:
                if needle in llm:
                    wanted.setdefault(param, set()).add(spec.get("name") or "")
        for param in sorted(wanted):
            if icp.get_param(param):
                continue
            lines.append(_(
                "de providersleutel %(param)s is hier niet gezet; zolang "
                "dat zo is doet %(agents)s niets",
                param=param,
                agents=", ".join(sorted(n for n in wanted[param] if n)),
            ))
        if "mcp.instance" in self.env:
            Instance = self.env["mcp.instance"].sudo().with_context(
                active_test=False,
            )
            for spec in (blueprint.get("tenants") or []):
                slug = spec.get("slug")
                if not slug:
                    continue
                instance = Instance.search([("slug", "=", slug)], limit=1)
                if not instance:
                    continue  # al gemeld door het toepassen zelf
                gaps = [
                    label
                    for field_name, label in (
                        ("url", _("endpoint-url")),
                        ("db", _("database")),
                        ("username", _("gebruiker")),
                        ("api_key_set", _("API-sleutel")),
                    )
                    if field_name in instance._fields
                    and not instance[field_name]
                ]
                if gaps:
                    lines.append(_(
                        "klantomgeving %(slug)s mist %(gaps)s; die horen bij "
                        "de klant-Odoo en moeten daar door een mens gezet "
                        "worden",
                        slug=slug, gaps=", ".join(gaps),
                    ))
        return lines

    @api.model
    def apply_report(self, blueprint, dry_run=False):
        """Toepassen én één leesbare uitkomst die als checklist dient.

        ``apply_blueprint`` geeft losse regels; wie een vestiging inricht
        wil twee dingen gescheiden zien: wat er nu staat, en wat hij zelf
        nog moet zetten voordat de vestiging iets doet.
        """
        lines = self.apply_blueprint(blueprint, dry_run=dry_run)
        changed = [
            line for line in lines if not line.startswith(MANUAL_MARK)
        ]
        manual = [
            line[len(MANUAL_MARK):]
            for line in lines if line.startswith(MANUAL_MARK)
        ]
        manual += self._environment_findings(blueprint)
        return {
            "changed": changed,
            "manual": manual,
            "text": self._format_report(changed, manual, dry_run),
        }

    @api.model
    def _format_report(self, changed, manual, dry_run):
        parts = [
            _("Proef \u2014 dit zou er gebeuren:") if dry_run
            else _("Toegepast:")
        ]
        parts += ["- %s" % line for line in changed] or [
            _("- niets te wijzigen")
        ]
        parts.append("")
        if manual:
            parts.append(_("Dit moet een mens nog zetten:"))
            parts += ["[ ] %s" % line for line in manual]
        else:
            parts.append(_(
                "Geen openstaande punten uit dit pakket: alles wat erin "
                "staat, staat hier."
            ))
        parts.append("")
        parts.append(_(
            "Wat een pakket per definitie niet meeneemt: het endpoint, de "
            "database en de sleutel van de klant-Odoo, de providersleutels, "
            "en de tools die als serveractie in modulecode staan. Zolang "
            "die ontbreken doet de betrokken collega niets \u2014 en geen "
            "planning staat aan, ook niet na een terugrol: die zet een "
            "mens aan."
        ))
        return "\n".join(parts)

    def action_apply(self):
        self.ensure_one()
        try:
            blueprint = json.loads(self.payload or "{}")
        except ValueError as exc:
            raise UserError(_("De blueprint is geen geldige JSON: %s", exc))
        self.summary = self.apply_report(blueprint)["text"]
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_dry_run(self):
        """Proef: wat zou deze blueprint hier veranderen?"""
        self.ensure_one()
        try:
            blueprint = json.loads(self.payload or "{}")
        except ValueError as exc:
            raise UserError(_("De blueprint is geen geldige JSON: %s", exc))
        self.summary = self.apply_report(blueprint, dry_run=True)["text"]
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
