# -*- coding: utf-8 -*-
"""Post-migration: seed ``ai.agent.daadit_office`` for databases where
``daadit_agent_dashboard`` was already installed before the Office field
shipped.

``post_init_hook`` only fires on a genuinely fresh install; this module
was already live in production, so the team's upgrade to 19.0.2.0.0
would otherwise skip the seeding entirely — see
``daadit_teams_meeting/migrations/19.0.1.7.0/post-migrate.py`` for the
same gap, hit and fixed earlier in this codebase.
"""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.daadit_agent_dashboard.hooks import _seed_agent_offices
    _seed_agent_offices(env)
