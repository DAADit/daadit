# -*- coding: utf-8 -*-
from odoo import fields, models


class DaaditAiAgentScheduleRun(models.Model):
    _inherit = "daadit.ai.agent.schedule.run"

    trigger = fields.Selection(
        selection_add=[("automation", "Automatisering")],
        ondelete={"automation": "set default"},
    )
    automation_event_id = fields.Many2one(
        "daadit.agent.automation.event", string="Recordgebeurtenis",
        readonly=True, index=True, ondelete="set null",
    )


class DaaditAiAgentSchedule(models.Model):
    _inherit = "daadit.ai.agent.schedule"

    def _automation_event(self):
        Event = self.env["daadit.agent.automation.event"].sudo()
        event_id = self.env.context.get("daadit_automation_event_id")
        return Event.browse(event_id).exists() if event_id else Event

    def _run_vals(self, trigger):
        vals = super()._run_vals(trigger)
        event = self._automation_event()
        if event and trigger == "automation":
            vals["automation_event_id"] = event.id
        return vals

    def _run_prompt(self):
        prompt = super()._run_prompt()
        event = self._automation_event()
        if not event:
            return prompt
        parts = [
            event.prompt or "",
            self.env._(
                "Dit gaat over %(model)s „%(name)s” (model %(tech)s, "
                "id %(id)s). Je antwoord komt bij dat record te staan.",
                model=event.res_model_name or event.res_model,
                name=event.res_name or "",
                tech=event.res_model,
                id=event.res_id,
            ),
        ]
        if prompt.strip():
            parts.append(
                "%s\n%s" % (self.env._("Je vaste werkwijze:"), prompt)
            )
        return "\n\n".join(p for p in parts if p)
