# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    process_tooling_active = fields.Boolean(
        string="Processtooling actief",
        help="Hoofdschakelaar: zet aan om de processen van deze klant zichtbaar "
             "te maken voor haar portalgebruikers onder 'Mijn account → Processen'.",
    )
    process_ids = fields.One2many(
        "daadit.process", "partner_id", string="Processen",
    )
    process_count = fields.Integer(
        string="Aantal processen", compute="_compute_process_count",
    )

    @api.depends("process_ids")
    def _compute_process_count(self):
        for partner in self:
            partner.process_count = len(partner.process_ids)

    def action_open_processes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Processen — %s" % self.name,
            "res_model": "daadit.process",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id, "default_is_template": False},
        }
