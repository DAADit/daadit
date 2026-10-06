# -*- coding: utf-8 -*-
"""Historische runs met een valse aanklacht opnieuw beoordelen.

Runs 818 en 820 (13-8) staan op ``claims_unverified = true`` met
``claim_count = 0`` en de reden "rapport claimt werk zonder schrijfactie",
terwijl beide rapporten juist letterlijk melden dat er niets is
aangemaakt. Die waarde komt uit de logica van vóór de negatie- en
toekomstfilters: opgeslagen berekende velden worden niet vanzelf
herberekend, dus de aanklacht blijft in de restlijst staan zolang
niemand hem aanraakt.

Deze migratie rekent de verificatie- en aandachtsvelden opnieuw uit voor
runs met een rapport, zodat een eerlijk rapport ook eerlijk in de lijst
staat en de reden bij verloren schrijfacties de nieuwe, per-reden
uitsplitsing krijgt.
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
            "daadit_ai_agent_schedule 19.0.20.1.0: kon environment niet "
            "openen voor de herberekening van beweringen",
        )
        return
    Run = env["daadit.ai.agent.schedule.run"].sudo()
    runs = Run.search([
        "|",
        ("claims_unverified", "=", True),
        ("action_ids.is_write", "=", True),
    ])
    hersteld = 0
    for offset in range(0, len(runs), 200):
        chunk = runs[offset:offset + 200]
        was_flagged = {r.id: r.claims_unverified for r in chunk}
        try:
            chunk._compute_claims_unverified()
            chunk._compute_action_count()
            chunk._compute_needs_attention()
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_agent_schedule 19.0.20.1.0: herberekening "
                "mislukte op runs %s — de rest gaat door", chunk.ids,
            )
            continue
        hersteld += sum(
            1 for r in chunk
            if was_flagged.get(r.id) and not r.claims_unverified
        )
    _logger.info(
        "daadit_ai_agent_schedule 19.0.20.1.0: %s runs herbeoordeeld, "
        "%s valse aanklacht(en) ingetrokken", len(runs), hersteld,
    )
