# -*- coding: utf-8 -*-
"""Settings for Claude Design OAuth + default project URL."""
from odoo import fields, models

from .claude_design_client import (
    DEFAULT_PROJECT_URL,
    PARAM_ACCESS,
    PARAM_CLIENT_ID,
    PARAM_EXPIRES,
    PARAM_PROJECT_URL,
    PARAM_REFRESH,
    DEFAULT_CLIENT_ID,
)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    daadit_claude_design_project_url = fields.Char(
        string="Claude Design project-URL",
        config_parameter=PARAM_PROJECT_URL,
        default=DEFAULT_PROJECT_URL,
        help="Share-URL van het design system, bv. "
             "https://claude.ai/design/p/<uuid>. Agents halen dit live op.",
    )
    daadit_claude_design_access_token = fields.Char(
        string="Claude Design access token",
        config_parameter=PARAM_ACCESS,
        help="OAuth access token met scope user:design:read "
             "(via Claude Code /design-login of design-mcp login).",
    )
    daadit_claude_design_refresh_token = fields.Char(
        string="Claude Design refresh token",
        config_parameter=PARAM_REFRESH,
        help="Refresh token zodat Odoo het access token zelf vernieuwt.",
    )
    daadit_claude_design_expires_at = fields.Char(
        string="Access token verloopt (unix)",
        config_parameter=PARAM_EXPIRES,
        help="Optioneel: unix-timestamp waarop het access token verloopt.",
    )
    daadit_claude_design_client_id = fields.Char(
        string="OAuth client id",
        config_parameter=PARAM_CLIENT_ID,
        default=DEFAULT_CLIENT_ID,
        groups="base.group_system",
        help="Laat op de standaard Claude Design client id staan tenzij "
             "Anthropic een andere publiceert. Staat niet in de UI; alleen "
             "via systeemparameters indien nodig.",
    )
