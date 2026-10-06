# -*- coding: utf-8 -*-
"""Kennisbronnen indexeren in een vaste volgorde (project 91, taak 1483).

De twee Enterprise-crons ("AI Embedding: Generate Embeddings" en "AI Agent
Sources: Process Sources") liepen los van elkaar en werden daarom met de
hand in volgorde gestart. Hier zijn ze één cron met twee stappen: eerst
de embeddings, dan de bronnen. Mislukt stap 1, dan draait stap 2 niet.

Elke ronde krijgt een regel in ``daadit.ai.index.run``; een bron die in
een geslaagde ronde geïndexeerd staat, krijgt die tijd als
``daadit_last_indexed_at``. Is er 24 uur geen geslaagde ronde geweest, of
hangt een bron al 24 uur op "processing", dan gaat er één melding per
etmaal naar het ops-adres.
"""
import logging
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.modules import module as odoo_module

from ..services import provider_bridge

_logger = logging.getLogger(__name__)

STALL_HOURS = 24
OPS_EMAIL_PARAM = "daadit_mcp_multi_tenant.ops_alert_email"
MISTRAL_BATCH = 200


class AiAgentSource(models.Model):
    _inherit = "ai.agent.source"

    daadit_last_indexed_at = fields.Datetime(
        string="Laatst geïndexeerd", readonly=True,
        help="De laatste geslaagde indexeringsronde waarin deze bron "
             "geïndexeerd stond.",
    )


class AiIndexRun(models.Model):
    _name = "daadit.ai.index.run"
    _description = "Indexeringsronde kennisbronnen"
    _order = "date desc, id desc"
    _rec_name = "date"

    date = fields.Datetime(
        default=fields.Datetime.now, required=True, readonly=True,
    )
    state = fields.Selection(
        [("done", "Geslaagd"), ("error", "Mislukt")],
        required=True, readonly=True,
    )
    embedding_ok = fields.Boolean(string="Embeddings", readonly=True)
    embedded_count = fields.Integer(
        string="Nieuwe embeddings (Mistral)", readonly=True,
    )
    sources_ok = fields.Boolean(string="Bronnen", readonly=True)
    indexed_count = fields.Integer(string="Geïndexeerd", readonly=True)
    processing_count = fields.Integer(string="In verwerking", readonly=True)
    failed_count = fields.Integer(string="Mislukt", readonly=True)
    error = fields.Text(readonly=True)
    alerted = fields.Boolean(string="Melding verstuurd", readonly=True)

    @api.model
    def _embed_mistral_chunks(self):
        """Embeddings voor ``mistral-embed``-stukken via onze eigen client.

        De Enterprise-cron kent geen provider voor ``mistral-embed`` en
        breekt daar af; deze stukken worden dus eerst hier gedaan.
        Zonder de Mistral-provider zijn er geen zulke stukken te doen.
        """
        client = provider_bridge.installed_service(
            self.env, "daadit_ai_mistral", "mistral_client",
        )
        if client is None:
            return 0
        self.env.cr.execute(
            "SELECT id FROM ai_embedding"
            " WHERE embedding_vector IS NULL AND embedding_model = %s"
            " AND content IS NOT NULL"
            " AND NOT COALESCE(has_embedding_generation_failed, FALSE)"
            " ORDER BY id LIMIT %s",
            (client.EMBEDDING_MODEL, MISTRAL_BATCH),
        )
        ids = [row[0] for row in self.env.cr.fetchall()]
        if not ids:
            return 0
        self.env["ai.embedding"].sudo().browse(ids) \
            ._daadit_run_embedding_pipeline()
        return len(ids)

    @api.model
    def _run_steps(self):
        """Stap 1 embeddings, stap 2 bronnen; stopt bij de eerste fout."""
        vals = {"embedding_ok": False, "sources_ok": False}
        try:
            vals["embedded_count"] = self._embed_mistral_chunks()
            self.env["ai.embedding"].sudo()._cron_generate_embedding()
            vals["embedding_ok"] = True
            self.env["ai.agent.source"].sudo()._cron_process_sources()
            vals["sources_ok"] = True
        except Exception as exc:  # noqa: BLE001
            _logger.exception("indexering: ronde mislukt")
            if not odoo_module.current_test:
                self.env.cr.rollback()
            vals["error"] = str(exc)[:2000]
        vals["state"] = "done" if vals["sources_ok"] else "error"
        return vals

    @api.model
    def _cron_index_sources(self):
        vals = self._run_steps()
        Source = self.env["ai.agent.source"].sudo()
        counts = {
            status: Source.search_count([("status", "=", status)])
            for status in ("indexed", "processing", "failed")
        }
        vals.update({
            "indexed_count": counts["indexed"],
            "processing_count": counts["processing"],
            "failed_count": counts["failed"],
        })
        run = self.sudo().create(vals)
        if run.state == "done":
            Source.search([("status", "=", "indexed")]).write({
                "daadit_last_indexed_at": run.date,
            })
        self._check_stalled(run)
        return run

    @api.model
    def _stall_reasons(self):
        cutoff = fields.Datetime.now() - timedelta(hours=STALL_HOURS)
        reasons = []
        last_ok = self.sudo().search([("state", "=", "done")], limit=1)
        if not last_ok or last_ok.date < cutoff:
            reasons.append(_(
                "Al %s uur geen geslaagde indexeringsronde (laatste: %s).",
                STALL_HOURS, last_ok.date or _("nooit"),
            ))
        stuck = self.env["ai.agent.source"].sudo().search([
            ("status", "=", "processing"),
            ("is_active", "=", True),
            ("write_date", "<", cutoff),
        ])
        if stuck:
            reasons.append(_(
                "%s bron(nen) staan langer dan %s uur op verwerking: %s",
                len(stuck), STALL_HOURS,
                ", ".join(
                    "%s (%s)" % (s.name or s.id, s.agent_id.name or "-")
                    for s in stuck[:10]
                ),
            ))
        return reasons

    @api.model
    def _check_stalled(self, run):
        reasons = self._stall_reasons()
        if not reasons:
            return False
        cutoff = fields.Datetime.now() - timedelta(hours=STALL_HOURS)
        if self.sudo().search_count([
            ("alerted", "=", True), ("date", ">=", cutoff),
        ]):
            return False
        recipient = (self.env["ir.config_parameter"].sudo().get_param(
            OPS_EMAIL_PARAM) or self.env.company.email or "").strip()
        if not recipient:
            _logger.warning(
                "indexering staat stil, maar er is geen ops-adres: %s",
                " ".join(reasons),
            )
            return False
        body = Markup("<p>%s</p><ul>%s</ul>") % (
            _("De indexering van de kennisbronnen staat stil:"),
            Markup("").join(Markup("<li>%s</li>") % escape(r)
                            for r in reasons),
        )
        self.env["mail.mail"].sudo().create({
            "subject": _("[AI] Indexering kennisbronnen staat stil"),
            "email_to": recipient,
            "body_html": body,
        })
        run.alerted = True
        return True
