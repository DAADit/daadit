# -*- coding: utf-8 -*-
"""Route appointment bookings into the parent module's Teams meeting flow.

When an attendee books an appointment under an ``appointment.type`` whose
``event_videocall_source`` is ``teams``, the resulting ``calendar.event``
needs to be treated as a Teams videocall — even if the booking flow
didn't set ``use_teams_meeting`` directly. We extend ``_teams_should_create``
to consider the linked appointment-type as a positive signal, and the
parent module's create-hook does the actual Graph call.
"""
from odoo import models


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    def _teams_should_create(self, vals=None):
        self.ensure_one()
        # Hand off to the parent module first; if it already wants to
        # create a meeting, no need for our extra signal.
        if super()._teams_should_create(vals=vals):
            return True
        # Booked under an appointment type configured for Teams?
        appt_type = self.appointment_type_id
        if appt_type and appt_type.event_videocall_source == "teams":
            # The other guards (token, enabled, no existing meeting) still
            # need to hold; replicate the early-exit checks here so we
            # never create when the organiser is not connected.
            if self.ms_teams_meeting_id:
                return False
            organiser = self._teams_organiser().sudo()
            if not organiser.ms_teams_access_token:
                return False
            cfg = self.env["res.config.settings"].sudo().get_teams_config()
            if not cfg["enabled"]:
                return False
            return True
        return False
