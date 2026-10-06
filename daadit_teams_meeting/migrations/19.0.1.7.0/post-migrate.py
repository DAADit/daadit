# -*- coding: utf-8 -*-
"""Post-migration: patch the stock calendar email templates so events with
a Microsoft Teams videocall show a friendly link label instead of the
raw teams.microsoft.com URL.

This runs on every module upgrade to version 19.0.1.7.0+. Idempotent —
``_patch_calendar_mail_templates`` only rewrites a template when the
expected un-patched snippet is found, and is a no-op otherwise.

Existed before as ``post_init_hook`` only, which fires solely on initial
install. Customers who already had the module installed before the hook
was added (i.e. before 19.0.1.5.0) never got the templates patched —
this migration fixes that gap.
"""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.daadit_teams_meeting.hooks import _patch_calendar_mail_templates
    _patch_calendar_mail_templates(env)
