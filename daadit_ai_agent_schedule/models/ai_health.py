# -*- coding: utf-8 -*-
"""Eén beeld van de gezondheid van het agentteam (taak 638).

Alle feiten die je nodig hebt om te weten of het goed gaat, bestonden al
— maar verspreid over vier lijsten en twee tellers. In de praktijk keek
niemand ze na, en werden storingen pas duidelijk toen een klant belde:
een planning die drie dagen achterliep, een agent die 'gepubliceerd'
schreef zonder één schrijfactie, een bundel die halverwege de maand op
was.

Dit brengt die feiten samen in één scherm, met dezelfde bronnen als de
governance zelf gebruikt — geen tweede waarheid, en geen enkele
opgeslagen kolom erbij. Elk getal is doorklikbaar naar de onderliggende
records; een dashboard dat je niet kunt narekenen is een mening.
"""
from datetime import timedelta

from odoo import api, fields, models, _

from ..services import prompt_registry

# Een planning die langer dan dit over zijn tijd is, loopt achter. Ruim
# genomen: de cron scant elk kwartier en een lange run mag een tick
# missen zonder dat het scherm rood wordt.
_OVERDUE_GRACE_HOURS = 2


class AiHealth(models.TransientModel):
    """Momentopname van de gezondheid van het agentteam."""

    _name = "daadit.ai.health"
    _description = "Gezondheid van het agentteam"

    # -- bundel en verbruik -------------------------------------------
    plan_name = fields.Char(
        string="Pakket", compute="_compute_health",
    )
    units_used = fields.Float(
        string="Gebruikte eenheden", compute="_compute_health",
    )
    units_included = fields.Float(
        string="Inbegrepen eenheden", compute="_compute_health",
    )
    units_ratio = fields.Float(
        string="Verbruikt", compute="_compute_health",
        help="Deel van de inbegrepen eenheden dat deze maand is "
             "gebruikt. Een fractie, niet een percentage: het scherm "
             "toont hem met het percentage-widget, dat zelf met 100 "
             "vermenigvuldigt.",
    )
    overage_eur = fields.Float(
        string="Meerverbruik (EUR)", compute="_compute_health",
    )
    spend_today_eur = fields.Float(
        string="Kosten vandaag (EUR)", compute="_compute_health",
    )
    spend_month_eur = fields.Float(
        string="Kosten deze maand (EUR)", compute="_compute_health",
    )

    # -- werk en betrouwbaarheid --------------------------------------
    runs_24h = fields.Integer(
        string="Runs (24 uur)", compute="_compute_health",
    )
    runs_failed_24h = fields.Integer(
        string="Mislukte runs (24 uur)", compute="_compute_health",
    )
    failed_actions_24h = fields.Integer(
        string="Mislukte toolacties (24 uur)", compute="_compute_health",
        help="Ook acties in runs die als geslaagd zijn afgesloten — "
             "daar zat het gat dat de restlijst eerder niet zag.",
    )
    attention_24h = fields.Integer(
        string="Runs met aandacht (24 uur)", compute="_compute_health",
        help="Runs die om aandacht vragen: een bewering zonder dekking, "
             "een mislukte actie of een schrijfpoging die niet landde. "
             "Zelfde venster als de rest van dit scherm.",
    )
    claims_24h = fields.Integer(
        string="Beweringen zonder dekking (24 uur)",
        compute="_compute_health",
        help="Runs die werk claimen terwijl geen enkele schrijfactie is "
             "vastgelegd.",
    )

    # -- planning ------------------------------------------------------
    schedules_active = fields.Integer(
        string="Actieve planningen", compute="_compute_health",
    )
    schedules_off = fields.Integer(
        string="Uitgezette planningen", compute="_compute_health",
        help="Alles wat uit staat, ook wat met opzet uit is gezet: "
             "vervangen door een nieuwere planning, of een eenmalige "
             "test. Dat is geen storing.",
    )
    schedules_tripped = fields.Integer(
        string="Uitgezet na fouten", compute="_compute_health",
        help="Uitgezet door de circuit breaker: het aantal "
             "opeenvolgende fouten haalde de drempel van die planning. "
             "Dit is werk dat stilstaat zonder dat iemand dat koos.",
    )
    schedules_overdue = fields.Integer(
        string="Planningen die achterlopen", compute="_compute_health",
    )
    last_run_date = fields.Datetime(
        string="Laatste run", compute="_compute_health",
    )

    # -- promptregistry ------------------------------------------------
    registry_entries = fields.Integer(
        string="Registry-koppelingen", compute="_compute_health",
        help="Koppelingen artikel \u2194 planning uit de promptregistry "
             "die te controleren zijn. Staat er 0 terwijl de registry "
             "gevuld is, dan is Knowledge hier niet beschikbaar en is er "
             "niets vergeleken.",
    )
    prompt_drift = fields.Integer(
        string="Planningen met drift", compute="_compute_health",
        help="Planningen waarvan de draaiende instructie afwijkt van het "
             "registry-artikel. Een leeg blok in het artikel is met opzet "
             "niet gesynchroniseerd en telt niet mee.",
    )
    prompt_drift_detail = fields.Text(
        string="Welke drift", compute="_compute_health",
        help="Per planning waar de gepubliceerde en de draaiende tekst "
             "uit elkaar lopen.",
    )
    registry_broken = fields.Integer(
        string="Koppelingen zonder doel", compute="_compute_health",
        help="Registry-entries waarvan het artikel of de planning niet "
             "meer bestaat: die instructie wordt nergens toegepast.",
    )
    registry_inactive = fields.Integer(
        string="Koppelingen naar uitgezette planning",
        compute="_compute_health",
        help="Het artikel staat gepubliceerd, maar de planning staat uit. "
             "Dat telt niet als gedekt en ook niet als drift: er draait "
             "niets om mee te vergelijken.",
    )
    registry_orphans = fields.Integer(
        string="Artikelen zonder koppeling", compute="_compute_health",
        help="Artikelen in de registry-map waar geen koppeling naar "
             "wijst. De sync doet er niets mee, dus wat erin staat is "
             "gepubliceerd maar niet in gebruik.",
    )
    schedules_covered = fields.Integer(
        string="Planningen met registry-artikel", compute="_compute_health",
    )
    coverage_report = fields.Text(
        string="Dekkingsrapport", compute="_compute_health",
        help="Per planning zonder artikel een voorstel voor wat erin "
             "hoort. Een voorstel, geen wijziging: dit scherm schrijft "
             "niets in Knowledge en niets op de planning.",
    )
    schedules_uncovered = fields.Integer(
        string="Planningen zonder registry-artikel",
        compute="_compute_health",
        help="Deze planningen draaien een instructie die nergens is "
             "gepubliceerd. Ze kunnen per definitie geen drift hebben \u2014 "
             "daarom zegt 'nul drift' pas iets naast dit getal.",
    )

    # -- samenvatting --------------------------------------------------
    status = fields.Selection(
        [("ok", "In orde"),
         ("warn", "Let op"),
         ("alarm", "Actie nodig")],
        string="Status", compute="_compute_health",
    )
    status_reason = fields.Text(
        string="Waarom", compute="_compute_health",
    )

    # ------------------------------------------------------------------
    @api.depends_context("uid")
    def _compute_health(self):
        Run = self.env["daadit.ai.agent.schedule.run"].sudo()
        Schedule = self.env["daadit.ai.agent.schedule"].sudo()
        now = fields.Datetime.now()
        since = now - timedelta(hours=24)
        usage = Schedule.tenant_usage_state()
        overdue_before = now - timedelta(hours=_OVERDUE_GRACE_HOURS)

        runs_24h = Run.search_count([("start_date", ">=", since)])
        failed_runs = Run.search_count([
            ("start_date", ">=", since), ("state", "=", "error"),
        ])
        attention = Run.search_count([
            ("start_date", ">=", since), ("needs_attention", "=", True),
        ])
        claims = Run.search_count([
            ("start_date", ">=", since), ("claims_unverified", "=", True),
        ])
        failed_actions = self.env[
            "daadit.ai.agent.schedule.run.action"
        ].sudo().search_count([
            ("run_id.start_date", ">=", since), ("is_error", "=", True),
        ])
        active = Schedule.search_count([])
        inactive = Schedule.with_context(active_test=False).search([
            ("active", "=", False),
        ])
        off = len(inactive)
        # De circuit breaker zet een planning uit zodra het aantal
        # opeenvolgende fouten de drempel haalt, en laat die teller
        # staan. Een planning die met opzet uit is gezet heeft die
        # teller op 0 — dat onderscheid is het verschil tussen "iemand
        # koos dit" en "hier staat werk stil".
        tripped = len(inactive.filtered(
            lambda s: s.failure_threshold > 0
            and s.consecutive_failures >= s.failure_threshold
        ))
        overdue = Schedule.search_count([
            ("nextcall", "<", overdue_before),
        ])
        latest = Run.search([], order="start_date desc", limit=1)
        state = prompt_registry.coverage(self.env)
        registry = state["rows"]
        drift = prompt_registry.drift_rows(registry)

        for rec in self:
            rec.plan_name = usage["plan"]
            rec.units_used = usage["units_used"]
            rec.units_included = usage["units_included"]
            rec.units_ratio = usage["ratio"]
            rec.overage_eur = usage["overage_eur"]
            rec.spend_today_eur = Schedule._tenant_spend_eur(
                Schedule._today_start_utc(),
            )
            rec.spend_month_eur = Schedule._tenant_spend_eur(
                Schedule._month_start_utc(),
            )
            rec.runs_24h = runs_24h
            rec.runs_failed_24h = failed_runs
            rec.failed_actions_24h = failed_actions
            rec.attention_24h = attention
            rec.claims_24h = claims
            rec.schedules_active = active
            rec.schedules_off = off
            rec.schedules_tripped = tripped
            rec.schedules_overdue = overdue
            rec.last_run_date = latest.start_date or False
            rec.registry_entries = len(registry)
            rec.prompt_drift = len(drift)
            rec.prompt_drift_detail = prompt_registry.summary(registry)
            rec.registry_broken = len(state["dead"])
            rec.registry_inactive = len(state["inactive"])
            rec.registry_orphans = len(state["orphans"])
            rec.schedules_uncovered = len(state["uncovered"])
            rec.schedules_covered = (
                len(state["schedules"]) - len(state["uncovered"])
            )
            rec.coverage_report = prompt_registry.coverage_report(state)
            status, reasons = rec._assess()
            rec.status = status
            rec.status_reason = "\n".join(reasons)

    def _assess(self):
        """Status plus de redenen ervoor, in volgorde van urgentie.

        Alarm is voorbehouden aan dingen die nú stilstaan of nú geld
        kosten. Een enkele mislukte actie is geen alarm — dat zou het
        scherm permanent rood maken en daarmee waardeloos.
        """
        self.ensure_one()
        reasons = []
        status = "ok"
        warn_ratio = self.env[
            "daadit.ai.agent.schedule"
        ]._fair_use_warn_ratio()
        if self.units_included and self.units_ratio >= 1.0:
            status = "alarm"
            reasons.append(_(
                "De bundel is op: %(used)s van %(incl)s eenheden. "
                "Geplande taken staan stil tot de volgende maand, "
                "meerverbruik %(over).2f EUR.",
                used=int(self.units_used), incl=int(self.units_included),
                over=self.overage_eur,
            ))
        elif self.units_included and self.units_ratio >= warn_ratio:
            status = "warn"
            reasons.append(_(
                "Fair use: %(pct)d%% van de bundel gebruikt.",
                pct=int(self.units_ratio * 100),
            ))
        if self.schedules_overdue:
            status = "alarm"
            reasons.append(_(
                "%(count)s planning(en) lopen meer dan %(hours)s uur "
                "achter \u2014 werk dat had moeten gebeuren, gebeurt niet.",
                count=self.schedules_overdue, hours=_OVERDUE_GRACE_HOURS,
            ))
        if self.schedules_tripped:
            status = "alarm"
            reasons.append(_(
                "%(count)s planning(en) zijn door de circuit breaker "
                "uitgezet na opeenvolgende fouten \u2014 dat werk staat "
                "stil zonder dat iemand dat koos.",
                count=self.schedules_tripped,
            ))
        if self.claims_24h:
            status = "alarm"
            reasons.append(_(
                "%(count)s run(s) beweren werk zonder dat er \u00e9\u00e9n "
                "schrijfactie is vastgelegd.", count=self.claims_24h,
            ))
        if self.prompt_drift and status == "ok":
            status = "warn"
        if self.prompt_drift:
            reasons.append(_(
                "%(count)s planning(en) draaien een andere instructie dan "
                "hun registry-artikel \u2014 de gepubliceerde grens is niet "
                "de gebruikte grens.", count=self.prompt_drift,
            ))
        if self.registry_broken:
            reasons.append(_(
                "%(count)s registry-koppeling(en) wijzen naar een artikel "
                "of planning die niet meer bestaat.",
                count=self.registry_broken,
            ))
        if self.schedules_uncovered and status == "ok":
            status = "warn"
        if self.schedules_uncovered:
            reasons.append(_(
                "%(count)s actieve planning(en) staan niet in de "
                "promptregistry \u2014 hun instructie is nergens gepubliceerd "
                "en kan dus ook geen drift tonen.",
                count=self.schedules_uncovered,
            ))
        if self.registry_inactive or self.registry_orphans:
            reasons.append(_(
                "%(inactive)s koppeling(en) wijzen naar een uitgezette "
                "planning en %(orphans)s artikel(en) in de registry-map "
                "hebben geen koppeling.",
                inactive=self.registry_inactive,
                orphans=self.registry_orphans,
            ))
        if self.runs_failed_24h and status == "ok":
            status = "warn"
        if self.runs_failed_24h:
            reasons.append(_(
                "%(count)s run(s) zijn de afgelopen 24 uur mislukt.",
                count=self.runs_failed_24h,
            ))
        if self.failed_actions_24h and status == "ok":
            status = "warn"
        if self.failed_actions_24h:
            reasons.append(_(
                "%(count)s toolacties mislukten, ook in runs die als "
                "geslaagd zijn afgesloten.", count=self.failed_actions_24h,
            ))
        if not reasons:
            reasons.append(_("Geen bijzonderheden."))
        return status, reasons

    # ------------------------------------------------------------------
    @api.model
    def action_open(self):
        """Open het dashboard op een verse momentopname."""
        record = self.create({})
        return {
            "type": "ir.actions.act_window",
            "name": _("Gezondheid van het agentteam"),
            "res_model": self._name,
            "res_id": record.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_attention(self):
        self.ensure_one()
        since = fields.Datetime.now() - timedelta(hours=24)
        return {
            "type": "ir.actions.act_window",
            "name": _("Runs met aandacht (24 uur)"),
            "res_model": "daadit.ai.agent.schedule.run",
            "view_mode": "list,form",
            "domain": [("start_date", ">=", since),
                       ("needs_attention", "=", True)],
        }

    def action_view_tripped(self):
        self.ensure_one()
        # Een domein kan geen twee velden met elkaar vergelijken; de
        # drempel staat per planning, dus filteren gebeurt hier.
        tripped = self.env["daadit.ai.agent.schedule"].sudo().with_context(
            active_test=False,
        ).search([("active", "=", False)]).filtered(
            lambda s: s.failure_threshold > 0
            and s.consecutive_failures >= s.failure_threshold
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Uitgezet na opeenvolgende fouten"),
            "res_model": "daadit.ai.agent.schedule",
            "view_mode": "list,form",
            "domain": [("id", "in", tripped.ids)],
            "context": {"active_test": False},
        }

    def action_view_uncovered(self):
        """De actieve planningen zonder registry-artikel."""
        self.ensure_one()
        state = prompt_registry.coverage(self.env)
        return {
            "type": "ir.actions.act_window",
            "name": _("Planningen zonder registry-artikel"),
            "res_model": "daadit.ai.agent.schedule",
            "view_mode": "list,form",
            "domain": [
                ("id", "in", [s["id"] for s in state["uncovered"]]),
            ],
        }

    def action_coverage_report(self):
        """Het dekkingsrapport tonen — een voorstel, geen wijziging.

        Het rapport zegt per ontbrekend artikel wat erin zou moeten
        (agent, doel, bestemming, budget en de tekst die nu draait), zodat
        iemand het één keer vastlegt. Deze knop schrijft niets: niet in
        Knowledge, niet op de planning en niet in de koppeling.
        """
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Dekking van de promptregistry (dry-run)"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "view_id": self.env.ref(
                "daadit_ai_agent_schedule.view_daadit_ai_coverage_report",
            ).id,
            "target": "new",
        }

    def action_view_prompt_drift(self):
        """De planningen die een andere instructie draaien dan hun artikel.

        Een domein kan geen tekst van twee modellen vergelijken, dus de
        selectie gebeurt hier — zoals bij de circuit breaker hierboven.
        """
        self.ensure_one()
        rows = prompt_registry.drift_rows(prompt_registry.scan(self.env))
        return {
            "type": "ir.actions.act_window",
            "name": _("Planningen met drift op de registry"),
            "res_model": "daadit.ai.agent.schedule",
            "view_mode": "list,form",
            "domain": [("id", "in", [row["schedule"] for row in rows])],
            "context": {"active_test": False},
        }

    def action_view_overdue(self):
        self.ensure_one()
        cutoff = fields.Datetime.now() - timedelta(
            hours=_OVERDUE_GRACE_HOURS,
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Planningen die achterlopen"),
            "res_model": "daadit.ai.agent.schedule",
            "view_mode": "list,form",
            "domain": [("nextcall", "<", cutoff)],
        }

    def action_view_recent_runs(self):
        self.ensure_one()
        since = fields.Datetime.now() - timedelta(hours=24)
        return {
            "type": "ir.actions.act_window",
            "name": _("Runs van de afgelopen 24 uur"),
            "res_model": "daadit.ai.agent.schedule.run",
            "view_mode": "list,form",
            "domain": [("start_date", ">=", since)],
        }
