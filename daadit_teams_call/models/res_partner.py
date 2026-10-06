# -*- coding: utf-8 -*-
"""Click-to-call via Microsoft Teams deeplinks.

Standalone telephony helper — does NOT require Odoo's ``voip`` app and
does NOT depend on the calendar / meeting integration.

Two flavours of call:

* PSTN dial — Teams Phone places a call to a phone number:

      https://teams.microsoft.com/l/call/0/0?users=4:+31201234567

  With a Teams Phone licence the call is placed directly; without it the
  link still opens Teams and the user can choose how to handle the call.

* Teams-user call — Teams calls another Teams user by email / UPN. No
  PSTN cost, no caller-id policy quirks (the call routes purely through
  Teams). Works for users in your tenant and federated external Teams
  contacts:

      https://teams.microsoft.com/l/call/0/0?users=jan@daadit.group

  Note the absence of the ``4:`` prefix — that prefix is reserved for
  PSTN dial strings.
"""
import logging
import re

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _normalize_phone(raw):
    """Strip whitespace, parens, dashes; preserve a leading + so Teams
    treats the number as E.164. Convert a leading ``00`` to ``+`` since
    Dutch users in particular often write ``0031`` instead of ``+31``."""
    if not raw:
        return ""
    cleaned = re.sub(r"[^\d+]", "", raw)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    return cleaned


def _teams_call_url(phone):
    """Build the Teams PSTN click-to-call deeplink. Returns False if the
    input can't be turned into a usable phone number."""
    phone = _normalize_phone(phone)
    if not phone:
        return False
    # Reference: Microsoft Teams deep-link documentation
    # https://learn.microsoft.com/en-us/microsoftteams/platform/concepts/deep-links
    return f"https://teams.microsoft.com/l/call/0/0?users=4:{phone}"


# Loose UPN-ish validation. We deliberately don't import full RFC-5322
# regexery — Teams will resolve the address server-side and politely
# complain if it isn't a known Teams user. We just guard against obvious
# garbage (whitespace, empty string, no @).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _teams_user_call_url(email):
    """Build the Teams user-call deeplink (calls another Teams user by
    email / UPN). Returns False if the input doesn't look like an email
    address at all."""
    if not email:
        return False
    email = email.strip()
    if not _EMAIL_RE.match(email):
        return False
    return f"https://teams.microsoft.com/l/call/0/0?users={email}"


class ResPartner(models.Model):
    _inherit = "res.partner"

    def action_call_via_teams(self):
        """PSTN-dial the partner's phone number via Teams."""
        self.ensure_one()
        # Odoo 19 dropped res.partner.mobile — phone is the only standard
        # field we can rely on. Customers who still need a separate mobile
        # column have to surface it via a custom field; the Teams deeplink
        # doesn't care which field the number came from.
        url = _teams_call_url(self.phone)
        if not url:
            raise UserError(_(
                "No phone number set for %s — fill one in first."
            ) % self.display_name)
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}

    def action_call_via_teams_email(self):
        """Call the partner as a Teams user (by email / UPN). Routes
        through Teams without using PSTN — works for internal users and
        federated external Teams contacts."""
        self.ensure_one()
        url = _teams_user_call_url(self.email)
        if not url:
            raise UserError(_(
                "No valid email address set for %s — fill one in first."
            ) % self.display_name)
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}
