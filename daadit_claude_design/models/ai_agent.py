# -*- coding: utf-8 -*-
"""AI tool backend: live Claude Design fetch for marketing agents."""
from odoo import models

from .claude_design_client import ClaudeDesignClient


class AiAgent(models.Model):
    _inherit = "ai.agent"

    def _daadit_claude_design_fetch(self, project_url=None):
        """Pull the current design system from Claude Design.

        Marketing agents must call this before drafting website or social
        content so they follow the live (moving) brand system.
        """
        self.ensure_one()
        return ClaudeDesignClient(self.env).fetch_design(project_url=project_url)
