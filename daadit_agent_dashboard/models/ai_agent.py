# -*- coding: utf-8 -*-
"""Organizational grouping for ``ai.agent``.

DAADit organizes its work into four "Offices" (documented in Knowledge,
article *Interne bedrijfsprocessen*): Delivery, Technical, Operations and
Executive. The native Agents kanban (AI app) showed every agent in one
undifferentiated pile; ``daadit_office`` lets it group by the same four
Offices instead, so a colleague can see at a glance which part of the
business an agent supports.

This is a plain, team-owned Selection field — no computation, no
automation. New agents start uncategorized (blank groups as "None" in
the kanban) until someone sets the Office by hand.

Also adds ``daadit_has_active_schedule``, a non-stored computed Boolean
backing a green "Active" ribbon (top-right, same spot as the stock
"Archived" ribbon) on the kanban card and form — true when the agent
has at least one active ``daadit.ai.agent.schedule``. Computed via an
explicit ``active = True`` search rather than relying on
``schedule_ids``' implicit active-filtering, so the meaning is
unambiguous and doesn't depend on ORM default behavior.
"""
from odoo import api, fields, models


class AiAgent(models.Model):
    _inherit = "ai.agent"

    daadit_office = fields.Selection(
        selection=[
            ("delivery", "🚚 Delivery Office"),
            ("technical", "🛠️ Technical Office"),
            ("operations", "⚙️ Operations Office"),
            ("executive", "🏛️ Executive Office"),
        ],
        string="Office",
        help="Welke van de vier DAADit-Offices (zie Knowledge → Interne "
             "bedrijfsprocessen) dit agent primair ondersteunt. Bepaalt de "
             "kolomindeling op het Agents-bord.",
    )

    daadit_has_active_schedule = fields.Boolean(
        string="Heeft actieve schedule",
        compute="_compute_daadit_has_active_schedule",
        search="_search_daadit_has_active_schedule",
        help="True zodra dit agent minstens één actieve "
             "daadit.ai.agent.schedule heeft. Bepaalt de groene "
             "'Active'-ribbon.",
    )

    @api.depends("schedule_ids.active")
    def _compute_daadit_has_active_schedule(self):
        active_agent_ids = set(
            self.env["daadit.ai.agent.schedule"].sudo().search(
                [("agent_id", "in", self.ids), ("active", "=", True)]
            ).agent_id.ids
        )
        for rec in self:
            rec.daadit_has_active_schedule = rec.id in active_agent_ids

    def _search_daadit_has_active_schedule(self, operator, value):
        """Translate a search on this computed Boolean into an id-domain.

        Mirrors ``daadit.agent.kpi._search_status``: whitelist the
        operators, resolve the set of boolean values actually wanted
        (handling list-valued ``in``/``not in`` and the mixed
        ``[True, False]`` case), and fail closed on anything else.
        """
        if operator not in ("=", "!=", "in", "not in"):
            return []
        wanted = set(value if isinstance(value, (list, tuple)) else [value])
        if operator in ("!=", "not in"):
            wanted = {True, False} - wanted
        agent_ids = self.env["daadit.ai.agent.schedule"].sudo().search(
            [("active", "=", True)]
        ).agent_id.ids
        if True in wanted and False in wanted:
            return []  # matches every agent
        if True in wanted:
            return [("id", "in", agent_ids)]
        if False in wanted:
            return [("id", "not in", agent_ids)]
        return [("id", "=", False)]  # matches nothing
