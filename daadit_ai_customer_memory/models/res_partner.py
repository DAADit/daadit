# Copyright 2026 DAADit
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0.html).
from odoo import _, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    ai_memory_opt_out = fields.Boolean(string="AI Memory Opt-out")
    ai_customer_memory_ids = fields.One2many(
        "daadit.ai.customer.memory",
        "partner_id",
        string="AI Customer Memory",
    )
    ai_customer_memory_count = fields.Integer(compute="_compute_ai_customer_memory_count")

    def _compute_ai_customer_memory_count(self):
        Memory = self.env["daadit.ai.customer.memory"]
        for partner in self:
            commercial = partner.commercial_partner_id or partner
            partner.ai_customer_memory_count = Memory.search_count([
                ("commercial_partner_id", "=", commercial.id),
                ("state", "=", "active"),
            ])

    def action_view_ai_customer_memory(self):
        self.ensure_one()
        commercial = self.commercial_partner_id or self
        return {
            "type": "ir.actions.act_window",
            "name": _("AI Customer Memory"),
            "res_model": "daadit.ai.customer.memory",
            "view_mode": "list,form",
            "domain": [("commercial_partner_id", "=", commercial.id)],
            "context": {"default_partner_id": self.id},
        }
