# -*- coding: utf-8 -*-
from odoo import fields, models


class ProjectTask(models.Model):
    _inherit = "project.task"

    daadit_process_step_id = fields.Many2one(
        "daadit.process.step", string="Uit handeling", index=True,
        ondelete="set null", copy=False,
        help="De handeling in het procescanvas waar deze taak uit voortkomt. "
             "Blijft staan als de handeling verdwijnt, zodat het werk niet "
             "zomaar uit het project valt.")
    daadit_process_id = fields.Many2one(
        "daadit.process", string="Uit proces", store=True, index=True,
        related="daadit_process_step_id.process_id",
        help="Opgeslagen zodat je in het project kunt filteren op het proces "
             "waar de bevinding vandaan komt.")
