# -*- coding: utf-8 -*-
"""Terugleesbare denkstappen — bezorg de vastgelegde stappen van één
chatbeurt aan de frontend.

De live denkstappen komen over de bus en zijn vluchtig: na het antwoord
zijn ze weg, en na een herlaadactie helemaal. Deze route levert dezelfde
stappen opnieuw, opgebouwd uit de audit (``run.action``) van de bij de
beurt horende run — zodat de frontend ze na afloop of na een herlaad
opnieuw kan tonen.

Toegang: alleen de ingelogde gebruiker, en alleen de chatbeurten die hij
zelf voerde (``user_id == uid``). De regels zijn hoe dan ook PII-vrij
(vaste labelset), maar we laten een gebruiker principieel niet de
denkstappen van andermans gesprek opvragen.
"""
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class DaaditAgentStepsController(http.Controller):

    @http.route(
        "/daadit_agent_steps/replay",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def replay(self, turn_id=None, **kw):
        """Geef de terugleesbare denkstappen voor ``turn_id``.

        Antwoordt met een lege lijst (niet met een fout) wanneer er geen
        eigen chatbeurt met dit id bestaat — de frontend valt dan gewoon
        terug op wat hij nog in het geheugen heeft.
        """
        turn_id = (turn_id or "").strip()
        if not turn_id:
            return {"ok": False, "error": "no_turn"}
        run = (
            request.env["daadit.ai.agent.schedule.run"]
            .sudo()
            .search(
                [
                    ("turn_uuid", "=", turn_id),
                    ("trigger", "=", "chat"),
                    ("user_id", "=", request.env.uid),
                ],
                order="id desc",
                limit=1,
            )
        )
        if not run:
            return {"ok": True, "steps": [], "agent": ""}
        try:
            steps = run._replay_steps()
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_agent_schedule: kon denkstappen voor beurt "
                "%s niet terughalen", turn_id,
            )
            return {"ok": False, "error": "replay_failed"}
        return {
            "ok": True,
            "agent": run.agent_id.name or "",
            "steps": steps,
        }
