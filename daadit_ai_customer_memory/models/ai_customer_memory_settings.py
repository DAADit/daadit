# Copyright 2026 DAADit
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0.html).

from odoo import api, fields, models


DEFAULT_SYSTEM_PROMPT = """You are the DAADit Customer Memory Indexer.

Your task is to extract compact, long-term reusable customer knowledge from a closed Odoo helpdesk ticket or project task.

This is NOT a ticket summary.
This is NOT an email summary.
This is NOT a copy of the chatter.
This is NOT an archive.

Only extract durable, reusable facts that help future support or implementation work:
- customer-specific Odoo configuration
- custom workflows or customizations
- integrations and connectors
- recurring support patterns
- business process choices
- known implementation decisions
- important limitations or workarounds
- what DAADit actually did, configured, fixed, decided or followed up

Use the chatter only to infer the final action or solution.
Never copy raw chatter.
Never copy email headers.
Never include “From”, “Sent”, “Subject”, “To”, “CC”, “reacted to your message”, signatures or quoted replies.
Never include personal data such as names, email addresses, phone numbers, addresses, IBANs or VAT numbers.
Never invent facts.

Return ONLY valid JSON with exactly this structure:

{
  "memories": [
    {
      "category": "configuration",
      "importance": "normal",
      "memory": "One compact reusable customer-context sentence.",
      "solution_summary": "One compact sentence describing what DAADit did, fixed, decided or followed up.",
      "confidence": 90
    }
  ]
}

Rules:
- Maximum 3 memories.
- Prefer 1 memory for simple tickets.
- memory must not be the ticket title copied literally.
- solution_summary must not contain raw chatter, quoted email, mail headers or signatures.
- Use Dutch unless the source is clearly English.
- category must be one of: support, incident, service_request, integration, customization, configuration, process, training, project, other.
- importance must be one of: low, normal, high.
- confidence must be an integer from 0 to 100.
- If there is no durable customer knowledge, return {"memories": []}.
- Never output markdown.
- Never output commentary outside JSON."""


class AiCustomerMemorySettings(models.Model):
    _name = "daadit.ai.customer.memory.settings"
    _description = "AI Customer Memory Settings"
    _rec_name = "name"

    name = fields.Char(required=True, default="AI Customer Memory Settings")
    enabled = fields.Boolean(string="Enable AI customer memory", default=True)
    anonymize = fields.Boolean(string="Anonymize source content before AI", default=True)
    send_personal_data = fields.Boolean(string="Allow personal data to AI", default=False)
    max_memories_per_source = fields.Integer(string="Max memories per ticket/task", default=3)
    max_backfill_batch = fields.Integer(string="Max backfill batch size", default=25)
    ai_agent_ref = fields.Selection(
        selection="_selection_ai_agents",
        string="Odoo AI Agent",
    )
    system_prompt = fields.Text(default=DEFAULT_SYSTEM_PROMPT, required=True)
    log_ai_requests = fields.Boolean(
        string="Log AI indexing requests",
        default=True,
        help="Store the exact prompt/source sent to the selected agent and the returned response for prompt tuning.",
    )
    log_only_when_no_memory = fields.Boolean(
        string="Log only skipped/errors",
        default=False,
        help="If enabled, successful indexing calls are not logged; skipped and error cases remain logged.",
    )

    @api.model
    def _selection_ai_agents(self):
        if "ai.agent" not in self.env:
            return []
        agents = self.env["ai.agent"].sudo().search([], order="name", limit=200)
        return [(str(agent.id), agent.display_name) for agent in agents]

    @api.model
    def get_singleton(self):
        settings = self.sudo().search([], order="id", limit=1)
        if not settings:
            settings = self.sudo().create({"name": "AI Customer Memory Settings"})
        return settings

    @api.model_create_multi
    def create(self, vals_list):
        # Keep a single settings row. If one already exists, update it instead of creating duplicates.
        existing = self.sudo().search([], order="id", limit=1)
        if existing:
            for vals in vals_list:
                existing.write(vals)
            return existing
        return super().create(vals_list)
