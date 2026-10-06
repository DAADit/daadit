# -*- coding: utf-8 -*-
"""Thin Microsoft Graph client.

Uses delegated permissions: each Odoo user authorises their own Microsoft
account once, and the resulting access / refresh tokens are stored on
``res.users``. All Graph calls below run as that user.

Scopes required (configured on the Azure AD app registration as Delegated
permissions, and requested again at consent time):

  - openid
  - profile
  - offline_access           (so we receive a refresh_token)
  - User.Read
  - OnlineMeetings.ReadWrite

If you later want full calendar sync as well, add:
  - Calendars.ReadWrite
"""
import logging
import random
import time
from datetime import timedelta

import requests

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DEFAULT_SCOPES = [
    "openid",
    "profile",
    "offline_access",
    "User.Read",
    "OnlineMeetings.ReadWrite",
]
TIMEOUT = 20

# Retry policy for transient Graph errors.
RETRY_STATUS = {429, 502, 503, 504}
MAX_RETRIES = 4
BASE_BACKOFF_S = 0.5  # seconds; doubles each retry, plus jitter


class MicrosoftGraph(models.AbstractModel):
    _name = "daadit_teams.graph"
    _description = "Microsoft Graph client (delegated auth)"

    # ------------------------------------------------------------------
    # Config / endpoints
    # ------------------------------------------------------------------
    def _config(self):
        cfg = self.env["res.config.settings"].get_teams_config()
        if not (cfg["tenant_id"] and cfg["client_id"] and cfg["client_secret"]):
            raise UserError(_(
                "Microsoft Teams integration is not fully configured. "
                "Set Tenant ID, Client ID and Client Secret in Settings."
            ))
        return cfg

    def _authority(self, tenant_id):
        return f"https://login.microsoftonline.com/{tenant_id}"

    def _token_endpoint(self, tenant_id):
        return f"{self._authority(tenant_id)}/oauth2/v2.0/token"

    def _authorize_endpoint(self, tenant_id):
        return f"{self._authority(tenant_id)}/oauth2/v2.0/authorize"

    def scopes(self):
        return list(DEFAULT_SCOPES)

    # ------------------------------------------------------------------
    # Auth-code exchange
    # ------------------------------------------------------------------
    def exchange_code(self, code, redirect_uri):
        cfg = self._config()
        data = {
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "scope": " ".join(self.scopes()),
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        resp = requests.post(
            self._token_endpoint(cfg["tenant_id"]),
            data=data,
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            _logger.error("MS Teams token exchange failed: %s %s", resp.status_code, resp.text)
            raise UserError(_("Microsoft sign-in failed: %s") % resp.text)
        return resp.json()

    def refresh_token_for(self, user):
        """Use the stored refresh_token to get a new access_token."""
        user = user.sudo()
        if not user.ms_teams_refresh_token:
            raise UserError(_("No refresh token available — please reconnect your Microsoft account."))
        cfg = self._config()
        data = {
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "scope": " ".join(self.scopes()),
            "refresh_token": user.ms_teams_refresh_token,
            "grant_type": "refresh_token",
        }
        resp = requests.post(
            self._token_endpoint(cfg["tenant_id"]),
            data=data,
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            _logger.warning(
                "MS Teams refresh failed for user %s: %s %s",
                user.id, resp.status_code, resp.text,
            )
            user._ms_teams_clear_tokens()
            raise UserError(_(
                "Could not refresh your Microsoft session — please reconnect Teams "
                "from your user preferences."
            ))
        payload = resp.json()
        user._ms_teams_store_tokens(payload)
        return payload["access_token"]

    def _ensure_token(self, user):
        user = user.sudo()
        if not user.ms_teams_access_token:
            raise UserError(_("Microsoft account is not connected for %s.") % user.name)
        now = fields.Datetime.now()
        if not user.ms_teams_token_expiry or user.ms_teams_token_expiry <= now + timedelta(seconds=30):
            return self.refresh_token_for(user)
        return user.ms_teams_access_token

    # ------------------------------------------------------------------
    # HTTP wrapper
    # ------------------------------------------------------------------
    def _request(self, user, method, path, *, json=None, params=None, _auth_retry=False):
        """Make an authenticated Graph request with:
        - One automatic token refresh on 401
        - Exponential backoff with jitter on 429/502/503/504, honouring
          any ``Retry-After`` header from Graph.
        """
        token = self._ensure_token(user)
        url = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        attempt = 0
        while True:
            resp = requests.request(
                method, url, headers=headers, json=json, params=params, timeout=TIMEOUT,
            )
            if resp.status_code == 401 and not _auth_retry:
                # Token may have just expired — refresh once and retry the
                # whole request (with its own retry budget).
                self.refresh_token_for(user)
                return self._request(
                    user, method, path, json=json, params=params, _auth_retry=True,
                )
            if resp.status_code in RETRY_STATUS and attempt < MAX_RETRIES:
                # Honour Retry-After when Graph supplies it; otherwise use
                # exponential backoff with a touch of jitter so concurrent
                # workers don't dogpile.
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 0
                except ValueError:
                    delay = 0
                if not delay:
                    delay = BASE_BACKOFF_S * (2 ** attempt) + random.uniform(0, 0.25)
                _logger.info(
                    "Graph %s %s returned %s, backing off %.2fs (attempt %d/%d)",
                    method, path, resp.status_code, delay, attempt + 1, MAX_RETRIES,
                )
                time.sleep(delay)
                # Refresh token header in case it rotated during the wait.
                headers["Authorization"] = f"Bearer {self._ensure_token(user)}"
                attempt += 1
                continue
            return resp

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_me(self, user):
        resp = self._request(user, "GET", "/me")
        resp.raise_for_status()
        return resp.json()

    def create_online_meeting(self, user, *, subject, start, end):
        """Create a Teams online meeting and return the join URL + id.

        ``start`` / ``end`` should be timezone-aware datetimes.
        """
        body = {
            "subject": subject or _("Meeting"),
            "startDateTime": start.isoformat(),
            "endDateTime": end.isoformat(),
        }
        resp = self._request(user, "POST", "/me/onlineMeetings", json=body)
        if resp.status_code not in (200, 201):
            _logger.error("Graph create onlineMeeting failed: %s %s", resp.status_code, resp.text)
            raise UserError(_("Could not create Teams meeting: %s") % resp.text)
        return resp.json()

    def update_online_meeting(self, user, meeting_id, *, subject=None, start=None, end=None):
        body = {}
        if subject is not None:
            body["subject"] = subject
        if start is not None:
            body["startDateTime"] = start.isoformat()
        if end is not None:
            body["endDateTime"] = end.isoformat()
        if not body:
            return None
        resp = self._request(user, "PATCH", f"/me/onlineMeetings/{meeting_id}", json=body)
        if resp.status_code not in (200, 204):
            _logger.warning("Graph patch onlineMeeting failed: %s %s", resp.status_code, resp.text)
        return resp

    def delete_online_meeting(self, user, meeting_id):
        resp = self._request(user, "DELETE", f"/me/onlineMeetings/{meeting_id}")
        if resp.status_code not in (200, 204, 404):
            _logger.warning("Graph delete onlineMeeting failed: %s %s", resp.status_code, resp.text)
        return resp
