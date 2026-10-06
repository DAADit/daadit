# -*- coding: utf-8 -*-
import logging
import secrets
from urllib.parse import urlencode, quote

import werkzeug

from odoo import _, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

_logger = logging.getLogger(__name__)


def _redirect(url):
    return werkzeug.utils.redirect(url, code=303)


def _redirect_with_message(path, status, message):
    """Redirect to ``path`` with ?teams_status=...&teams_message=... so
    feedback rides along on the URL without us having to render a custom
    QWeb template (which is fragile across Odoo versions)."""
    sep = "&" if "?" in path else "?"
    return _redirect(
        f"{path}{sep}teams_status={status}&teams_message={quote(message)}"
    )


class DaaditTeamsOAuth(http.Controller):

    @http.route("/daadit_teams/oauth/authorize", type="http", auth="user", website=False)
    def authorize(self, **kwargs):
        """Kick off the Microsoft authorization-code flow for the current user."""
        user = request.env.user
        target_user_id = int(kwargs.get("user_id") or user.id)
        if target_user_id != user.id and not user.has_group("base.group_system"):
            raise AccessError(_("You may only connect your own Microsoft account."))

        cfg = request.env["res.config.settings"].sudo().get_teams_config()
        if not (cfg["tenant_id"] and cfg["client_id"] and cfg["redirect_uri"]):
            return _redirect_with_message(
                "/odoo/settings",
                "error",
                _("Microsoft Teams integration is not configured. "
                  "Set tenant ID, client ID, secret and redirect URI."),
            )

        state = secrets.token_urlsafe(24)
        request.session["daadit_teams_oauth_state"] = state
        request.session["daadit_teams_oauth_user"] = target_user_id

        scopes = request.env["daadit_teams.graph"].sudo().scopes()
        params = {
            "client_id": cfg["client_id"],
            "response_type": "code",
            "redirect_uri": cfg["redirect_uri"],
            "response_mode": "query",
            "scope": " ".join(scopes),
            "state": state,
            "prompt": "select_account",
        }
        auth_url = (
            f"https://login.microsoftonline.com/{cfg['tenant_id']}/oauth2/v2.0/authorize"
            f"?{urlencode(params)}"
        )
        return _redirect(auth_url)

    @http.route("/daadit_teams/oauth/callback", type="http", auth="user", website=False, csrf=False)
    def callback(self, **kwargs):
        """Microsoft redirects here with ?code= and ?state=."""
        try:
            # Microsoft-side errors come back as ?error=...&error_description=...
            if kwargs.get("error"):
                msg = f"{kwargs.get('error')}: {kwargs.get('error_description') or ''}"
                _logger.warning("MS Teams OAuth callback error from Microsoft: %s", msg)
                return _redirect_with_message("/odoo", "error", msg)

            state = kwargs.get("state")
            expected = request.session.pop("daadit_teams_oauth_state", None)
            target_user_id = request.session.pop("daadit_teams_oauth_user", None)

            # State validation:
            # - Hard fail when state is present on both sides but differs.
            # - Soft path when session was lost (expected is None): we log a
            #   warning and continue, because ``auth=user`` already requires
            #   a logged-in Odoo session for this controller to even run.
            if expected and state != expected:
                _logger.warning(
                    "MS Teams OAuth state mismatch: got=%r expected=%r", state, expected
                )
                return _redirect_with_message(
                    "/odoo", "error", _("OAuth state mismatch — please retry."),
                )
            if not expected:
                _logger.info("MS Teams OAuth callback: session state missing, continuing")

            code = kwargs.get("code")
            if not code:
                return _redirect_with_message(
                    "/odoo", "error", _("Microsoft did not return an authorization code."),
                )

            user = request.env.user
            if target_user_id and target_user_id != user.id and not user.has_group("base.group_system"):
                return _redirect_with_message(
                    "/odoo", "error", _("You may only connect your own Microsoft account."),
                )
            target_user = request.env["res.users"].sudo().browse(target_user_id or user.id)
            if not target_user.exists():
                return _redirect_with_message(
                    "/odoo", "error", _("Target user not found."),
                )

            cfg = request.env["res.config.settings"].sudo().get_teams_config()
            graph = request.env["daadit_teams.graph"].sudo()

            try:
                payload = graph.exchange_code(code, cfg["redirect_uri"])
            except UserError as exc:
                _logger.warning("MS Teams token exchange failed: %s", exc)
                return _redirect_with_message(
                    "/odoo", "error",
                    exc.args[0] if exc.args else _("Token exchange failed."),
                )

            target_user._ms_teams_store_tokens(payload)

            # Best-effort UPN lookup; failure here must not block the flow.
            try:
                me = graph.get_me(target_user)
                target_user.write({
                    "ms_teams_account_upn": me.get("userPrincipalName") or me.get("mail"),
                })
            except Exception as exc:
                _logger.warning("Could not fetch /me after Teams connect: %s", exc)

            return _redirect_with_message(
                "/odoo/calendar",
                "ok",
                _("Microsoft Teams connected as %s") % (
                    target_user.ms_teams_account_upn or _("user")
                ),
            )

        except Exception as exc:  # noqa: BLE001
            # Last-resort safety net so a buggy callback never returns a bare 500.
            _logger.exception("MS Teams OAuth callback crashed: %s", exc)
            return _redirect_with_message(
                "/odoo", "error",
                _("Unexpected error during Microsoft sign-in (see server log)."),
            )
