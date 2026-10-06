# -*- coding: utf-8 -*-
"""GDPR Art. 17 (right to erasure) helper for Loes usage history.

When a partner exercises the right to erasure, we must remove or
anonymise the personal data we hold about them. ``loes_usage`` rows
hold ``user_id`` (which links back to a partner). This action wipes
those links and adds a pseudonym so the cost-aggregation queries that
group by user keep working without identifying anyone.

De methode heette tot 6 oktober 2026 ``action_daadit_ai_gdpr_erase``
— exact dezelfde naam die ``daadit_ai_mistral`` op ``res.partner``
gebruikt, en geen van beide riep ``super()``. Odoo laat er dan één
winnen; op productie was dat Mistral. De knop "Erase Loes usage" wiste
dus Mistral-regels, meldde succes, en Loes-verbruik was met geen enkele
knop te wissen. Vandaar de eigen naam hieronder; wijzig hem niet terug.

Aanroepen gaat via ``daadit.gdpr.erase`` in ``daadit_mcp_multi_tenant``
(tandwielmenu op de relatiekaart), dat alle sporen in één venster toont.
"""
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = "res.partner"

    def action_daadit_loes_gdpr_erase(self):
        """Anonymise ``loes_usage`` rows tied to this partner's
        users. Keeps the cost figures intact (token counts and USD
        estimates) but removes the user identifier and stamps the row
        with the erasure date for audit purposes.
        """
        self.ensure_one()
        # Permission: gated by the view's `groups` attribute.
        # loes_usage rows live on `daadit_ai_loes.usage`.
        if "daadit_ai_loes.usage" not in self.env:
            raise UserError(_(
                "The `daadit_ai_loes.usage` model is not registered "
                "— is the module properly installed?"
            ))
        Usage = self.env["daadit_ai_loes.usage"].sudo()

        users = self.env["res.users"].sudo().search(
            [("partner_id", "=", self.id)]
        )
        if not users:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Loes usage — GDPR erasure"),
                    "message": _(
                        "No internal user is linked to %s — nothing to "
                        "anonymise on the usage history."
                    ) % self.display_name,
                    "type": "warning",
                },
            }
        rows = Usage.search([("user_id", "in", users.ids)])
        if not rows:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Loes usage — GDPR erasure"),
                    "message": _(
                        "No usage rows reference %(name)s "
                        "(%(users)s linked user(s)) — nothing to do."
                    ) % {"name": self.display_name, "users": len(users)},
                    "type": "warning",
                },
            }
        # Anonymise: drop user_id, leave token counts + cost intact for
        # aggregate cost reporting.
        rows.write({"user_id": False})
        when = fields.Datetime.now().strftime("%Y-%m-%d")
        _logger.info(
            "daadit_ai_loes.gdpr_erase: partner=%s users=%s "
            "usage_rows=%s wiped on %s",
            self.id, len(users), len(rows), when,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Loes usage — GDPR erasure"),
                "message": _(
                    "Anonymised %(rows)s Loes usage row(s) for "
                    "%(name)s. Token counts and cost figures kept "
                    "(no PII), user link removed."
                ) % {"rows": len(rows), "name": self.display_name},
                "type": "success",
                "sticky": True,
            },
        }
