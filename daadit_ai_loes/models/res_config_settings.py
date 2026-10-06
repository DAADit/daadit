# -*- coding: utf-8 -*-
"""Settings UI for the Loes provider.

Three configuration knobs land in ``ir.config_parameter``:

* ``daadit_ai_loes.loes_key_enabled`` — master toggle
* ``daadit_ai_loes.loes_key``         — API key (group_system only)
* ``daadit_ai_loes.loes_base_url``    — endpoint override
* ``daadit_ai_loes.loes_timeout``     — request timeout in seconds

Security note (v19.0.3.11.0)
----------------------------
``loes_base_url`` is **strictly validated** at save time. The Bearer
token is sent on every request, so a tampered URL would leak the key to
any host an admin (or a code path with ICP-write rights) could set. We
therefore require:

  * scheme = ``https://``
  * host in a small allowlist of known Loes endpoints

This blocks the most direct SSRF + key-exfiltration vector. Admins who
need a custom proxy can extend the allowlist via the
``daadit_ai_loes.allowed_base_url_hosts`` ICP (comma-separated).

``loes_timeout`` is constrained to a minimum of 1 second so a
mis-saved 0 doesn't hang an Odoo worker indefinitely on a network
stall.
"""
from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


# Loes is served via the HostYourAI EU-router (hostyourai.com/#router):
# OpenAI-compatible endpoints under /api/v1, bearer keys prefixed
# ``hyai-``. Endpoint confirmed by the user on 2026-07-29.
LOES_DEFAULT_BASE_URL = "https://hostyourai.com/api/v1"

# Hosts we accept by default. Anything else has to be opted-in via the
# ``daadit_ai_loes.allowed_base_url_hosts`` ICP (comma-separated).
# Public Loes/HostYourAI hosts only — no localhost, no internal ranges.
_DEFAULT_ALLOWED_HOSTS = (
    "hostyourai.com",
    "api.loes.ai",
)

