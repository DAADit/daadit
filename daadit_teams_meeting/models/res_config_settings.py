# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# System parameter keys
PARAM_TENANT_ID = "daadit_teams_meeting.tenant_id"
PARAM_CLIENT_ID = "daadit_teams_meeting.client_id"
PARAM_CLIENT_SECRET = "daadit_teams_meeting.client_secret"
PARAM_REDIRECT_URI = "daadit_teams_meeting.redirect_uri"
PARAM_ENABLED = "daadit_teams_meeting.enabled"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ms_teams_enabled = fields.Boolean(
        string="Enable Microsoft Teams Meetings",
        config_parameter=PARAM_ENABLED,
        help="When enabled, calendar events can use Microsoft Teams as their videoconferencing provider.",
    )
    ms_teams_tenant_id = fields.Char(
        string="Azure AD Tenant ID",
        config_parameter=PARAM_TENANT_ID,
        help="The Microsoft Entra (Azure AD) tenant ID for the registered application.",
    )
    ms_teams_client_id = fields.Char(
        string="Application (Client) ID",
        config_parameter=PARAM_CLIENT_ID,
    )
    ms_teams_client_secret = fields.Char(
        string="Client Secret",
        config_parameter=PARAM_CLIENT_SECRET,
        help="Client secret value (not the secret ID). Treat as sensitive.",
    )
    ms_teams_redirect_uri = fields.Char(
        string="Redirect URI",
        config_parameter=PARAM_REDIRECT_URI,
        help="Must exactly match the redirect URI configured on the Azure AD app registration. "
             "Typically https://<your-odoo-host>/daadit_teams/oauth/callback",
    )

    @api.model
    def get_teams_config(self):
        """Helper for runtime config reads."""
        get = self.env["ir.config_parameter"].sudo().get_param
        return {
            "enabled": get(PARAM_ENABLED, "False") == "True",
            "tenant_id": (get(PARAM_TENANT_ID) or "").strip(),
            "client_id": (get(PARAM_CLIENT_ID) or "").strip(),
            "client_secret": (get(PARAM_CLIENT_SECRET) or "").strip(),
            "redirect_uri": (get(PARAM_REDIRECT_URI) or "").strip(),
        }

    def action_test_teams_connection(self):
        """Diagnostic — verifies that the configured Azure AD app credentials
        are valid and that the current user's Microsoft token works against
        Graph. Shows a notification with the result.

        Two-stage check:
        1. Tenant credentials reachable (client_credentials token endpoint)
        2. Current user's delegated token can call /me
        """
        self.ensure_one()
        cfg = self.env["res.config.settings"].sudo().get_teams_config()
        missing = [k for k in ("tenant_id", "client_id", "client_secret", "redirect_uri")
                   if not cfg.get(k)]
        if missing:
            return self._teams_notif(
                "warning",
                _("Configuration incomplete"),
                _("Missing field(s): %s") % ", ".join(missing),
            )

        # Stage 1 — credential check via client_credentials grant. This
        # validates tenant_id + client_id + secret without needing any user
        # to be connected yet.
        import requests
        try:
            resp = requests.post(
                f"https://login.microsoftonline.com/{cfg['tenant_id']}/oauth2/v2.0/token",
                data={
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                },
                timeout=15,
            )
        except Exception as exc:
            _logger.exception("Teams test connection failed at credential stage")
            return self._teams_notif("danger", _("Connection failed"), str(exc))
        if resp.status_code != 200:
            return self._teams_notif(
                "danger",
                _("Tenant credentials rejected"),
                _("Azure AD responded with %s: %s") % (resp.status_code, resp.text[:300]),
            )

        # Stage 2 — current user's delegated token reaches /me
        user = self.env.user
        if not user.ms_teams_access_token:
            return self._teams_notif(
                "warning",
                _("Tenant OK, user not connected"),
                _("Credentials are valid. Connect your Microsoft account "
                  "from User Preferences to complete the check."),
            )
        graph = self.env["daadit_teams.graph"].sudo()
        try:
            me = graph.get_me(user)
        except Exception as exc:
            _logger.exception("Teams test connection failed at /me stage")
            return self._teams_notif(
                "danger",
                _("Delegated token check failed"),
                str(exc),
            )
        upn = me.get("userPrincipalName") or me.get("mail") or "(no UPN)"
        return self._teams_notif(
            "success",
            _("Microsoft Teams connection OK"),
            _("Tenant credentials valid; /me returned %s.") % upn,
        )

    def _teams_notif(self, kind, title, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": kind,
                "title": title,
                "message": message,
                "sticky": kind in ("danger", "warning"),
            },
        }
