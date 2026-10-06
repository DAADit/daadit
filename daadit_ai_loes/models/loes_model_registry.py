# -*- coding: utf-8 -*-
"""Live registry of Loes models available via the Loes API.

The ``ai.agent.llm_model`` selection is fed from this table (see
``ai_agent.py``) so newly-released Loes models appear automatically
once the daily sync — or the manual "Refresh models" button — has run.
No code change or redeploy is needed when Loes ships a new model.

A small hardcoded seed (``data/loes_models_seed.xml``) keeps the
dropdown usable before the first successful sync (fresh install, no key
yet, or the API is unreachable).
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class DaaditAiLoesModel(models.Model):
    _name = "daadit.ai.loes.model"
    _description = "Loes model (synced from Loes API)"
    _order = "sequence, technical_name"
    # Without _rec_name Odoo's computed display_name falls back to
    # "model,id" — which is exactly what the agent dropdown then shows.
    _rec_name = "technical_name"

    technical_name = fields.Char(
        required=True,
        index=True,
        help="Model id sent to the Loes API, e.g. 'hyai/loes-large'.",
    )
    label = fields.Char(
        help="Human-readable label shown in the agent model dropdown. "
             "(Named 'label' because 'display_name' is reserved by base "
             "and silently breaks the selection labels.)",
    )
    active = fields.Boolean(
        default=True,
        help="Untick to hide this model from the agent dropdown without "
             "deleting it (keeps existing agents that use it valid).",
    )
    sequence = fields.Integer(default=10)
    synced_from_api = fields.Boolean(
        string="From API",
        readonly=True,
        help="True when this row was created/updated by a sync with the "
             "Loes API (as opposed to the built-in seed).",
    )
    last_synced = fields.Datetime(readonly=True)

    # v19: models.Constraint replaces the deprecated _sql_constraints
    # list, which Odoo 19 silently ignores.
    _technical_name_uniq = models.Constraint(
        "UNIQUE(technical_name)",
        "This Loes model id already exists in the registry.",
    )

    @api.model
    def _selection_entries(self):
        """Return ``(value, label)`` tuples for the ``llm_model`` field.

        Active rows only, ordered by sequence. Empty list if the table
        has no active rows — the caller then falls back to the seed
        constant so the dropdown is never empty.
        """
        recs = self.sudo().search(
            [("active", "=", True)], order="sequence, technical_name",
        )
        return [(r.technical_name, r.label or r.technical_name)
                for r in recs]

    @api.model
    def _sync_from_api(self):
        """Fetch models from Loes and upsert rows. Returns the count.

        Raises (via ``LoesClient.from_env``) if the Loes key is not
        enabled — callers that must not crash (the cron) guard for that
        first.
        """
        from ..services.loes_client import LoesClient, is_loes_model

        client = LoesClient.from_env(self.env)
        models_data = client.list_models()
        now = fields.Datetime.now()
        touched = 0
        skipped = 0
        # The HostYourAI router serves hundreds of models (Llama, Qwen,
        # Mistral, …). This registry is the LOES provider's dropdown, so
        # only Loes models are imported — everything else would (a) bloat
        # the agent dropdown and (b) collide with the selection keys of
        # the sibling provider modules (daadit_ai_mistral e.a.).
        seq = 0
        # Één router-push aan het einde i.p.v. één per rij — de
        # ORM-hooks hieronder zouden anders per upsert vuren.
        Registry = self.sudo().with_context(daadit_skip_router_push=True)
        for item in models_data:
            mid = item["id"]
            if not is_loes_model(mid):
                skipped += 1
                continue
            seq += 1
            vals = {
                "label": item.get("display_name") or mid,
                "synced_from_api": True,
                "last_synced": now,
                "active": True,
                "sequence": seq,
            }
            rec = Registry.search(
                [("technical_name", "=", mid)], limit=1,
            )
            if rec:
                rec.write(vals)
            else:
                Registry.create(dict(vals, technical_name=mid))
            touched += 1
        _logger.info(
            "daadit_ai_loes: synced %d Loes models from the API "
            "(%d non-Loes router models skipped)",
            touched, skipped,
        )
        self._daadit_push_to_router()
        return touched

    # ------------------------------------------------------------------
    # Directe doorstuur naar de AI Router (daadit_ai_router).
    #
    # De routerpagina "Providers & modellen" spiegelt dit register. Door
    # elke wijziging (API-sync én handmatige edits) direct door te duwen
    # is er geen wachttijd op de dagelijkse router-cron en is dit
    # register de enige beheerplek. Losjes gekoppeld: als de router niet
    # geïnstalleerd is, gebeurt er niets.
    # ------------------------------------------------------------------
    def _daadit_push_to_router(self):
        if self.env.context.get("daadit_skip_router_push"):
            return
        if "ai.router.model" not in self.env:
            return
        try:
            self.env["ai.router.model"].sudo()._sync_from_registries()
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_loes: push van modelregister naar "
                "ai.router.model mislukt")

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._daadit_push_to_router()
        return records

    def write(self, vals):
        result = super().write(vals)
        self._daadit_push_to_router()
        return result

    def unlink(self):
        env = self.env
        skip = env.context.get("daadit_skip_router_push")
        result = super().unlink()
        if not skip and "ai.router.model" in env:
            try:
                env["ai.router.model"].sudo()._sync_from_registries()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "daadit_ai_loes: push van modelregister naar "
                    "ai.router.model mislukt")
        return result

    @api.model
    def _cron_sync_models(self):
        """Daily cron entry point. Never raises — a transient API error
        must not leave the scheduled action in a failed state."""
        ICP = self.env["ir.config_parameter"].sudo()
        if ICP.get_param("daadit_ai_loes.loes_key_enabled") not in (
            "True", "1", True,
        ):
            _logger.info(
                "daadit_ai_loes: model sync skipped (Loes key disabled)"
            )
            return False
        try:
            self._sync_from_api()
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_loes: scheduled Loes model sync failed"
            )
            return False
        return True

    def action_sync_now(self):
        """Manual refresh from the list view / settings button."""
        count = self._sync_from_api()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Loes models refreshed"),
                "message": _(
                    "%s model(s) synced from the Loes API.", count,
                ),
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
