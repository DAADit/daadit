# -*- coding: utf-8 -*-
"""Periodieke rechtenreview van de agents die mogen uitvoeren (taak 708).

Vijf collega's in het OAS hebben het rechtenniveau *uitvoeren*: ze
publiceren, versturen of wijzigen records. Die niveaus zijn per agent
ooit bewust gezet, maar hun scope groeide daarna mee met hun werk en
niemand herijkte dat. "We kijken er af en toe naar" is geen controle.

Bij Pim werd op 1-8 een foutieve aanroep door de scope-guard geweigerd:
er is niets geschreven. Het rechtenniveau was dus niet het probleem, de
rapportage erover wel. Deze review stelt daarom twee vragen per agent:

1. **Mag hij dit?** — rechtenniveau, schrijvende en publicerende tools,
   leesscope, PII-blocklist, en wat er sinds de vorige review in die
   configuratie veranderde.
2. **Klopt wat hij erover meldt?** — runs, runs met een onverifieerbare
   bewering, runs die aandacht vragen, echte schrijfacties en door de
   guard geweigerde pogingen.

Het rapport wordt door code samengesteld uit de werkelijke
configuratie; er staat geen handmatige lijst met agentnamen in dit
bestand. Vergelijken gebeurt met de bestaande
configuratie-momentopnamen (``daadit.config.snapshot`` uit
``daadit_tenant_blueprint``) — een tweede snapshotmechanisme zou een
tweede waarheid opleveren.

Deze module maakt zichtbaar; ze besluit niets. Een suggestie om in te
perken staat in de regel van de agent, en de reviewer legt zijn uitkomst
vast op die regel.
"""
import json
import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _

from .ai_agent_schedule import (
    _READONLY_CLASSIFY_ONLY,
    _READONLY_CLASSIFY_PARAM,
    _READONLY_TOOL_NAMES,
    _action_tool_name,
)

_logger = logging.getLogger(__name__)

# De login van de verantwoordelijke reviewer. Bewust een instelling en
# geen id in code: ids uit de ene database betekenen niets in de andere,
# en de verantwoordelijke wisselt zonder deploy.
RESPONSIBLE_LOGIN_PARAM = "daadit_ai_agent_schedule.permission_review_login"
# Hoeveel dagen de reviewer krijgt voordat de To-Do te laat staat.
DEADLINE_DAYS_PARAM = (
    "daadit_ai_agent_schedule.permission_review_deadline_days"
)
DEFAULT_DEADLINE_DAYS = 14
# De cadans van de review. Kwartaal is de gekozen frequentie; de
# periode waarover de betrouwbaarheid wordt geteld loopt gelijk op.
REVIEW_PERIOD_MONTHS = 3

# Werkwoorden in een toolnaam die zeggen dat er naar buiten wordt
# gepubliceerd of verstuurd. Een schrijfactie binnen Odoo is terug te
# draaien; een verstuurde mailing of een gepubliceerde post niet, en dat
# verschil hoort in het rapport te staan.
_OUTBOUND_VERBS = (
    "publish", "publiceer", "post", "send", "verstuur", "mailing",
    "social", "linkedin", "blog", "tweet", "newsletter", "nieuwsbrief",
)
# Werkwoorden voor onomkeerbaar weghalen. Een agent zonder tool met een
# van deze werkwoorden kan die actie niet uitvoeren — dat is geen
# belofte van de prompt maar een gevolg van zijn toolset.
_DESTRUCTIVE_VERBS = (
    "delete", "unlink", "remove", "verwijder", "archive", "archiveer",
    "unpublish", "depubliceer", "close", "afsluit", "sluit",
)
# Rechtenniveaus die schrijven of uitvoeren toestaan. Alleen gebruikt om
# een agent mee te nemen wanneer het niveau dat zegt maar zijn toolset
# (nog) niets schrijvends bevat; de omgekeerde weg — een schrijftool
# zonder niveau — neemt hem óók mee.
_WRITING_LEVEL_WORDS = ("uitvoer", "schrijf", "wijzig", "publice", "write")


def _contains(name, verbs):
    """True wanneer een van ``verbs`` in ``name`` voorkomt."""
    lowered = (name or "").lower()
    return any(verb in lowered for verb in verbs)


