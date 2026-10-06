# -*- coding: utf-8 -*-
"""Leesacties met een vertaalde toolnaam opnieuw indelen.

De toolnaam volgt de naam van de serveractie in de taal van de run:
"AI: Get Fields" is op een Nederlandse collega
``ir_actions_server_velden_oproepen``. Die naam stond niet in de vaste
leeslijst, dus telde elke veldopvraging als schrijfactie (run 1676:
"5 gewijzigd" zonder één wijziging). ``is_write`` is opgeslagen en
wordt niet vanzelf herberekend; deze migratie doet dat voor de acties
die met de nieuwe lijst als lezen gelden en werkt de tellers van hun
runs bij.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    try:
        from odoo import api, SUPERUSER_ID
        from odoo.addons.daadit_ai_agent_schedule.models.ai_agent_schedule import (
            readonly_tool_names,
        )

        env = api.Environment(cr, SUPERUSER_ID, {})
    except Exception:  # noqa: BLE001
        _logger.exception(
            "daadit_ai_agent_schedule 19.0.20.5.0: kon environment niet "
            "openen voor de herindeling van leesacties",
        )
        return
    Action = env["daadit.ai.agent.schedule.run.action"].sudo()
    acties = Action.search([
        ("is_write", "=", True),
        ("tool_name", "in", sorted(readonly_tool_names(env))),
    ])
    if not acties:
        _logger.info(
            "daadit_ai_agent_schedule 19.0.20.5.0: geen leesacties die "
            "als schrijfactie stonden",
        )
        return
    for offset in range(0, len(acties), 500):
        chunk = acties[offset:offset + 500]
        try:
            chunk._compute_is_write()
            chunk._compute_is_effective_write()
            chunk._compute_effect()
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_agent_schedule 19.0.20.5.0: herindeling "
                "mislukte op acties %s — de rest gaat door", chunk.ids,
            )
    runs = acties.mapped("run_id")
    for offset in range(0, len(runs), 200):
        chunk = runs[offset:offset + 200]
        try:
            chunk._compute_action_count()
            chunk._compute_claims_unverified()
            chunk._compute_needs_attention()
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_agent_schedule 19.0.20.5.0: tellers niet "
                "bijgewerkt op runs %s — de rest gaat door", chunk.ids,
            )
    _logger.info(
        "daadit_ai_agent_schedule 19.0.20.5.0: %s leesacties heringedeeld "
        "in %s runs", len(acties), len(runs),
    )
