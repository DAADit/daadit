# -*- coding: utf-8 -*-
"""Spot an AI colleague being tagged in a chatter note."""

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class MailMessage(models.Model):
    _inherit = "mail.message"

    @api.model_create_multi
    def create(self, vals_list):
        messages = super().create(vals_list)
        try:
            messages._daadit_queue_agent_mentions()
        except Exception:  # noqa: BLE001
            # Posting a note must never fail because of us.
            _logger.exception(
                "daadit_agent_mention: queueing mentions raised"
            )
        return messages

    def _daadit_queue_agent_mentions(self):
        Agent = self.env["ai.agent"].sudo()
        agent_partners = None
        Mention = self.env["daadit.agent.mention"].sudo()
        queued = False

        for message in self:
            if message.message_type not in ("comment", "notification"):
                continue
            if not message.model or not message.res_id:
                continue
            # Chat channels already have their own agent flow; this is
            # about the chatter on business records.
            if message.model == "discuss.channel":
                continue
            partners = message.partner_ids
            if not partners:
                continue

            if agent_partners is None:
                # Every agent is reachable through TWO partners: its own
                # (the AI-chat identity) and its employee's work contact
                # (the "colleague" the @-dropdown finds in a chatter).
                # Nick tagged @Penny and got the employee partner — and
                # the mention fell in the gap between the two. So both
                # must map to the agent.
                agents = Agent.search([("partner_id", "!=", False)])
                agent_partners = {}
                for candidate in agents:
                    agent_partners[candidate.partner_id.id] = candidate
                    # x_employee_id is a database-defined field that may
                    # not exist outside production; stay defensive.
                    employee = getattr(candidate, "x_employee_id", False)
                    if employee and employee.work_contact_id:
                        agent_partners.setdefault(
                            employee.work_contact_id.id, candidate
                        )
                    if employee and employee.user_id:
                        agent_partners.setdefault(
                            employee.user_id.partner_id.id, candidate
                        )
            if not agent_partners:
                return

            # An agent answering another agent's note would loop until
            # someone noticed the token bill.
            if message.author_id and message.author_id.id in agent_partners:
                continue

            for partner in partners:
                agent = agent_partners.get(partner.id)
                if not agent:
                    continue
                Mention.create({
                    "message_id": message.id,
                    "agent_id": agent.id,
                    "res_model": message.model,
                    "res_id": message.res_id,
                })
                queued = True

        if queued:
            # Nudge the cron so the reply lands in seconds rather than
            # whenever the schedule next comes round.
            cron = self.env.ref(
                "daadit_agent_mention.ir_cron_agent_mention",
                raise_if_not_found=False,
            )
            if cron:
                cron.sudo()._trigger()
