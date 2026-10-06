# -*- coding: utf-8 -*-
"""Een werkafspraak tussen één bedrijf en één collega.

De standaardopdracht van een collega geldt voor iedereen; deze regels
zijn wat één bedrijf anders wil. Ze staan apart, zodat een nieuwe
standaard ze niet overschrijft, en ze zijn per stuk aan en uit te zetten.
Wijzigingen worden in de chatter bijgehouden.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services import werkafspraken


class DaaditAiWerkafspraak(models.Model):
    _name = "daadit.ai.werkafspraak"
    _description = "Werkafspraak van een bedrijf met een AI-collega"
    _inherit = ["mail.thread"]
    _order = "agent_id, partner_id, id"

    agent_id = fields.Many2one(
        "ai.agent", string="Collega", required=True, ondelete="cascade",
        index=True, tracking=True,
    )
    partner_id = fields.Many2one(
        "res.partner", string="Bedrijf", required=True, ondelete="cascade",
        index=True, tracking=True,
        help="Het bedrijf waarvoor deze afspraak geldt.",
    )
    name = fields.Text(
        string="Afspraak", required=True, tracking=True,
        help="Wat de collega bij dit bedrijf anders doet dan zijn "
             "standaardwerkwijze, in gewone taal.",
    )
    active = fields.Boolean(string="Aan", default=True, tracking=True)
    origin = fields.Selection(
        [("chat", "Uit een gesprek"), ("beheer", "Ingevoerd")],
        string="Herkomst", default="beheer", required=True,
    )
    author_id = fields.Many2one(
        "res.partner", string="Gevraagd door", ondelete="set null",
    )
    author_name = fields.Char(string="Gevraagd door (naam)")
    source_message_id = fields.Many2one(
        "mail.message", string="Bericht", ondelete="set null",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "name" in vals:
                vals["name"] = werkafspraken.clean(vals["name"])
        return super().create(vals_list)

    def write(self, vals):
        if "name" in vals:
            vals["name"] = werkafspraken.clean(vals["name"])
        return super().write(vals)

    @api.constrains("name")
    def _check_name(self):
        for rec in self:
            if not (rec.name or "").strip():
                raise UserError(_("Een werkafspraak mag niet leeg zijn."))

    @api.model
    def _lopend(self, agent, partner):
        return self.sudo().search([
            ("agent_id", "=", agent.id), ("partner_id", "=", partner.id),
        ], order="id")

    @api.model
    def _vastleggen(self, agent, partner, tekst, message=None, author=None,
                    author_name=""):
        """Leg één afspraak vast; dezelfde tekst twee keer geeft één regel."""
        tekst = werkafspraken.clean(tekst)
        if not tekst:
            raise UserError(_("Zeg in één zin wat de afspraak is."))
        lopend = self._lopend(agent, partner)
        for rec in lopend:
            if rec.name.lower() == tekst.lower():
                return rec
        if len(lopend) >= werkafspraken.MAX_ACTIEF:
            raise UserError(_(
                "Er staan al %d werkafspraken voor dit bedrijf. Laat er "
                "eerst een uitzetten voordat er een bij komt."
            ) % werkafspraken.MAX_ACTIEF)
        return self.sudo().create({
            "agent_id": agent.id,
            "partner_id": partner.id,
            "name": tekst,
            "origin": "chat" if (message or author or author_name) else "beheer",
            "source_message_id": message.id if message else False,
            "author_id": author.id if author else False,
            "author_name": author_name or (author.name if author else ""),
        })

    @api.model
    def _render(self, agent, partner):
        rows = [
            (
                rec.id,
                rec.name,
                rec.create_date and rec.create_date.strftime("%d-%m-%Y") or "",
                rec.author_name or rec.author_id.name or "",
            )
            for rec in self._lopend(agent, partner)
        ]
        return werkafspraken.render_section(partner.display_name, rows)
