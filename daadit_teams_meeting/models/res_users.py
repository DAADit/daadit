# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = "res.users"

    ms_teams_access_token = fields.Char(
        string="MS Teams Access Token",
        groups="base.group_system",
        copy=False,
    )
    ms_teams_refresh_token = fields.Char(
        string="MS Teams Refresh Token",
        groups="base.group_system",
        copy=False,
    )
    ms_teams_token_expiry = fields.Datetime(
        string="MS Teams Token Expiry",
        groups="base.group_system",
        copy=False,
    )
    ms_teams_account_upn = fields.Char(
        string="Microsoft Account",
        readonly=True,
        copy=False,
        help="The userPrincipalName of the connected Microsoft account.",
    )
    ms_teams_use_by_default = fields.Boolean(
        string="Use Teams for New Meetings",
        default=True,
        help="When enabled, new calendar events created by this user will use "
             "Microsoft Teams as videoconferencing provider.",
    )

    # ------------------------------------------------------------------
    # SELF
    # ------------------------------------------------------------------
    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            "ms_teams_account_upn",
            "ms_teams_use_by_default",
        ]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + [
            "ms_teams_use_by_default",
        ]

    # ------------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------------
    def _ms_teams_clear_tokens(self):
        self.sudo().write({
            "ms_teams_access_token": False,
            "ms_teams_refresh_token": False,
            "ms_teams_token_expiry": False,
            "ms_teams_account_upn": False,
        })

    def _ms_teams_store_tokens(self, token_payload, upn=None):
        """Persist tokens returned by the Microsoft identity platform."""
        self.ensure_one()
        access = token_payload.get("access_token")
        refresh = token_payload.get("refresh_token")
        expires_in = int(token_payload.get("expires_in", 3600))
        # Refresh a minute before actual expiry
        expiry = fields.Datetime.now() + timedelta(seconds=max(expires_in - 60, 60))
        vals = {
            "ms_teams_access_token": access,
            "ms_teams_token_expiry": expiry,
        }
        if refresh:
            vals["ms_teams_refresh_token"] = refresh
        if upn:
            vals["ms_teams_account_upn"] = upn
        self.sudo().write(vals)

    def action_ms_teams_connect(self):
        """Button on the user preferences -> redirect to OAuth start."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/daadit_teams/oauth/authorize?user_id={self.id}",
            "target": "self",
        }

    def action_ms_teams_disconnect(self):
        self.ensure_one()
        self._ms_teams_clear_tokens()
        return True

    @api.model
    def _cron_refresh_ms_teams_tokens(self):
        """Refresh tokens that will expire in the next 30 minutes."""
        soon = fields.Datetime.now() + timedelta(minutes=30)
        users = self.sudo().search([
            ("ms_teams_refresh_token", "!=", False),
            ("ms_teams_token_expiry", "<=", soon),
        ])
        graph = self.env["daadit_teams.graph"].sudo()
        for user in users:
            try:
                graph.refresh_token_for(user)
            except Exception as exc:
                _logger.warning(
                    "Background refresh failed for user %s: %s", user.id, exc
                )
