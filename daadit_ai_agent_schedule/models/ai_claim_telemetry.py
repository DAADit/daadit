# -*- coding: utf-8 -*-
"""Dagtelling van claim tegenover effect, per agent en per model (1071).

Per run wordt al gemeten of een bewering door een geregistreerd effect
gedekt is: ``claims_unverified`` staat op de run, ``attention_reason``
noemt de grond, en een opvolgrun mag "opgelost" alleen afgeven bij een
effectieve write. Wat ontbrak is het beeld erboven. Zonder telling per
dag ontdek je onbetrouwbaarheid per incident, en dat is precies hoe
taken 714, 779 en 803 zijn ontstaan: één keer opgemerkt, terwijl het
verschijnsel al weken liep.

Deze telling is bewust afgeleid en niet gemeten naast de runlog: elk
getal komt uit ``daadit.ai.agent.schedule.run``, en de gebruikte runs
staan als ``run_ids`` in de regel. Een cijfer dat opvalt is daarmee
narekenbaar in plaats van te vertrouwen.

De koppeling met kosten staat er met opzet naast: een goedkoper model
mag geld schelen, maar niet stil meer onwaarheid opleveren. Die twee
kolommen naast elkaar maken dat een afweging in plaats van een gevoel.
"""
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

# Hoeveel dagen de cron terugkijkt. Meer dan één, zodat een dag waarop
# de cron niet liep alsnog wordt bijgewerkt in plaats van voorgoed leeg
# te blijven staan.
BACKFILL_DAYS_PARAM = "daadit_ai_agent_schedule.claim_telemetry_backfill_days"
DEFAULT_BACKFILL_DAYS = 3