# Minimum HTTP timeout. 0 = "no timeout" in requests, which can wedge a
# worker forever on a stalled connection.
_MIN_TIMEOUT_SECONDS = 1
_MAX_TIMEOUT_SECONDS = 600  # 10 min — generous, anything more is a bug.


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    loes_key_enabled = fields.Boolean(
        string="Enable custom Loes API key",
        config_parameter="daadit_ai_loes.loes_key_enabled",
        help=(
            "When enabled, AI agents configured to use a Loes model will "
            "call the HostYourAI EU-router directly using the key below "
            "instead of going through Odoo's IAP. You'll be billed by "
            "HostYourAI (prepaid balance, hostyourai.com)."
        ),
    )
    loes_key = fields.Char(
        string="Loes API key",
        config_parameter="daadit_ai_loes.loes_key",
        help=(
            "Personal HostYourAI API key (hyai-…). Stored in "
            "ir.config_parameter — only visible to users with the Settings "
            "right (group_system). The key is sent as Authorization: Bearer "
            "to the URL configured below; that URL is validated against an "
            "allowlist so a tampered URL cannot exfiltrate the key."
        ),
    )
    loes_key_batch = fields.Char(
        string="Loes API-key voor geplande agents (batch)",
        config_parameter="daadit_ai_loes.loes_key_batch",
        help=(
            "Optionele tweede API-key die uitsluitend door geplande "
            "agent-runs (daadit_ai_agent_schedule) wordt gebruikt. "
            "Hiermee is batch-verkeer qua rate-limit gescheiden van de "
            "klant-facing key (Ask AI, Milo livechat): batch mag "
            "stikken, de livechat nooit. Leeg = geplande runs "
            "gebruiken de gewone key."
        ),
    )
    loes_base_url = fields.Char(
        string="Loes API base URL",
        config_parameter="daadit_ai_loes.loes_base_url",
        default=LOES_DEFAULT_BASE_URL,
        help=(
            "Loes API endpoint (the HostYourAI EU-router). Defaults to "
            "LOES_DEFAULT_BASE_URL above. Must be https:// and the host "
            "must be on the allowlist. To accept "
            "a corporate proxy or regional endpoint, extend the allowlist "
            "via ICP daadit_ai_loes.allowed_base_url_hosts (comma-sep)."
        ),
    )
    loes_timeout = fields.Integer(
        string="Loes request timeout (seconds)",
        config_parameter="daadit_ai_loes.loes_timeout",
        default=60,
        help=(
            "How long to wait for a Loes response before giving up. "
            "Minimum 1 second (0 would hang the worker on a stalled "
            "connection). Maximum 600 seconds — anything higher is "
            "almost certainly a misconfiguration."
        ),
    )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @api.constrains("loes_base_url", "loes_key_enabled")
    def _check_loes_base_url(self):
        """Reject a base URL that's not on the host allowlist or not https.

        Runs ONLY when the Loes toggle is on — this is the
        cross-module guarantee: configuring Claude / Copilot / any
        other AI provider in the same Settings page does NOT trigger
        this validator. The
        ``daadit_ai_loes.allowed_base_url_hosts`` ICP lets an admin
        extend the host set without code changes.
        """
        for rec in self:
            if not rec.loes_key_enabled:
                continue
            url = (rec.loes_base_url or "").strip()
            if not url:
                continue  # default kicks in via field default
            # Idempotent save: if the URL equals the field default,
            # there's nothing to re-validate (the default is on the
            # allowlist by construction). Lets users save unrelated
            # tweaks elsewhere on the Settings page without
            # re-running the Loes check on an untouched URL.
            if url.rstrip("/") == LOES_DEFAULT_BASE_URL.rstrip("/"):
                continue
            self._validate_base_url(self.env, url)

    @api.constrains("loes_timeout")
    def _check_loes_timeout(self):
        for rec in self:
            t = rec.loes_timeout or 0
            if t < _MIN_TIMEOUT_SECONDS:
                raise ValidationError(_(
                    "Loes request timeout must be at least %(min)s "
                    "second(s). 0 means 'no timeout' in the underlying "
                    "HTTP library and would block an Odoo worker "
                    "indefinitely on a stalled connection.",
                    min=_MIN_TIMEOUT_SECONDS,
                ))
            if t > _MAX_TIMEOUT_SECONDS:
                raise ValidationError(_(
                    "Loes request timeout %(t)ss is unreasonably "
                    "high (max %(max)ss). Loes chat completions "
                    "rarely need more than 60 seconds.",
                    t=t, max=_MAX_TIMEOUT_SECONDS,
                ))

    # ------------------------------------------------------------------
    # Public helper — also called from LoesClient.from_env so the
    # validation runs even if an ICP row is poked directly via the
    # backend (bypassing this view).
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_base_url(env, url):
        """Raise ValidationError if ``url`` is not safe to send the key to.

        Rules:
          * scheme MUST be https
          * host MUST be on the allowlist (default + ICP extension)
          * no userinfo (``user:pass@host``) — never a legitimate use here
          * no IP literals — those bypass DNS-based reasoning about hosts
        """
        try:
            parsed = urlparse(url)
        except Exception as exc:  # noqa: BLE001
            raise ValidationError(_(
                "Loes base URL is not a valid URL: %(err)s", err=str(exc),
            )) from exc

        if parsed.scheme.lower() != "https":
            raise ValidationError(_(
                "Loes base URL must use https:// — got %(scheme)r. "
                "Sending the API key over plain HTTP would expose it.",
                scheme=parsed.scheme or "(empty)",
            ))
        if parsed.username or parsed.password:
            raise ValidationError(_(
                "Loes base URL must not contain userinfo "
                "(user:pass@host). Use the API-key field instead."
            ))
        host = (parsed.hostname or "").lower()
        if not host:
            raise ValidationError(_("Loes base URL has no host."))

        # Reject IP literals — anyone who needs to talk to a numeric
        # endpoint should opt in via the ICP allowlist, not the URL.
        import re
        if re.match(r"^[\d.:a-fA-F]+$", host) and any(
            ch.isdigit() for ch in host
        ) and (host.count(".") == 3 or ":" in host):
            raise ValidationError(_(
                "Loes base URL must use a hostname, not an IP "
                "literal (%(h)s). Add the host to the allowlist via "
                "ICP daadit_ai_loes.allowed_base_url_hosts if you "
                "really need to.",
                h=host,
            ))

        allowed = set(_DEFAULT_ALLOWED_HOSTS)
        try:
            extra = env["ir.config_parameter"].sudo().get_param(
                "daadit_ai_loes.allowed_base_url_hosts", default=""
            ) or ""
            for entry in extra.split(","):
                entry = entry.strip().lower()
                if entry:
                    allowed.add(entry)
        except Exception:  # noqa: BLE001
            pass

        if host not in allowed:
            # Cross-provider hint — if the host clearly belongs to a
            # known other provider, point the admin at the right
            # settings field instead of just rejecting the URL.
            #
            # v19.0.4.5.0 (store readiness): hints that name a DAADit
            # sibling module's field only render when that module is
            # actually installed. A store customer running just this
            # module gets a generic "does not belong here" wording
            # instead of a pointer to a field they don't have.
            def _sibling_installed(name):
                try:
                    return bool(env["ir.module.module"].sudo().search_count(
                        [("name", "=", name), ("state", "=", "installed")],
                    ))
                except Exception:  # noqa: BLE001
                    return False

            wrong_provider_hint = ""
            host_lower = host.lower()
            if "mistral.ai" in host_lower:
                if _sibling_installed("daadit_ai_mistral"):
                    wrong_provider_hint = _(
                        " This URL looks like Mistral AI's API "
                        "endpoint — set it on the 'Mistral API base URL' "
                        "field of the Mistral provider block, not "
                        "Loes's."
                    )
                else:
                    wrong_provider_hint = _(
                        " This URL looks like Mistral AI's API "
                        "endpoint — it does not belong in the Loes "
                        "provider settings."
                    )
            elif "anthropic.com" in host_lower:
                if _sibling_installed("daadit_ai_claude"):
                    wrong_provider_hint = _(
                        " This URL looks like Anthropic Claude's API "
                        "endpoint — set it on the 'Claude API base URL' "
                        "field of the Anthropic / Claude provider block, "
                        "not Loes's."
                    )
                else:
                    wrong_provider_hint = _(
                        " This URL looks like Anthropic Claude's API "
                        "endpoint — it does not belong in the Loes "
                        "provider settings."
                    )
            elif "openai.azure.com" in host_lower or (
                "services.ai.azure.com" in host_lower
            ):
                if _sibling_installed("daadit_ai_copilot"):
                    wrong_provider_hint = _(
                        " This URL looks like an Azure OpenAI / Foundry "
                        "endpoint — set it on the 'Microsoft 365 Copilot "
                        "endpoint' field, not Loes's."
                    )
                else:
                    wrong_provider_hint = _(
                        " This URL looks like an Azure OpenAI / Foundry "
                        "endpoint — it does not belong in the Loes "
                        "provider settings."
                    )
            elif "openai.com" in host_lower:
                wrong_provider_hint = _(
                    " This URL looks like OpenAI's public API — "
                    "configure it on the OpenAI provider block, not "
                    "Loes's."
                )
            elif "googleapis.com" in host_lower or (
                "generativelanguage.googleapis.com" in host_lower
            ):
                wrong_provider_hint = _(
                    " This URL looks like Google Gemini's API — "
                    "configure it on the Google provider block, not "
                    "Loes's."
                )
            raise ValidationError(_(
                "Loes base URL host %(host)r is not on the allowlist. "
                "Allowed hosts: %(allowed)s.%(hint)s To approve another "
                "host, set ir.config_parameter "
                "'daadit_ai_loes.allowed_base_url_hosts' (comma-"
                "separated).",
                host=host,
                allowed=", ".join(sorted(allowed)),
                hint=wrong_provider_hint,
            ))

    # ------------------------------------------------------------------
    # Model list — manual refresh from the settings screen
    #
    # The Loes model dropdown on ai.agent is fed from the live
    # ``daadit.ai.loes.model`` registry, refreshed daily by cron. This
    # button lets an admin pull the current list on demand. Runs against
    # the SAVED configuration, so save the key first if you just entered it.
    # ------------------------------------------------------------------
    def action_daadit_loes_sync_models(self):
        self.ensure_one()
        return self.env["daadit.ai.loes.model"].sudo().action_sync_now()
