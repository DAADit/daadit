# -*- coding: utf-8 -*-
from odoo import api, fields, models


class DaaditProcessPhase(models.Model):
    """Een fase is op het canvas één Odoo-app."""
    _inherit = "daadit.process.phase"

    app_module_id = fields.Many2one(
        "ir.module.module", string="Odoo-app",
        domain=[("application", "=", True), ("state", "=", "installed")],
        help="De Odoo-app waar deze fase zich afspeelt. Bepaalt welk icoon op "
             "het canvas getoond wordt.")
    # Naam, label en icoonpad worden opgeslagen. Zo leest de portal alleen dit
    # record en heeft een klantgebruiker geen leesrecht op ir.module.module
    # nodig -- dat heeft hij namelijk niet.
    app_technical_name = fields.Char(
        string="Technische naam", compute="_compute_app_fields", store=True)
    app_label = fields.Char(
        string="App", compute="_compute_app_fields", store=True)
    app_icon = fields.Char(
        string="Icoonpad", compute="_compute_app_fields", store=True,
        help="Pad naar het app-icoon. Dit pad is ook zonder aanmelden "
             "bereikbaar, dus het icoon hoeft nergens ingesloten te worden.")

    canvas_x = fields.Integer(string="Positie X", default=0)
    canvas_y = fields.Integer(string="Positie Y", default=0)

    @api.depends("app_module_id")
    def _compute_app_fields(self):
        for phase in self:
            # sudo: alleen interne gebruikers wijzigen de app-koppeling, maar een
            # herberekening mag niet stukloopen op leesrechten van de module-lijst.
            module = phase.app_module_id.sudo()
            phase.app_technical_name = module.name or False
            phase.app_label = (module.shortdesc or module.name) or False
            # Bewust zelf samengesteld in plaats van module.icon over te nemen:
            # dit pad geldt voor elke app en blijft gelijk over Odoo-versies.
            phase.app_icon = (
                "/%s/static/description/icon.png" % module.name
                if module.name else False)
