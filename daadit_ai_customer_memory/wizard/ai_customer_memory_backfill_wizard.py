# Copyright 2026 DAADit
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0.html).
from odoo import _, fields, models
from odoo.exceptions import UserError


class AiCustomerMemoryBackfillWizard(models.TransientModel):
    _name = "daadit.ai.customer.memory.backfill.wizard"
    _description = "AI Customer Memory Backfill Wizard"

    source_type = fields.Selection(
        selection=[("helpdesk.ticket", "Helpdesk Tickets"), ("project.task", "Project Tasks")],
        default="helpdesk.ticket",
        required=True,
    )
    limit = fields.Integer(default=25, required=True)
    force_reindex = fields.Boolean(default=False)

    def action_run_backfill(self):
        self.ensure_one()
        settings = self.env["daadit.ai.customer.memory.settings"].get_singleton()
        safety_limit = settings.max_backfill_batch or 25
        if self.limit > safety_limit:
            raise UserError(_("The requested limit (%s) is higher than the configured safety limit (%s).") % (self.limit, safety_limit))

        domain = [("partner_id", "!=", False)]
        records = self.env[self.source_type].search(domain, order="id desc", limit=self.limit)
        indexed = 0
        for record in records:
            created = self.env["daadit.ai.customer.memory"].index_source_record(record, force=self.force_reindex)
            indexed += len(created)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("AI Customer Memory"),
                "message": _("Backfill completed. Created %s memory item(s).") % indexed,
                "type": "success",
                "sticky": False,
            },
        }
