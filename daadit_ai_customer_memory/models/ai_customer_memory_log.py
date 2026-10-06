# Copyright 2026 DAADit
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0.html).

from odoo import _, fields, models


class AiCustomerMemoryLog(models.Model):
    _name = "daadit.ai.customer.memory.log"
    _description = "AI Customer Memory Indexing Log"
    _order = "create_date desc, id desc"
    _rec_name = "display_name"

    display_name = fields.Char(compute="_compute_display_name", store=True)
    partner_id = fields.Many2one("res.partner", string="Customer", index=True, ondelete="set null")
    source_model = fields.Selection(
        selection=[("helpdesk.ticket", "Helpdesk Ticket"), ("project.task", "Project Task"), ("manual", "Manual")],
        string="Source Model",
        index=True,
    )
    source_res_id = fields.Integer(string="Source Record ID", index=True)
    ticket_id = fields.Many2one("helpdesk.ticket", string="Ticket", ondelete="set null")
    task_id = fields.Many2one("project.task", string="Task", ondelete="set null")
    state = fields.Selection(
        selection=[("success", "Success"), ("skipped", "Skipped"), ("error", "Error")],
        default="skipped",
        required=True,
        index=True,
    )
    agent_ref = fields.Char(string="Agent Ref")
    agent_name = fields.Char(string="Agent")
    message = fields.Char(string="Result Message")
    source_json = fields.Text(string="Source JSON Sent")
    prompt = fields.Text(string="Full Prompt Sent")
    raw_response = fields.Text(string="Raw Agent Response")
    parsed_response = fields.Text(string="Parsed Response")
    memory_ids = fields.One2many("daadit.ai.customer.memory", "log_id", string="Created Memories")

    def _compute_display_name(self):
        for rec in self:
            source = dict(self._fields["source_model"].selection).get(rec.source_model, rec.source_model or "Source")
            state = dict(self._fields["state"].selection).get(rec.state, rec.state or "")
            rec.display_name = "%s - %s - %s" % (rec.partner_id.display_name or "No customer", source, state)

    def action_open_source(self):
        self.ensure_one()
        if not self.source_model or self.source_model == "manual" or not self.source_res_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": self.source_model,
            "res_id": self.source_res_id,
            "view_mode": "form",
            "target": "current",
        }