class AiClaimTelemetry(models.Model):
    """Eén dag, één agent, één model: wat er geclaimd en wat er gedaan is."""

    _name = "daadit.ai.claim.telemetry"
    _description = "Claim tegenover effect per dag"
    _order = "date desc, unverified_share desc, agent_name"

    date = fields.Date(string="Dag", required=True, readonly=True, index=True)
    agent_id = fields.Many2one(
        "ai.agent", string="Collega", readonly=True, ondelete="set null",
        index=True,
    )
    agent_name = fields.Char(
        string="Collega (naam)", readonly=True,
        help="De naam zoals hij die dag was — een latere hernoeming mag "
             "de geschiedenis niet herschrijven.",
    )
    llm_model = fields.Char(string="Model", readonly=True, index=True)
    run_count = fields.Integer(string="Runs", readonly=True)
    unverified_count = fields.Integer(
        string="Onverifieerbaar", readonly=True,
        help="Runs die werk claimden zonder dat een geregistreerd effect "
             "dat dekt.",
    )
    attention_count = fields.Integer(
        string="Vraagt aandacht", readonly=True,
    )
    disputed_count = fields.Integer(
        string="Betwist", readonly=True,
        help="Runs waarin een beweerde handeling geen geslaagde "
             "tool-actie achter zich had (verzonnen). Telt ook mee in "
             "Onverifieerbaar.",
    )
    write_attempt_count = fields.Integer(
        string="Schrijfpogingen", readonly=True,
    )
    effective_write_count = fields.Integer(
        string="Effectieve writes", readonly=True,
    )
    lost_write_count = fields.Integer(
        string="Verloren writes", readonly=True,
        help="Pogingen die niets hebben vastgelegd.",
    )
    cost_eur = fields.Float(
        string="Kosten (EUR)", readonly=True, digits=(12, 4),
    )
    unverified_share = fields.Float(
        string="Aandeel onverifieerbaar", readonly=True, digits=(5, 2),
        help="Onverifieerbare runs gedeeld door alle runs van die dag, "
             "in procenten. Dit is het getal dat over de tijd moet "
             "zakken.",
    )
    run_ids = fields.Many2many(
        "daadit.ai.agent.schedule.run", string="Runs (bron)",
        readonly=True,
        help="De runs waaruit deze regel is geteld, zodat elk cijfer "
             "narekenbaar is.",
    )
    display_name = fields.Char(compute="_compute_display_name")

    # v19: models.Constraint in plaats van de afgeschafte
    # _sql_constraints-lijst.
    _date_agent_model_uniq = models.Constraint(
        "UNIQUE(date, agent_id, llm_model)",
        "Er is per dag, collega en model één telling.",
    )

    @api.depends("date", "agent_name", "llm_model")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "%s — %s (%s)" % (
                fields.Date.to_string(rec.date) or "",
                rec.agent_name or _("onbekend"),
                rec.llm_model or _("geen model"),
            )

    # ------------------------------------------------------------------
    @api.model
    def _backfill_days(self):
        icp = self.env["ir.config_parameter"].sudo()
        try:
            days = int(
                icp.get_param(BACKFILL_DAYS_PARAM, DEFAULT_BACKFILL_DAYS)
                or DEFAULT_BACKFILL_DAYS
            )
        except (TypeError, ValueError):
            days = DEFAULT_BACKFILL_DAYS
        return max(1, days)

    @api.model
    def _runs_of_day(self, day):
        """De runs die op ``day`` zijn begonnen.

        De grens loopt op de datum van ``start_date``, dezelfde grens die
        de restlijst gebruikt; twee verschillende dagdefinities zouden
        twee verschillende waarheden geven.

        Alleen de telvelden worden geladen — niet ``findings`` /
        ``error`` / actieresultaten. Op een drukke dag zijn die tekst-
        velden het verschil tussen een lichte telling en een worker die
        op signal 9 sneuvelt.
        """
        Run = self.env["daadit.ai.agent.schedule.run"].sudo()
        start = fields.Datetime.to_datetime("%s 00:00:00" % day)
        end = fields.Datetime.to_datetime("%s 23:59:59" % day)
        runs = Run.search([
            ("start_date", ">=", start), ("start_date", "<=", end),
        ])
        if runs:
            runs.fetch([
                "agent_id", "model", "claims_unverified", "needs_attention",
                "state",
                "write_attempt_count", "write_action_count",
                "lost_write_count", "estimated_cost_eur",
            ])
        return runs

    @api.model
    def collect(self, day):
        """Tel één dag opnieuw en geef de regels terug.

        Opnieuw tellen overschrijft: een dag die al geteld was maar
        waarvan runs zijn bijgewerkt, klopt daarna weer. Dat is bewust
        idempotent — een cron die twee keer draait mag geen dubbele
        werkelijkheid opleveren.
        """
        # Ids + tellers in gewone dicts, geen groeiende recordsets via
        # ``|=``: die houden alle runs in het geheugen tot het eind.
        buckets = {}
        for run in self._runs_of_day(day):
            key = (run.agent_id.id or 0, (run.model or "").strip())
            bucket = buckets.get(key)
            if bucket is None:
                bucket = {
                    "ids": [],
                    "agent_name": (
                        run.agent_id.name if run.agent_id
                        else _("zonder collega")
                    ),
                    "unverified": 0,
                    "attention": 0,
                    "disputed": 0,
                    "write_attempt": 0,
                    "write_action": 0,
                    "lost_write": 0,
                    "cost_eur": 0.0,
                }
                buckets[key] = bucket
            bucket["ids"].append(run.id)
            if run.state == "disputed":
                bucket["disputed"] += 1
            if run.claims_unverified or run.state == "disputed":
                bucket["unverified"] += 1
            if run.needs_attention:
                bucket["attention"] += 1
            bucket["write_attempt"] += int(run.write_attempt_count or 0)
            bucket["write_action"] += int(run.write_action_count or 0)
            bucket["lost_write"] += int(run.lost_write_count or 0)
            bucket["cost_eur"] += float(run.estimated_cost_eur or 0.0)

        rows = self.browse()
        for (agent_id, llm_model), bucket in buckets.items():
            n = len(bucket["ids"])
            vals = {
                "date": day,
                "agent_id": agent_id or False,
                "agent_name": bucket["agent_name"],
                "llm_model": llm_model,
                "run_count": n,
                "unverified_count": bucket["unverified"],
                "attention_count": bucket["attention"],
                "disputed_count": bucket["disputed"],
                "write_attempt_count": bucket["write_attempt"],
                "effective_write_count": bucket["write_action"],
                "lost_write_count": bucket["lost_write"],
                "cost_eur": bucket["cost_eur"],
                "unverified_share": (
                    round(100.0 * bucket["unverified"] / n, 2) if n else 0.0
                ),
                "run_ids": [(6, 0, bucket["ids"])],
            }
            existing = self.sudo().search([
                ("date", "=", day),
                ("agent_id", "=", agent_id or False),
                ("llm_model", "=", llm_model),
            ], limit=1)
            if existing:
                existing.write(vals)
                rows |= existing
            else:
                rows |= self.sudo().create(vals)
        _logger.info(
            "claim-telemetrie %s: %s regel(s), %s run(s)",
            day, len(rows), sum(rows.mapped("run_count")),
        )
        return rows

    @api.model
    def _cron_collect(self):
        """De dagcadans: gisteren tellen en een paar dagen bijwerken."""
        today = fields.Date.context_today(self)
        for offset in range(1, self._backfill_days() + 1):
            self.collect(fields.Date.subtract(today, days=offset))
