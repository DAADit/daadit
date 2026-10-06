# -*- coding: utf-8 -*-
"""Push a bus-notificatie wanneer een run van status wisselt.

De payload is bewust leeg op een reden na: de client herlaadt bij elk
signaal de volledige payload via ``daadit.agent.activity.get_payload``.
Zo kan een gemiste notificatie nooit tot een verkeerd beeld leiden en
hoeft hier geen state te worden gesynchroniseerd.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

BUS_CHANNEL = "daadit_agent_activity"


class DaaditAgentScheduleRun(models.Model):
    _inherit = "daadit.ai.agent.schedule.run"

    def _daadit_activity_notify(self, reason):
        try:
            self.env["bus.bus"].sudo()._sendone(
                BUS_CHANNEL, "daadit_agent_activity/update",
                {"reason": reason},
            )
        except Exception:  # noqa: BLE001
            # Nooit een run laten falen op een dashboard-notificatie.
            _logger.exception("agent_activity: bus notify failed")

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._daadit_activity_notify("run_created")
        return records

    def write(self, vals):
        result = super().write(vals)
        if "state" in vals:
            self._daadit_activity_notify("run_state_%s" % vals.get("state"))
        return result
