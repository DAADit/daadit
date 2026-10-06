# -*- coding: utf-8 -*-
from odoo import fields, models


class DaaditProcessPhase(models.Model):
    _name = "daadit.process.phase"
    _description = "Procesfase"
    _order = "sequence, id"

    process_id = fields.Many2one(
        "daadit.process", string="Proces", required=True,
        ondelete="cascade", index=True,
    )
    name = fields.Char(string="Fase", required=True)
    sequence = fields.Integer(default=10)
    step_ids = fields.One2many("daadit.process.step", "phase_id", string="Stappen")
