# -*- coding: utf-8 -*-
from odoo import api, fields, models

VALIDATION_STATES = [
    ("open", "Open"),
    ("akkoord", "Akkoord"),
    ("aandacht", "Aandachtspunt"),
    ("afkeur", "Afkeur"),
]


class DaaditProcessStep(models.Model):
    _name = "daadit.process.step"
    _description = "Processtap"
    _order = "sequence, id"

    phase_id = fields.Many2one(
        "daadit.process.phase", string="Fase", required=True,
        ondelete="cascade", index=True,
    )
    process_id = fields.Many2one(
        "daadit.process", string="Proces",
        related="phase_id.process_id", store=True, index=True,
    )
    name = fields.Char(string="Stap", required=True)
    sequence = fields.Integer(default=10)

    role = fields.Char(string="Rol")
    system = fields.Char(string="Systeem")
    input = fields.Char(string="Input")
    output = fields.Char(string="Output")
    description = fields.Html(string="Omschrijving")

    validation_state = fields.Selection(
        VALIDATION_STATES, string="Validatie", default="open", index=True,
    )
    validation_note = fields.Text(string="Opmerking")
    validated_by = fields.Many2one("res.users", string="Gevalideerd door", readonly=True)
    validated_on = fields.Datetime(string="Gevalideerd op", readonly=True)

    def set_validation(self, state, note=None):
        """Set the validation state + stamp who/when. Used by the portal."""
        self.ensure_one()
        if state not in dict(VALIDATION_STATES):
            state = "open"
        vals = {"validation_state": state}
        if note is not None:
            vals["validation_note"] = note
        if state and state != "open":
            vals["validated_by"] = self.env.uid
            vals["validated_on"] = fields.Datetime.now()
        else:
            vals["validated_by"] = False
            vals["validated_on"] = False
        self.write(vals)
        return True
