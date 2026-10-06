# -*- coding: utf-8 -*-
"""Genummerde pakketten van de agentconfiguratie (taken 706 en 738).

De configuratie van dit systeem — welke collega's er zijn, wat ze mogen
lezen, welke tools in welk onderwerp zitten, welke planningen lopen — is
in de UI gebouwd en leeft alleen in de database. Er is geen commit, geen
diff en geen terugweg. Twee keer eerder was een verdwenen tool pas dagen
later te zien in het gedrag van een agent (Nova, 2-8: publicatietools uit
topic 15 gehaald terwijl haar opdracht ze nog vroeg).

Elke opname is daarom een **pakket**: een oplopend nummer per vestiging,
een tijdstip, wie het maakte en de blueprint-JSON. Terugrollen naar de
stand van gisteren is dan het pakket van gisteren opnieuw toepassen — met
het nummer als de taal waarin je erover praat ("zet uitzendkracht.ai
terug naar pakket 12"). Een pakket wordt alleen bewaard wanneer er iets
is veranderd; anders verdrinkt de echte wijziging in identieke rijen.

Drie regels maken een terugrol veilig, en het zijn dezelfde regels als
bij een import, want het is hetzelfde pad (``apply_blueprint``):

1. **Nooit een sleutel in een pakket.** De export heeft twee netten (een
   allowlist en een naamcontrole); bij het bewaren gaat de inhoud daar
   nog eens langs, zodat ook een pakket dat via de ORM wordt aangemaakt
   geen ``mistral_key`` kan bevatten.
2. **Een terugrol zet geen planning aan.** Nieuwe planningen komen
   uitgeschakeld binnen en ``active`` staat niet in de over te nemen
   velden; een agent die na een terugrol begint te draaien is een agent
   die niemand heeft aangezet.
3. **Eerst zien, dan doen.** Een terugrol maakt altijd eerst de proef
   (dry run) en bewaart die, en legt daarna vast wat hij feitelijk heeft
   veranderd, door wie en wanneer.
"""
import hashlib
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Bewaartermijn, gelijkgetrokken met de audit-argumenten (taak 635).
RETENTION_PARAM = "daadit_tenant_blueprint.snapshot_retention_days"
DEFAULT_RETENTION_DAYS = 90

# Welke vestiging deze database is. Op één deployment met meerdere
# vestigingen loopt de nummering per vestiging, zodat "pakket 12" iets
# betekent zonder dat je erbij hoeft te zeggen van wie.
VESTIGING_PARAM = "daadit_tenant_blueprint.vestiging"


