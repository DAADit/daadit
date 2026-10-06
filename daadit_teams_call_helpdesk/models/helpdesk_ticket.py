# -*- coding: utf-8 -*-
"""Click-to-call from a helpdesk ticket. Reuses the phone normaliser +
Teams deeplink builder from the parent ``daadit_teams_call`` module so
the behaviour stays consistent across ``res.partner`` and
``helpdesk.ticket``."""
from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.daadit_teams_call.models.res_partner import (
    _teams_call_url,
    _teams_user_call_url,
)


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    def action_call_via_teams(self):
        """Open the Teams PSTN deeplink for the ticket's customer phone."""
        self.ensure_one()
        # Prefer the ticket's own partner_phone (the agent may have
        # corrected it on the ticket independently of the partner record),
        # then fall back to the partner's phone. Odoo 19 dropped
        # res.partner.mobile, so no mobile fallback.
        phone = (
            self.partner_phone
            or (self.partner_id.phone if self.partner_id else "")
        )
        url = _teams_call_url(phone)
        if not url:
            raise UserError(_(
                "No phone number on this ticket — fill in 'Customer Phone' "
                "or set a phone on the linked contact first."
            ))
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}

    def action_call_via_teams_email(self):
        """Open the Teams user-call deeplink for the ticket customer's
        email (no PSTN, routes via Teams)."""
        self.ensure_one()
        # helpdesk.ticket doesn't carry its own email field — the
        # customer's address always lives on res.partner. Read it from
        # the linked partner.
        email = self.partner_id.email if self.partner_id else ""
        url = _teams_user_call_url(email)
        if not url:
            raise UserError(_(
                "No valid customer email on this ticket — set one on the "
                "linked contact first."
            ))
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}
