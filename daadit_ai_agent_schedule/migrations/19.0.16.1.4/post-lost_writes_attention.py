# -*- coding: utf-8 -*-
"""Taak 802: herberekken ``needs_attention`` voor gedeeltelijk verlies.

Voorheen gold alleen ``write_attempt_count and not write_action_count``.
Runs zoals 546 en 609 (pogingen > effecten, maar effecten > 0) stonden
op ``needs_attention = false`` en verdwenen uit de restlijst. Na de
codefix moeten opgeslagen berekende velden opnieuw.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = None
    try:
        from odoo import api, SUPERUSER_ID

        env = api.Environment(cr, SUPERUSER_ID, {})
    except Exception:  # noqa: BLE001
        _logger.exception(
            "daadit_ai_agent_schedule 19.0.16.1.4: kon environment "
            "niet openen voor attention-recompute",
        )
        return
    Run = env["daadit.ai.agent.schedule.run"].sudo()
    # Alleen runs met schrijfpogingen. Zoeken gaat via het opgeslagen
    # is_write op de acties: write_attempt_count zelf is berekend en
    # niet opgeslagen, dus niet doorzoekbaar.
    runs = Run.search([("action_ids.is_write", "=", True)])
    for offset in range(0, len(runs), 200):
        chunk = runs[offset:offset + 200]
        chunk._compute_action_count()
        chunk._compute_needs_attention()
    _logger.info(
        "daadit_ai_agent_schedule 19.0.16.1.4: attention herrekend op "
        "%s runs met schrijfpogingen",
        len(runs),
    )