class AiPermissionReview(models.Model):
    """Eén rechtenreview: één rapport over één periode."""

    _name = "daadit.ai.permission.review"
    _description = "Rechtenreview van de uitvoerende agents"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "period_end desc, id desc"

    name = fields.Char(required=True, readonly=True)
    period_start = fields.Datetime(
        string="Periode vanaf", required=True, readonly=True,
        help="Begin van de periode waarover de betrouwbaarheid van de "
             "rapportage is geteld.",
    )
    period_end = fields.Datetime(
        string="Periode tot", required=True, readonly=True,
    )
    trigger = fields.Selection(
        [("cron", "Kwartaalcadans"), ("manual", "Handmatig")],
        default="cron", required=True, readonly=True,
    )
    responsible_user_id = fields.Many2one(
        "res.users", string="Verantwoordelijke", readonly=True,
        help="De gebruiker die de To-Do voor deze review kreeg.",
    )
    line_ids = fields.One2many(
        "daadit.ai.permission.review.line", "review_id",
        string="Agents", readonly=True,
    )
    agent_count = fields.Integer(
        string="Aantal agents", compute="_compute_counts", store=True,
    )
    changed_count = fields.Integer(
        string="Gewijzigd sinds vorige review",
        compute="_compute_counts", store=True,
    )
    unreliable_count = fields.Integer(
        string="Met onverifieerbare beweringen",
        compute="_compute_counts", store=True,
    )
    open_count = fields.Integer(
        string="Nog te beoordelen", compute="_compute_counts", store=True,
    )
    summary = fields.Text(string="Samenvatting", readonly=True)
    baseline_snapshot_name = fields.Char(
        string="Vergeleken met", readonly=True,
        help="De configuratie-momentopname die als vorige stand is "
             "gebruikt.",
    )
    baseline_snapshot_date = fields.Datetime(
        string="Stand van", readonly=True,
    )

    @api.depends("line_ids", "line_ids.config_changed",
                 "line_ids.claims_unverified_count", "line_ids.outcome")
    def _compute_counts(self):
        for rec in self:
            lines = rec.line_ids
            rec.agent_count = len(lines)
            rec.changed_count = len(lines.filtered("config_changed"))
            rec.unreliable_count = len(
                lines.filtered(lambda line: line.claims_unverified_count)
            )
            rec.open_count = len(
                lines.filtered(lambda line: line.outcome == "open")
            )

    # ------------------------------------------------------------------
    # Samenstellen van het rapport
    # ------------------------------------------------------------------
    @api.model
    def _readonly_tool_slugs(self):
        """De toolnamen die alleen lezen, uit dezelfde bron als de runlog.

        Zo kan een tool niet in het ene scherm als leesactie en in het
        andere als schrijfactie gelden.
        """
        slugs = set(_READONLY_TOOL_NAMES) | set(_READONLY_CLASSIFY_ONLY)
        param = self.env["ir.config_parameter"].sudo().get_param(
            _READONLY_CLASSIFY_PARAM, "",
        )
        for piece in (param or "").split(","):
            piece = piece.strip()
            if piece:
                slugs.add(piece)
        return slugs

    @api.model
    def _agent_rights_level(self, agent):
        """Het rechtenniveau van een agent, of een lege string.

        ``x_rechten`` is een Studio-veld: het bestaat in de
        DAADit-database maar niet per definitie in een verse tenant.
        """
        for field in ("x_rechten", "x_rechtenniveau"):
            if field in agent._fields:
                return (agent[field] or "").strip()
        return ""

    @api.model
    def _agent_role(self, agent):
        if "x_rol" in agent._fields:
            return (agent["x_rol"] or "").strip()
        return agent.subtitle or ""

    @api.model
    def _split_tools(self, agent):
        """De tools van deze agent, verdeeld naar wat ze kunnen.

        Returns ``(schrijvend, publicerend, weghalend, alles)`` met
        toolnamen zoals ze in de runlog verschijnen.
        """
        readonly = self._readonly_tool_slugs()
        writing, outbound, destructive, every = [], [], [], []
        for action in agent.sudo().topic_ids.tool_ids:
            label = action.name or ""
            slug = _action_tool_name(action)
            every.append(label)
            if slug and slug in readonly:
                continue
            writing.append(label)
            if _contains(label, _OUTBOUND_VERBS):
                outbound.append(label)
            if _contains(label, _DESTRUCTIVE_VERBS):
                destructive.append(label)
        return (
            sorted(set(writing)), sorted(set(outbound)),
            sorted(set(destructive)), sorted(set(every)),
        )

    @api.model
    def _agent_scope(self, agent):
        """Leesscope en PII-blocklist van deze agent.

        De velden komen van de provider-module (``daadit_ai_mistral``);
        een tenant zonder die module heeft ze niet, en dan zegt het
        rapport dat in plaats van een lege scope te suggereren.
        """
        allowed, blocked, blocklist = [], [], ""
        known = True
        if "daadit_allowed_model_ids" in agent._fields:
            allowed = sorted(agent.daadit_allowed_model_ids.mapped("model"))
            blocked = sorted(agent.daadit_blocked_model_ids.mapped("model"))
        else:
            known = False
        if "daadit_field_blocklist" in agent._fields:
            blocklist = (agent.daadit_field_blocklist or "").strip()
        return {
            "known": known,
            "allowed": allowed,
            "blocked": blocked,
            "blocklist": blocklist,
        }

    @api.model
    def _writing_agents(self):
        """De agents met schrijf- of uitvoerrechten, uit de configuratie.

        Een agent komt hier in wanneer hij minstens één tool heeft die
        niet in de leeslijst staat, of wanneer zijn rechtenniveau
        schrijven/uitvoeren zegt. Wie alleen zoekt en leest hoort niet in
        een rechtenreview: dan gaat de review over de verkeerde vragen.
        """
        Agent = self.env["ai.agent"].sudo().with_context(active_test=False)
        out = []
        for agent in Agent.search([]):
            writing, outbound, destructive, every = self._split_tools(agent)
            level = self._agent_rights_level(agent)
            if not writing and not _contains(level, _WRITING_LEVEL_WORDS):
                continue
            out.append({
                "agent": agent,
                "level": level,
                "writing": writing,
                "outbound": outbound,
                "destructive": destructive,
                "tools": every,
            })
        return out

    # ------------------------------------------------------------------
    # Vergelijken met de vorige stand
    # ------------------------------------------------------------------
    @api.model
    def _baseline_snapshot(self, previous_review):
        """De momentopname die de stand bij de vorige review beschrijft.

        Zonder vorige review is dat de oudste bewaarde opname: dan is de
        review de eerste en meldt hij wat er sinds het begin van de reeks
        veranderde. Ontbreekt de snapshotmodule, dan is er niets om mee
        te vergelijken en zegt het rapport dat.
        """
        if "daadit.config.snapshot" not in self.env:
            return None
        Snapshot = self.env["daadit.config.snapshot"].sudo()
        if previous_review:
            snapshot = Snapshot.search(
                [("create_date", "<=", previous_review.create_date)],
                order="create_date desc", limit=1,
            )
            if snapshot:
                return snapshot
        return Snapshot.search([], order="create_date asc", limit=1)

    @api.model
    def _snapshot_config(self, snapshot):
        """De configuratie uit een opname, per agentnaam.

        Returns een dict ``{agentnaam: spec}`` waarin ``spec`` de tools
        van zijn onderwerpen al bevat, zodat een tool die uit een topic
        is gehaald zichtbaar wordt zonder dat de lezer twee lijsten moet
        combineren.
        """
        if not snapshot:
            return {}
        try:
            payload = json.loads(snapshot.payload or "{}")
        except ValueError:
            _logger.warning(
                "rechtenreview: opname %s is onleesbaar", snapshot.name,
            )
            return {}
        tools_by_topic = {
            topic.get("name"): sorted(topic.get("tools") or [])
            for topic in (payload.get("topics") or [])
        }
        out = {}
        for spec in (payload.get("agents") or []):
            topics = list(spec.get("topics") or [])
            tools = []
            for topic in topics:
                tools.extend(tools_by_topic.get(topic) or [])
            enriched = dict(spec)
            enriched["_tools"] = sorted(set(tools))
            out[spec.get("name")] = enriched
        return out

    @api.model
    def _config_changes(self, current, before):
        """Leesbare regels over wat er in de configuratie veranderde.

        ``before`` is de agentspec uit de opname; ``None`` betekent dat
        er geen vergelijkbare stand is.
        """
        if before is None:
            return []
        lines = []
        level_before = (
            before.get("x_rechten") or before.get("x_rechtenniveau") or ""
        ).strip()
        if level_before != current["level"]:
            lines.append(_(
                "rechtenniveau: %(before)s \u2192 %(after)s",
                before=level_before or _("(leeg)"),
                after=current["level"] or _("(leeg)"),
            ))
        for label, key, now_value in (
            (_("tools"), "_tools", current["tools"]),
            (_("leesscope"), "read_scope", current["scope"]["allowed"]),
            (_("geblokkeerde modellen"), "blocked_models",
             current["scope"]["blocked"]),
        ):
            if key not in before:
                continue
            was = sorted(before.get(key) or [])
            added = sorted(set(now_value) - set(was))
            gone = sorted(set(was) - set(now_value))
            if added:
                lines.append(_(
                    "%(label)s erbij: %(items)s",
                    label=label, items=", ".join(added),
                ))
            if gone:
                lines.append(_(
                    "%(label)s eraf: %(items)s",
                    label=label, items=", ".join(gone),
                ))
        if "field_blocklist" in before:
            was = (before.get("field_blocklist") or "").strip()
            if was != current["scope"]["blocklist"]:
                lines.append(_(
                    "PII-blocklist: %(before)s \u2192 %(after)s",
                    before=was or _("(leeg)"),
                    after=current["scope"]["blocklist"] or _("(leeg)"),
                ))
        topics_before = sorted(before.get("topics") or [])
        topics_now = sorted(current["agent"].sudo().topic_ids.mapped("name"))
        if topics_before != topics_now:
            lines.append(_(
                "onderwerpen: %(before)s \u2192 %(after)s",
                before=", ".join(topics_before) or _("(geen)"),
                after=", ".join(topics_now) or _("(geen)"),
            ))
        return lines

    # ------------------------------------------------------------------
    # Betrouwbaarheid van de rapportage
    # ------------------------------------------------------------------
    @api.model
    def _reporting_facts(self, agent, period_start, period_end):
        """Wat de runlog over deze agent zegt in de reviewperiode."""
        Run = self.env["daadit.ai.agent.schedule.run"].sudo()
        Action = self.env["daadit.ai.agent.schedule.run.action"].sudo()
        window = [
            ("agent_id", "=", agent.id),
            ("start_date", ">=", period_start),
            ("start_date", "<=", period_end),
        ]
        action_window = [
            ("run_id.agent_id", "=", agent.id),
            ("run_id.start_date", ">=", period_start),
            ("run_id.start_date", "<=", period_end),
        ]
        return {
            "runs": Run.search_count(window),
            "claims": Run.search_count(
                window + [("claims_unverified", "=", True)]
            ),
            "attention": Run.search_count(
                window + [("needs_attention", "=", True)]
            ),
            "writes": Action.search_count(
                action_window + [("is_effective_write", "=", True)]
            ),
            "refused": Action.search_count(
                action_window
                + [("is_write", "=", True),
                   ("is_effective_write", "=", False)]
            ),
        }

    # ------------------------------------------------------------------
    @api.model
    def build(self, trigger="cron"):
        """Stel een nieuwe rechtenreview samen en geef hem terug."""
        period_end = fields.Datetime.now()
        period_start = period_end - relativedelta(
            months=REVIEW_PERIOD_MONTHS,
        )
        previous = self.sudo().search([], order="id desc", limit=1)
        snapshot = self._baseline_snapshot(previous)
        before_by_name = self._snapshot_config(snapshot)

        review = self.sudo().create({
            "name": _("Rechtenreview %s") % fields.Date.to_string(
                fields.Date.context_today(self),
            ),
            "period_start": period_start,
            "period_end": period_end,
            "trigger": trigger,
            "baseline_snapshot_name": snapshot.name if snapshot else False,
            "baseline_snapshot_date": (
                snapshot.create_date if snapshot else False
            ),
        })

        Line = self.env["daadit.ai.permission.review.line"].sudo()
        for entry in self._writing_agents():
            agent = entry["agent"]
            entry["scope"] = self._agent_scope(agent)
            before = before_by_name.get(agent.name)
            changes = self._config_changes(entry, before)
            facts = self._reporting_facts(agent, period_start, period_end)
            Line.create({
                "review_id": review.id,
                "agent_id": agent.id,
                "agent_name": agent.name,
                "rights_level": entry["level"],
                "role": self._agent_role(agent),
                "write_tools": "\n".join(entry["writing"]),
                "outbound_tools": "\n".join(entry["outbound"]),
                "destructive_tools": "\n".join(entry["destructive"]),
                "scope_models": "\n".join(entry["scope"]["allowed"]),
                "blocked_models": "\n".join(entry["scope"]["blocked"]),
                "scope_unrestricted": bool(
                    entry["scope"]["known"] and not entry["scope"]["allowed"]
                ),
                "scope_unknown": not entry["scope"]["known"],
                "pii_blocklist": entry["scope"]["blocklist"],
                "hard_blocked": self._hard_blocked_text(entry),
                "config_changed": bool(changes),
                "config_change_detail": (
                    "\n".join(changes) if changes
                    else self._no_change_text(before)
                ),
                "run_count": facts["runs"],
                "claims_unverified_count": facts["claims"],
                "needs_attention_count": facts["attention"],
                "effective_write_count": facts["writes"],
                "refused_write_count": facts["refused"],
            })
        review.summary = review._compose_summary()
        _logger.info(
            "rechtenreview: %s samengesteld over %s agent(s)",
            review.name, len(review.line_ids),
        )
        return review

    @api.model
    def _no_change_text(self, before):
        if before is None:
            return _(
                "Geen eerdere momentopname van deze agent om mee te "
                "vergelijken."
            )
        return _("Niets gewijzigd in de vastgelegde configuratie.")

    @api.model
    def _hard_blocked_text(self, entry):
        """Wat deze agent niet kan, en waarom niet.

        Alleen wat uit de configuratie volgt: een actie waarvoor geen
        tool bestaat kan de agent niet uitvoeren, en een model buiten
        zijn leesscope wordt door de guard geweigerd. Wat de prompt
        belooft staat hier niet in — een instructie is geen controle.
        """
        lines = []
        if not entry["destructive"]:
            lines.append(_(
                "verwijderen, archiveren en depubliceren: geen tool "
                "beschikbaar"
            ))
        else:
            lines.append(
                _("let op: heeft tools die weghalen \u2014 %s")
                % ", ".join(entry["destructive"])
            )
        scope = entry["scope"]
        if scope["blocked"]:
            lines.append(_(
                "modellen op de blokkeerlijst: %s",
            ) % ", ".join(scope["blocked"]))
        if scope["known"] and scope["allowed"]:
            lines.append(_(
                "alles buiten de leesscope wordt door de guard geweigerd"
            ))
        if scope["blocklist"]:
            lines.append(_(
                "velden op de PII-blocklist: %s",
            ) % scope["blocklist"])
        return "\n".join(lines)

    def _compose_summary(self):
        """De samenvatting, geteld en niet geschreven."""
        self.ensure_one()
        lines = [_(
            "%(agents)s agent(s) met schrijf- of uitvoerrechten over de "
            "periode %(start)s t/m %(end)s.",
            agents=len(self.line_ids),
            start=fields.Datetime.to_string(self.period_start),
            end=fields.Datetime.to_string(self.period_end),
        )]
        if self.baseline_snapshot_name:
            lines.append(_(
                "Vergeleken met momentopname %s.",
                self.baseline_snapshot_name,
            ))
        else:
            lines.append(_(
                "Er is geen configuratie-momentopname beschikbaar; "
                "wijzigingen sinds de vorige review zijn daarom niet "
                "vast te stellen."
            ))
        changed = self.line_ids.filtered("config_changed")
        if changed:
            lines.append(_(
                "Configuratie gewijzigd bij: %s.",
                ", ".join(changed.mapped("agent_name")),
            ))
        claims = self.line_ids.filtered(
            lambda line: line.claims_unverified_count
        )
        if claims:
            lines.append(_(
                "Onverifieerbare beweringen bij: %s.",
                ", ".join(claims.mapped("agent_name")),
            ))
        outbound = self.line_ids.filtered("outbound_tools")
        if outbound:
            lines.append(_(
                "Publiceert of verstuurt naar buiten: %s.",
                ", ".join(outbound.mapped("agent_name")),
            ))
        lines.append(_(
            "Dit rapport wijzigt geen enkel recht; elke inperking is een "
            "besluit van de reviewer."
        ))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Cadans
    # ------------------------------------------------------------------
    @api.model
    def _responsible_user(self):
        """De gebruiker die de review moet doen, opgezocht op login.

        Geen id in code. Staat de instelling niet, dan valt dit terug op
        de gebruiker onder wiens rechten de meeste planningen draaien —
        die kent de agents het best — en anders op de beheerder. Elke
        terugval wordt gelogd, zodat een verkeerd geadresseerde To-Do
        terug te vinden is.
        """
        Users = self.env["res.users"].sudo()
        login = (self.env["ir.config_parameter"].sudo().get_param(
            RESPONSIBLE_LOGIN_PARAM, "",
        ) or "").strip()
        if login:
            user = Users.search([
                "|", ("login", "=ilike", login), ("email", "=ilike", login),
            ], limit=1)
            if user:
                return user
            _logger.warning(
                "rechtenreview: geen gebruiker met login %s (%s)",
                login, RESPONSIBLE_LOGIN_PARAM,
            )
        schedules = self.env["daadit.ai.agent.schedule"].sudo().search([])
        if schedules:
            counts = {}
            for schedule in schedules:
                if schedule.user_id:
                    counts[schedule.user_id] = counts.get(
                        schedule.user_id, 0,
                    ) + 1
            if counts:
                user = max(counts, key=lambda key: counts[key])
                _logger.info(
                    "rechtenreview: geen %s ingesteld, To-Do naar %s "
                    "(eigenaar van de meeste planningen)",
                    RESPONSIBLE_LOGIN_PARAM, user.login,
                )
                return user
        fallback = self.env.ref("base.user_admin", raise_if_not_found=False)
        _logger.warning(
            "rechtenreview: terugval op de beheerder voor de To-Do; zet "
            "%s op de login van de verantwoordelijke",
            RESPONSIBLE_LOGIN_PARAM,
        )
        return fallback or self.env.user

    @api.model
    def _deadline_days(self):
        icp = self.env["ir.config_parameter"].sudo()
        try:
            days = int(icp.get_param(
                DEADLINE_DAYS_PARAM, DEFAULT_DEADLINE_DAYS,
            ) or DEFAULT_DEADLINE_DAYS)
        except (TypeError, ValueError):
            days = DEFAULT_DEADLINE_DAYS
        return max(1, days)

    def _schedule_todo(self):
        """Zet deze review als To-Do met deadline bij de reviewer."""
        self.ensure_one()
        user = self._responsible_user()
        if not user:
            _logger.warning(
                "rechtenreview: geen gebruiker gevonden; %s staat zonder "
                "To-Do", self.name,
            )
            return self.env["mail.activity"]
        deadline = fields.Date.context_today(self) + relativedelta(
            days=self._deadline_days(),
        )
        note = (self.summary or "").replace("\n", "<br/>")
        activity = self.sudo().activity_schedule(
            "mail.mail_activity_data_todo",
            date_deadline=deadline,
            summary=_("Rechtenreview van de uitvoerende agents"),
            note=note,
            user_id=user.id,
        )
        self.sudo().responsible_user_id = user
        return activity

    @api.model
    def _cron_quarterly_review(self):
        """De kwartaalcadans: rapport samenstellen en uitzetten."""
        review = self.build(trigger="cron")
        review._schedule_todo()
        return review

    # ------------------------------------------------------------------
    @api.model
    def action_build_now(self):
        review = self.build(trigger="manual")
        review._schedule_todo()
        return {
            "type": "ir.actions.act_window",
            "name": _("Rechtenreview"),
            "res_model": self._name,
            "res_id": review.id,
            "view_mode": "form",
            "target": "current",
        }


