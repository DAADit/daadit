# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class IrActionsServer(models.Model):
    _inherit = "ir.actions.server"

    state = fields.Selection(
        selection_add=[("daadit_agent", "AI-collega inschakelen")],
        ondelete={"daadit_agent": "cascade"},
    )
    daadit_schedule_id = fields.Many2one(
        "daadit.ai.agent.schedule", string="Collega en werkwijze",
        ondelete="restrict",
        help="De planning bepaalt welke collega het werk doet, met welke "
             "tools, leesscope, gebruiker en budget. Zet de planning op "
             "inactief als zij alleen op gebeurtenissen moet reageren.",
    )
    daadit_agent_id = fields.Many2one(
        related="daadit_schedule_id.agent_id", string="AI-collega",
    )
    daadit_prompt_template = fields.Text(
        string="Opdracht",
        help="Wat de collega met het record moet doen. Recordvelden "
             "zoals bij e-mailsjablonen: {{ object.name }}.",
    )
    daadit_run_mode = fields.Selection(
        [("direct", "Direct"), ("queue", "Via de wachtrij")],
        string="Uitvoering", default="queue",
        help="Direct: de wachtrij wordt meteen aangezet. Via de wachtrij: "
             "bij de volgende ronde (elke paar minuten). Het opslaan van "
             "het record wacht in beide gevallen niet op de collega.",
    )
    daadit_throttle_minutes = fields.Integer(
        string="Hoogstens één keer per (minuten)", default=60,
    )
    daadit_outcome = fields.Selection(
        [("note", "Notitie in de chatter"),
         ("activity", "Activiteit voor de verantwoordelijke")],
        string="Uitkomst", default="note",
    )

    @api.constrains(
        "state", "daadit_schedule_id", "daadit_prompt_template",
        "daadit_throttle_minutes",
    )
    def _check_daadit_agent(self):
        for action in self.filtered(lambda a: a.state == "daadit_agent"):
            if not action.daadit_schedule_id:
                raise ValidationError(_("Kies welke collega dit oppakt."))
            if not (action.daadit_prompt_template or "").strip():
                raise ValidationError(_("Schrijf wat de collega moet doen."))
            if action.daadit_throttle_minutes < 1:
                raise ValidationError(_(
                    "Hoogstens één keer per … moet minstens 1 minuut zijn."
                ))

    def _run_action_daadit_agent_multi(self, eval_context=None):
        records = (eval_context or {}).get("records")
        if not records:
            return False
        events = self.env["daadit.agent.automation.event"].sudo()._enqueue(
            self, records,
        )
        if events and self.daadit_run_mode == "direct":
            self.env.ref(
                "daadit_agent_automation.ir_cron_agent_automation"
            ).sudo()._trigger()
        return False
