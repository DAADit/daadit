# -*- coding: utf-8 -*-
"""Add a Microsoft Teams option to the appointment-type videoconferencing
dropdown. ``appointment.type.event_videocall_source`` is a STORED selection
field in Odoo 19, so ``selection_add`` is safe here.
"""
from odoo import fields, models


class AppointmentType(models.Model):
    _inherit = "appointment.type"

    event_videocall_source = fields.Selection(
        selection_add=[("teams", "Microsoft Teams")],
        # "set null" rather than "set default" so this bridge's uninstall
        # path can't blow up if the parent field's default changes or
        # disappears in a future Odoo release. Records that were on
        # "teams" simply lose their videoconferencing source; the user
        # can re-pick whatever they want.
        ondelete={"teams": "set null"},
    )