class AiPermissionReviewLine(models.Model):
    """Eén agent in één rechtenreview, plus de uitkomst ervan."""

    _name = "daadit.ai.permission.review.line"
    _description = "Rechtenreview \u2014 agentregel"
    _order = "review_id desc, agent_name"

    review_id = fields.Many2one(
        "daadit.ai.permission.review", string="Review", required=True,
        ondelete="cascade", index=True,
    )
    agent_id = fields.Many2one(
        "ai.agent", string="Agent", readonly=True, ondelete="set null",
    )
    agent_name = fields.Char(
        string="Naam", readonly=True,
        help="De naam zoals hij tijdens de review was; een later "
             "verwijderde agent blijft zo terug te lezen.",
    )
    rights_level = fields.Char(string="Rechtenniveau", readonly=True)
    role = fields.Char(string="Rol", readonly=True)

    write_tools = fields.Text(string="Schrijvende tools", readonly=True)
    outbound_tools = fields.Text(
        string="Publiceert of verstuurt", readonly=True,
        help="Tools die naar buiten gaan: een verstuurde mailing of een "
             "gepubliceerde post is niet terug te draaien.",
    )
    destructive_tools = fields.Text(
        string="Tools die weghalen", readonly=True,
    )
    scope_models = fields.Text(string="Leesscope", readonly=True)
    blocked_models = fields.Text(
        string="Geblokkeerde modellen", readonly=True,
    )
    scope_unrestricted = fields.Boolean(
        string="Leesscope niet ingeperkt", readonly=True,
        help="Er staat geen toegestane-modellenlijst; de agent leest "
             "alles waar de 'run as'-gebruiker bij mag.",
    )
    scope_unknown = fields.Boolean(
        string="Leesscope onbekend", readonly=True,
        help="De providermodule met de scopevelden is hier niet "
             "geïnstalleerd, dus de scope is niet uit te lezen.",
    )
    pii_blocklist = fields.Char(string="PII-blocklist", readonly=True)
    hard_blocked = fields.Text(string="Hard geblokkeerd", readonly=True)

    config_changed = fields.Boolean(
        string="Configuratie gewijzigd", readonly=True,
    )
    config_change_detail = fields.Text(
        string="Wat er wijzigde", readonly=True,
    )

    run_count = fields.Integer(string="Runs", readonly=True)
    claims_unverified_count = fields.Integer(
        string="Beweringen zonder dekking", readonly=True,
    )
    needs_attention_count = fields.Integer(
        string="Runs met aandacht", readonly=True,
    )
    effective_write_count = fields.Integer(
        string="Echte schrijfacties", readonly=True,
    )
    refused_write_count = fields.Integer(
        string="Geweigerde schrijfpogingen", readonly=True,
        help="Schrijfpogingen die de guard of de tool niet uitvoerde. "
             "Niet per se fout: bij Pim werd op 1-8 precies zo een "
             "foutieve aanroep tegengehouden.",
    )

    outcome = fields.Selection(
        [("open", "Nog te beoordelen"),
         ("keep", "Niveau blijft"),
         ("narrow", "Scope inperken"),
         ("revoke", "Recht intrekken")],
        string="Uitkomst", default="open", required=True,
        help="De reviewer legt hier zijn besluit vast. Het besluit "
             "uitvoeren gebeurt in de configuratie, niet hier.",
    )
    reviewer_note = fields.Text(
        string="Toelichting van de reviewer",
    )

    def action_open_agent(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "ai.agent",
            "res_id": self.agent_id.id,
            "view_mode": "form",
        }

    def action_view_runs(self):
        """De runs waar de cijfers van deze regel uit komen."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Runs \u2014 %s") % (self.agent_name or ""),
            "res_model": "daadit.ai.agent.schedule.run",
            "view_mode": "list,form",
            "domain": [
                ("agent_id", "=", self.agent_id.id),
                ("start_date", ">=", self.review_id.period_start),
                ("start_date", "<=", self.review_id.period_end),
            ],
        }
