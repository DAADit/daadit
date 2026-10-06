# Copyright 2026 DAADit
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0.html).
from odoo import _, fields, models
from odoo.tools import html2plaintext


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    ai_customer_memory_ids = fields.One2many(
        "daadit.ai.customer.memory",
        "ticket_id",
        string="AI Customer Memory",
    )
    ai_customer_memory_count = fields.Integer(compute="_compute_ai_customer_memory_count")
    ai_memory_opt_out = fields.Boolean(
        string="AI Memory Opt-out",
        related="partner_id.commercial_partner_id.ai_memory_opt_out",
        readonly=True,
    )

    def _compute_ai_customer_memory_count(self):
        for ticket in self:
            ticket.ai_customer_memory_count = len(ticket.ai_customer_memory_ids)

    def action_index_ai_customer_memory(self):
        for ticket in self:
            self.env["daadit.ai.customer.memory"].index_source_record(ticket, force=True)
        return True

    def action_view_ai_customer_memory(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("AI Customer Memory"),
            "res_model": "daadit.ai.customer.memory",
            "view_mode": "list,form",
            "domain": [("ticket_id", "=", self.id)],
            "context": {
                "default_ticket_id": self.id,
                "default_partner_id": self.partner_id.id if self.partner_id else False,
                "default_source_model": "helpdesk.ticket",
                "default_source_res_id": self.id,
            },
        }

    def _prepare_ai_memory_source_values(self):
        self.ensure_one()
        chatter = []
        messages = self.message_ids.sorted("date")[-20:]
        for message in messages:
            body = html2plaintext(message.body or "").strip()
            if body:
                chatter.append({
                    "date": fields.Datetime.to_string(message.date) if message.date else "",
                    "author": message.author_id.display_name if message.author_id else "",
                    "body": body[:1500],
                })
        return {
            "source_model": self._name,
            "source_res_id": self.id,
            "title": self.name or "",
            "description": html2plaintext(self.description or "").strip(),
            "partner_id": self.partner_id.id if self.partner_id else False,
            "partner_name": self.partner_id.display_name if self.partner_id else "",
            "commercial_partner_name": self.partner_id.commercial_partner_id.display_name if self.partner_id else "",
            "stage": self.stage_id.display_name if self.stage_id else "",
            "team": self.team_id.display_name if self.team_id else "",
            "assigned_user": self.user_id.display_name if self.user_id else "",
            "chatter": chatter,
        }
