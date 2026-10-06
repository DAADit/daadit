# -*- coding: utf-8 -*-
"""Click-to-call from a CRM lead / opportunity. Sales reps live in the
pipeline view; this lets them dial a prospect without first navigating
to the linked contact record.

Reuses ``_teams_call_url`` from the core ``daadit_teams_call`` module so
the deeplink format and phone normalisation are identical across every
Teams-call surface (partner, helpdesk, crm)."""
from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.daadit_teams_call.models.res_partner import (
    _teams_call_url,
    _teams_user_call_url,
)


class CrmLead(models.Model):
    _inherit = "crm.lead"

    def action_call_via_teams(self):
        """Open the Teams PSTN deeplink for the lead's phone."""
        self.ensure_one()
        # Odoo 19's crm.lead only has `phone`. Sales reps may type a
        # temporary number on the lead before it's promoted to a full
        # partner, so prefer the lead's own phone over partner_id.phone.
        phone = self.phone or (self.partner_id.phone if self.partner_id else "")
        url = _teams_call_url(phone)
        if not url:
            raise UserError(_(
                "No phone number on this lead — fill one in first."
            ))
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}

    def action_call_via_teams_email(self):
        """Open the Teams user-call deeplink for the lead's email/UPN."""
        self.ensure_one()
        # email_from is the canonical email field on crm.lead; fall back
        # to the linked partner's email if the lead doesn't have one yet.
        email = self.email_from or (self.partner_id.email if self.partner_id else "")
        url = _teams_user_call_url(email)
        if not url:
            raise UserError(_(
                "No valid email address on this lead — fill one in first."
            ))
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}
