# -*- coding: utf-8 -*-
"""Expose schedules on the AI Agent form (smart button + count)."""
from odoo import api, fields, models, _
from odoo.exceptions import UserError

from ..services import werkafspraken


class AiAgent(models.Model):
    _inherit = "ai.agent"

    schedule_ids = fields.One2many(
        "daadit.ai.agent.schedule", "agent_id", string="Schedules",
        # v19.0.2.5.0: archived schedules (incl. ones the circuit
        # breaker switched off) stay visible in the agent's Schedules
        # tab — otherwise a tripped breaker looks like a vanished
        # schedule.
        context={"active_test": False},
    )
    schedule_count = fields.Integer(
        string="# Schedules", compute="_compute_schedule_count",
    )
    # v19.0.2.0.0: expose all runs across all schedules for this agent
    # on the agent form itself — one click to the full audit log instead
    # of having to drill through the Schedules smart button first.
    run_ids = fields.One2many(
        "daadit.ai.agent.schedule.run", "agent_id", string="Runs",
    )
    run_count = fields.Integer(
        string="# Runs", compute="_compute_run_count",
    )
    # Dagbudget per agent. De bestaande grens
    # (daadit_ai_mistral.daily_cost_cap_usd) geldt voor alles samen,
    # waardoor één doorgeslagen agent het budget van alle andere kan
    # opeten voordat iemand het merkt. Een eigen grens houdt de schade
    # lokaal. Nul = geen eigen grens; dan gelden alleen de
    # planning-grens en de systeemgrens.
    daadit_daily_cost_cap_eur = fields.Float(
        string="Dagbudget (EUR)", default=0.0,
        help="Maximale kosten per dag voor alle runs van deze agent "
             "samen, inclusief chatbeurten. 0 = geen eigen grens; dan "
             "geldt de standaard uit de instellingen.",
    )
    # Een dagbudget alleen laat een agent nog steeds twintig dagen op
    # 95% draaien; de marge op €599 per maand overleeft dat niet. De
    # maandgrens is daarom de grens die de verkoopprijs bewaakt, de
    # daggrens beperkt de schade van één doorgeslagen dag.
    daadit_monthly_cost_cap_eur = fields.Float(
        string="Maandbudget (EUR)", default=0.0,
        help="Maximale kosten per kalendermaand voor alle runs van deze "
             "agent samen, inclusief chatbeurten. 0 = geen eigen grens; "
             "dan geldt de standaard uit de instellingen.",
    )

    # ------------------------------------------------------------------
    # Reparatiebereik (taak 727)
    # ------------------------------------------------------------------
    daadit_repair_scope_ids = fields.One2many(
        "daadit.ai.agent.repair.scope", "agent_id",
        string="Mag deze collega's repareren",
        help="Expliciete grens voor autonome fixes. Leeg = deze poort "
             "geldt niet voor deze agent (de stand van vandaag); zodra "
             "er één regel staat, is de lijst uitputtend.",
    )
    daadit_werkafspraak_ids = fields.One2many(
        "daadit.ai.werkafspraak", "agent_id", string="Werkafspraken",
        context={"active_test": False},
        help="Wat een bedrijf deze collega anders laat doen dan zijn "
             "standaardopdracht. Staat los van de opdracht zelf, dus een "
             "nieuwe standaard overschrijft deze afspraken niet.",
    )
    daadit_repair_target_count = fields.Integer(
        string="# Repareerbare collega's",
        compute="_compute_daadit_repair_target_count",
    )

    @api.depends("daadit_repair_scope_ids",
                 "daadit_repair_scope_ids.effective")
    def _compute_daadit_repair_target_count(self):
        for rec in self:
            rec.daadit_repair_target_count = len(
                rec.daadit_repair_scope_ids.filtered("effective")
            )

    def daadit_repair_targets(self):
        """De collega's die deze agent autonoom mag repareren."""
        self.ensure_one()
        lines = self.sudo().daadit_repair_scope_ids.filtered("effective")
        return lines.mapped("target_agent_id")

    def daadit_may_repair(self, target_agent=None, article_ref=0):
        """Mag deze agent aan die collega werken?

        Levert ``(toegestaan, reden)``. De poort staat open zolang er
        geen enkele regel is: dan is er niets ingesteld en verandert dit
        veld niets aan de huidige stand. Staat er wél een regel, dan is
        die lijst uitputtend en wordt de rest geweigerd — verruimen doe
        je per agent, met een naam erbij.
        """
        self.ensure_one()
        lines = self.sudo().daadit_repair_scope_ids.filtered("effective")
        if not lines:
            return True, ""
        if target_agent and target_agent.id == self.id:
            return False, _(
                "Een agent repareert zijn eigen prompt niet."
            )
        if target_agent and target_agent in lines.mapped("target_agent_id"):
            return True, ""
        if article_ref and article_ref in lines.mapped("article_ref"):
            return True, ""
        allowed = ", ".join(
            sorted(lines.mapped("target_agent_id.display_name"))
        )
        return False, _(
            "Buiten het reparatiebereik van %(agent)s. Toegestaan: "
            "%(allowed)s. Een collega toevoegen is een menselijke "
            "handeling: zet er een regel bij op de agentkaart.",
            agent=self.display_name or "?", allowed=allowed or _("(geen)"),
        )

    def daadit_budget_state(self):
        """Budgetstand van deze agent, voor planner én chat.

        Geeft een dict terug met ``ratio`` (het hoogste van dag- en
        maandverbruik als fractie van zijn grens), ``blocked`` en een
        Nederlandse ``message``. De chatlaag gebruikt dit om te
        waarschuwen of de tools weg te laten; de planner om een run niet
        te starten. Eén bron, zodat chat en planning niet uiteenlopen.
        """
        self.ensure_one()
        Schedule = self.env["daadit.ai.agent.schedule"]
        return Schedule._agent_budget_state(self)

    @api.depends("schedule_ids")
    def _compute_schedule_count(self):
        # active_test=False: count matches the tab (archived included).
        data = self.env["daadit.ai.agent.schedule"].with_context(
            active_test=False,
        )._read_group(
            [("agent_id", "in", self.ids)],
            groupby=["agent_id"], aggregates=["__count"],
        )
        counts = {agent.id: cnt for agent, cnt in data}
        for rec in self:
            rec.schedule_count = counts.get(rec.id, 0)

    @api.depends("run_ids")
    def _compute_run_count(self):
        data = self.env["daadit.ai.agent.schedule.run"]._read_group(
            [("agent_id", "in", self.ids)],
            groupby=["agent_id"], aggregates=["__count"],
        )
        counts = {agent.id: cnt for agent, cnt in data}
        for rec in self:
            rec.run_count = counts.get(rec.id, 0)

    def action_view_schedules(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Schedules — %s") % self.name,
            "res_model": "daadit.ai.agent.schedule",
            "view_mode": "list,form",
            "domain": [("agent_id", "=", self.id)],
            "context": {
                "default_agent_id": self.id,
                "default_name": _("Schedule — %s") % self.name,
            },
        }

    def action_view_runs(self):
        """Open the full run-log for this agent across every schedule.

        Landing directly on the runs (findings + tool actions + tokens)
        is the most common triage entry point when something looks
        wrong: "what did the agent actually do in the last N runs?"
        """
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Runs — %s") % self.name,
            "res_model": "daadit.ai.agent.schedule.run",
            "view_mode": "list,form",
            "domain": [("agent_id", "=", self.id)],
            "context": {},
        }

    # ------------------------------------------------------------------
    # Werkafspraken per bedrijf
    # ------------------------------------------------------------------
    def _daadit_werkafspraak_scope(self):
        """De collega en het bedrijf waarvoor hij op dit moment werkt.

        Hier is dat het eigen bedrijf; modules die één collega voor meer
        klanten laten werken, geven de klant van de run terug.
        """
        self.ensure_one()
        return self, self.env.company.partner_id

    def _daadit_werkafspraken_section(self):
        self.ensure_one()
        agent, partner = self._daadit_werkafspraak_scope()
        if not partner:
            return ""
        return self.env["daadit.ai.werkafspraak"]._render(agent, partner)

    def _daadit_apply_werkafspraken(self, prompt):
        """``prompt`` met het blok werkafspraken erachter."""
        self.ensure_one()
        return werkafspraken.apply_section(
            prompt, self._daadit_werkafspraken_section(),
        )

    def _ai_tool_werkafspraak_vastleggen(self, afspraak=None, **_extra):
        self.ensure_one()
        agent, partner = self._daadit_werkafspraak_scope()
        if not partner:
            return {"ok": False, "error": _(
                "Ik werk nu niet voor een bedrijf, dus er is niemand om "
                "een afspraak mee te maken."
            )}
        ctx = self.env.context
        message = self.env["mail.message"].sudo().browse(
            ctx.get("daadit_werkafspraak_message_id") or [],
        ).exists()
        author = message.author_id if message else self.env["res.partner"]
        try:
            rec = self.env["daadit.ai.werkafspraak"]._vastleggen(
                agent, partner, afspraak, message=message or None,
                author=author or None,
                author_name=ctx.get("daadit_werkafspraak_author_name") or "",
            )
        except UserError as exc:
            return {"ok": False, "error": str(exc.args[0] if exc.args else exc)}
        return {
            "ok": True,
            "nummer": rec.id,
            "afspraak": rec.name,
            "zeg_tegen_gebruiker": _(
                "Afgesproken, dit onthoud ik voor %(bedrijf)s: %(wat)s"
            ) % {"bedrijf": partner.display_name, "wat": rec.name},
        }

    def _ai_tool_werkafspraak_intrekken(self, nummer=None, **_extra):
        self.ensure_one()
        agent, partner = self._daadit_werkafspraak_scope()
        try:
            wanted = int(str(nummer or "").strip().lstrip("#"))
        except ValueError:
            return {"ok": False, "error": _(
                "Noem het nummer van de afspraak, zoals #12."
            )}
        rec = self.env["daadit.ai.werkafspraak"]._lopend(
            agent, partner,
        ).filtered(lambda r: r.id == wanted)
        if not rec:
            return {"ok": False, "error": _(
                "Afspraak #%d staat niet bij dit bedrijf."
            ) % wanted}
        rec.active = False
        return {
            "ok": True,
            "nummer": rec.id,
            "zeg_tegen_gebruiker": _(
                "Afspraak #%(nr)d staat uit: %(wat)s"
            ) % {"nr": rec.id, "wat": rec.name},
        }

    @api.model
    def _daadit_attach_werkafspraak_topic(self):
        """Elke collega kan een werkafspraak vastleggen en intrekken."""
        topic = self.env.ref(
            "daadit_ai_agent_schedule.ai_topic_werkafspraken",
            raise_if_not_found=False,
        )
        if not topic:
            return
        agents = self.sudo().with_context(active_test=False).search([
            ("topic_ids", "not in", topic.ids),
        ])
        if agents:
            agents.write({"topic_ids": [(4, topic.id)]})