class ConfigSnapshot(models.Model):
    """Eén genummerd pakket met de volledige agentconfiguratie."""

    _name = "daadit.config.snapshot"
    _description = "Configuratiepakket (genummerde momentopname)"
    _order = "vestiging, package_number desc, id desc"

    name = fields.Char(required=True, readonly=True)
    vestiging = fields.Char(
        string="Vestiging", required=True, readonly=True, index=True,
        default=lambda self: self._default_vestiging(),
        help="De klantvestiging waar dit pakket bij hoort. De nummering "
             "loopt per vestiging.",
    )
    package_number = fields.Integer(
        string="Pakketnummer", required=True, readonly=True, index=True,
        default=0,
        help="Oplopend per vestiging. Terugrollen is: een lager "
             "pakketnummer opnieuw toepassen.",
    )
    payload = fields.Text(
        string="Configuratie", required=True, readonly=True,
        help="De blueprint zoals hij op dat moment was, zonder "
             "sleutels of tokens.",
    )
    digest = fields.Char(
        string="Vingerafdruk", required=True, readonly=True, index=True,
        help="SHA-256 over de inhoud. Twee opnamen met dezelfde "
             "vingerafdruk beschrijven dezelfde configuratie.",
    )
    change_summary = fields.Text(
        string="Wijzigingen", readonly=True,
        help="Wat er anders is dan in de vorige opname.",
    )
    agent_count = fields.Integer(readonly=True)
    schedule_count = fields.Integer(readonly=True)
    trigger = fields.Selection(
        [("cron", "Automatisch"), ("manual", "Handmatig")],
        default="cron", required=True, readonly=True,
    )
    restore_preview = fields.Text(
        string="Proef bij terugrollen", readonly=True,
        help="Wat de laatste terugrol zou veranderen, vastgelegd vóór hij "
             "het deed.",
    )
    restore_log = fields.Text(
        string="Terugrolverslag", readonly=True,
        help="Wat de laatste terugrol feitelijk heeft veranderd.",
    )
    restored_at = fields.Datetime(string="Teruggerold op", readonly=True)
    restored_by_id = fields.Many2one(
        "res.users", string="Teruggerold door", readonly=True,
    )

    # v19: models.Constraint in plaats van de afgeschafte
    # _sql_constraints-lijst. Odoo 19 negeert die lijst stilzwijgend, dus
    # met de oude vorm bestond de unieke combinatie helemaal niet en kon
    # "terug naar pakket 12" naar twee verschillende pakketten wijzen.
    _package_number_per_vestiging = models.Constraint(
        "UNIQUE(vestiging, package_number)",
        "Binnen één vestiging kan een pakketnummer maar één keer "
        "bestaan — anders is 'terug naar pakket 12' geen opdracht.",
    )

    # Bewust geen unieke vingerafdruk: een configuratie die wordt
    # teruggedraaid naar een eerdere stand levert dezelfde inhoud op,
    # en die terugdraai is juist wat je wil kunnen zien.

    # ------------------------------------------------------------------
    @api.model
    def _default_vestiging(self):
        """De naam van deze vestiging, met de database als terugval."""
        return self.env["ir.config_parameter"].sudo().get_param(
            VESTIGING_PARAM,
        ) or self.env.cr.dbname

    @api.model_create_multi
    def create(self, vals_list):
        """Nummer het pakket, en laat geen sleutel in een pakket landen.

        De export bewaakt zichzelf al, maar een pakket kan ook via de ORM
        of een migratie ontstaan. Deze controle is hetzelfde tweede net,
        één laag dieper: liever een harde fout bij het bewaren dan een
        bewaarde sleutel die later ergens heen geëxporteerd wordt.
        """
        Blueprint = self.env["daadit.tenant.blueprint"]
        for vals in vals_list:
            vestiging = vals.get("vestiging") or self._default_vestiging()
            vals["vestiging"] = vestiging
            if not vals.get("package_number"):
                vals["package_number"] = self._next_package_number(vestiging)
            payload = vals.get("payload")
            if payload:
                try:
                    content = json.loads(payload)
                except ValueError as exc:
                    raise UserError(_(
                        "Dit pakket is geen geldige JSON en kan dus niet "
                        "worden bewaard: %s", exc,
                    ))
                Blueprint._assert_no_secrets(content, "pakket")
        return super().create(vals_list)

    @api.model
    def _next_package_number(self, vestiging):
        latest = self.sudo().search(
            [("vestiging", "=", vestiging)],
            order="package_number desc", limit=1,
        )
        return (latest.package_number or 0) + 1

    # ------------------------------------------------------------------
    @api.model
    def _digest_of(self, blueprint):
        """Vingerafdruk over de inhoud, zonder het tijdstip.

        ``exported_at`` verandert per definitie elke keer; zou die
        meetellen, dan was elke opname 'nieuw' en zei de reeks niets
        meer over echte wijzigingen.
        """
        stable = {
            k: v for k, v in blueprint.items()
            if k not in ("exported_at", "source_db")
        }
        blob = json.dumps(stable, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @api.model
    def _diff(self, previous, current):
        """Verschillen tussen twee blueprints, in leesbare regels."""
        lines = []
        for section, key in (
            ("agents", "name"), ("topics", "name"), ("schedules", "name"),
        ):
            old = {
                item.get(key): item for item in (previous.get(section) or [])
            }
            new = {
                item.get(key): item for item in (current.get(section) or [])
            }
            for gone in sorted(set(old) - set(new)):
                lines.append(_("%(section)s verdwenen: %(name)s",
                               section=section, name=gone))
            for added in sorted(set(new) - set(old)):
                lines.append(_("%(section)s nieuw: %(name)s",
                               section=section, name=added))
            for same in sorted(set(old) & set(new)):
                for field in sorted(set(old[same]) | set(new[same])):
                    before = old[same].get(field)
                    after = new[same].get(field)
                    if before == after:
                        continue
                    lines.append(_(
                        "%(section)s %(name)s: %(field)s gewijzigd",
                        section=section, name=same, field=field,
                    ))
        old_params = previous.get("parameters") or {}
        new_params = current.get("parameters") or {}
        for key in sorted(set(old_params) | set(new_params)):
            before = old_params.get(key)
            after = new_params.get(key)
            if before == after:
                continue
            lines.append(_(
                "instelling %(key)s: %(before)s \u2192 %(after)s",
                key=key,
                before=before if before is not None else _("(afwezig)"),
                after=after if after is not None else _("(afwezig)"),
            ))
        return lines

    # ------------------------------------------------------------------
    @api.model
    def latest_package(self, vestiging=None):
        """Het hoogste pakket van deze vestiging, of een leeg recordset."""
        return self.sudo().search(
            [("vestiging", "=", vestiging or self._default_vestiging())],
            order="package_number desc, id desc", limit=1,
        )

    @api.model
    def capture(self, trigger="cron", vestiging=None):
        """Bewaar de huidige configuratie als het volgende pakket.

        Returns het nieuwe pakket, of een leeg recordset wanneer er niets
        veranderd is: een reeks identieke pakketten maakt de echte
        wijziging onvindbaar en geeft niets extra's om naar terug te
        rollen.
        """
        vestiging = vestiging or self._default_vestiging()
        blueprint = self.env["daadit.tenant.blueprint"].build_blueprint()
        digest = self._digest_of(blueprint)
        latest = self.latest_package(vestiging)
        if latest and latest.digest == digest:
            _logger.info(
                "config-pakket: %s onveranderd sinds pakket %s",
                vestiging, latest.package_number,
            )
            return self.browse()
        lines = []
        if latest:
            try:
                lines = self._diff(json.loads(latest.payload), blueprint)
            except ValueError:
                lines = [_("het vorige pakket is onleesbaar")]
        else:
            lines = [_("eerste pakket \u2014 niets om mee te vergelijken")]
        number = self._next_package_number(vestiging)
        snapshot = self.sudo().create({
            "name": _(
                "%(vestiging)s pakket %(number)s — %(moment)s",
                vestiging=vestiging, number=number,
                moment=fields.Datetime.to_string(fields.Datetime.now()),
            ),
            "vestiging": vestiging,
            "package_number": number,
            "payload": json.dumps(blueprint, indent=2, ensure_ascii=False),
            "digest": digest,
            "change_summary": "\n".join(lines),
            "agent_count": len(blueprint.get("agents") or []),
            "schedule_count": len(blueprint.get("schedules") or []),
            "trigger": trigger,
        })
        _logger.info(
            "config-pakket: %s vastgelegd, %d wijziging(en)",
            snapshot.name, len(lines),
        )
        return snapshot

    @api.model
    def _cron_capture(self):
        """Dagelijkse opname plus opschonen van verlopen opnamen."""
        self.capture(trigger="cron")
        self._sweep()

    @api.model
    def _sweep(self):
        icp = self.env["ir.config_parameter"].sudo()
        try:
            days = int(icp.get_param(
                RETENTION_PARAM, DEFAULT_RETENTION_DAYS,
            ) or DEFAULT_RETENTION_DAYS)
        except (TypeError, ValueError):
            days = DEFAULT_RETENTION_DAYS
        if days <= 0:
            return 0
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        expired = self.sudo().search([("create_date", "<", cutoff)])
        # Het oudste pakket per vestiging blijft staan: zonder een
        # beginpunt is een reeks diffs niet meer terug te lezen, en heeft
        # een vestiging geen enkel pakket meer om naar terug te rollen.
        for vestiging in set(expired.mapped("vestiging")):
            oldest = self.sudo().search(
                [("vestiging", "=", vestiging)],
                order="package_number asc, id asc", limit=1,
            )
            expired -= oldest
        count = len(expired)
        if count:
            expired.unlink()
            _logger.info(
                "config-snapshot: %d verlopen opname(n) verwijderd "
                "(bewaartermijn %d dagen)", count, days,
            )
        return count

    # ------------------------------------------------------------------
    def action_capture_now(self):
        latest = self.latest_package()
        snapshot = self.capture(trigger="manual")
        if not snapshot:
            raise UserError(_(
                "De configuratie is niet veranderd sinds pakket "
                "%(number)s, dus er is niets nieuws vast te leggen — dat "
                "pakket is de stand van nu en dus ook het pakket om naar "
                "terug te rollen.",
                number=latest.package_number if latest else 0,
            ))
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": snapshot.id,
            "view_mode": "form",
        }

    def action_restore_dry_run(self):
        return self._restore(dry_run=True)

    def action_restore(self):
        return self._restore(dry_run=False)

    def _restore(self, dry_run):
        """Rol terug naar dit pakket, via het blueprint-pad.

        Dat pad matcht op naam en maakt onbekende collega's niet aan, dus
        terugrollen kan nooit een halve collega opleveren die lijkt te
        werken. Een echte terugrol doet altijd éérst de proef en bewaart
        die, en legt daarna vast wat hij feitelijk veranderde: zonder dat
        onderscheid is achteraf niet te zien of de terugrol deed wat hij
        aankondigde.
        """
        self.ensure_one()
        try:
            blueprint = json.loads(self.payload or "{}")
        except ValueError as exc:
            raise UserError(_("Dit pakket is onleesbaar: %s", exc))
        Blueprint = self.env["daadit.tenant.blueprint"]
        preview = Blueprint.apply_report(blueprint, dry_run=True)["text"]
        if dry_run:
            self.sudo().write({"restore_preview": preview})
            return self._restore_notification(
                _("Proef \u2014 dit zou er gebeuren:"), preview, dry_run=True,
            )
        body = Blueprint.apply_report(blueprint, dry_run=False)["text"]
        self.sudo().write({
            "restore_preview": preview,
            "restore_log": body,
            "restored_at": fields.Datetime.now(),
            "restored_by_id": self.env.user.id,
        })
        _logger.warning(
            "config-pakket: %s teruggerold door %s \u2014 %s",
            self.name, self.env.user.login, body,
        )
        return self._restore_notification(
            _("Teruggerold naar pakket %s:", self.package_number),
            body, dry_run=False,
        )

    def _restore_notification(self, head, body, dry_run):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": head,
                "message": body,
                "sticky": True,
                "type": "warning" if dry_run else "success",
            },
        }
