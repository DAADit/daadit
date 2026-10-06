# -*- coding: utf-8 -*-
"""Recordgebeurtenissen die op een AI-collega wachten.

Een automatiseringsregel draait in de transactie van wie het record
opslaat. Een LLM-run duurt seconden tot minuten, dus de regel zet alleen
een gebeurtenis klaar; een cron laat de collega die via het gewone
runpad (budget, feitenregel, schrijfgrenzen, audit) afhandelen.
"""

import logging
from datetime import timedelta

from markupsafe import Markup
from odoo import _, api, fields, models
from odoo.tools import SQL, html_sanitize

_logger = logging.getLogger(__name__)

# Een gebeurtenis die steeds faalt, kost anders elke ronde opnieuw tokens.
MAX_ATTEMPTS = 5


class DaaditAgentAutomationEvent(models.Model):
    _name = "daadit.agent.automation.event"
    _description = "Recordgebeurtenis voor een AI-collega"
    _order = "id desc"

    action_id = fields.Many2one(
        "ir.actions.server", string="Actie", required=True,
        ondelete="cascade", index=True, readonly=True,
    )
    schedule_id = fields.Many2one(
        "daadit.ai.agent.schedule", string="Collega en werkwijze",
        required=True, ondelete="cascade", readonly=True,
    )
    agent_id = fields.Many2one(related="schedule_id.agent_id")
    res_model = fields.Char(required=True, index=True, readonly=True)
    res_id = fields.Integer(required=True, index=True, readonly=True)
    res_model_name = fields.Char(string="Soort record", readonly=True)
    res_name = fields.Char(string="Record", readonly=True)
    prompt = fields.Text(string="Opdracht", readonly=True)
    state = fields.Selection(
        [("pending", "Wacht"), ("done", "Afgehandeld"),
         ("skipped", "Overgeslagen"), ("failed", "Mislukt")],
        default="pending", required=True, index=True, readonly=True,
    )
    attempts = fields.Integer(readonly=True)
    error = fields.Char(readonly=True)
    run_id = fields.Many2one(
        "daadit.ai.agent.schedule.run", string="Run",
        readonly=True, ondelete="set null",
    )
    message_id = fields.Many2one(
        "mail.message", string="Notitie", readonly=True,
        ondelete="set null",
    )
    activity_id = fields.Many2one(
        "mail.activity", string="Activiteit", readonly=True,
        ondelete="set null",
    )

    # Twee gelijktijdige writes op hetzelfde record zetten nooit twee
    # wachtende gebeurtenissen voor dezelfde regel klaar.
    _one_pending_per_record = models.UniqueIndex(
        "(action_id, res_model, res_id) WHERE state = 'pending'"
    )

    @api.model
    def _enqueue(self, action, records):
        """Zet per record hoogstens één gebeurtenis per periode klaar."""
        since = fields.Datetime.now() - timedelta(
            minutes=max(action.daadit_throttle_minutes, 1)
        )
        recent = self.search([
            ("action_id", "=", action.id),
            ("res_model", "=", records._name),
            ("res_id", "in", records.ids),
            "|", ("state", "=", "pending"), ("create_date", ">=", since),
        ])
        busy = set(recent.mapped("res_id"))
        todo = records.filtered(lambda r: r.id not in busy)
        if not todo:
            return self.browse()
        prompts = self.env["mail.render.mixin"].sudo()._render_template(
            action.daadit_prompt_template, todo._name, todo.ids,
            engine="inline_template",
        )
        model_name = self.env["ir.model"]._get(todo._name).name
        now = self.env.cr.now()
        uid = self.env.uid
        ids = []
        for record in todo:
            # Een regel kan in één transactie vaker vuren (aanmaken plus
            # herberekende velden), en twee transacties kunnen tegelijk
            # schrijven: de database laat hoogstens één wachtende
            # gebeurtenis per record en regel toe.
            self.env.cr.execute(SQL(
                """
                INSERT INTO daadit_agent_automation_event
                    (action_id, schedule_id, res_model, res_id,
                     res_model_name, res_name, prompt, state, attempts,
                     create_uid, create_date, write_uid, write_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', 0,
                        %s, %s, %s, %s)
                ON CONFLICT (action_id, res_model, res_id)
                    WHERE state = 'pending' DO NOTHING
                RETURNING id
                """,
                action.id, action.daadit_schedule_id.id, record._name,
                record.id, model_name, record.display_name,
                prompts.get(record.id) or "", uid, now, uid, now,
            ))
            ids.extend(row[0] for row in self.env.cr.fetchall())
        return self.browse(ids)

    def _record(self):
        self.ensure_one()
        if self.res_model not in self.env:
            return None
        return self.env[self.res_model].browse(self.res_id).exists() or None

    @api.model
    def _cron_process(self, limit=5):
        pending = self.search(
            [("state", "=", "pending"), ("attempts", "<", MAX_ATTEMPTS)],
            order="id", limit=limit,
        )
        for event in pending:
            try:
                event._process()
                self.env.cr.commit()
            except Exception as exc:  # noqa: BLE001
                self.env.cr.rollback()
                _logger.exception(
                    "daadit_agent_automation: gebeurtenis %s mislukt",
                    event.id,
                )
                attempts = event.attempts + 1
                event.write({
                    "attempts": attempts,
                    "error": str(exc)[:250],
                    "state": (
                        "failed" if attempts >= MAX_ATTEMPTS else "pending"
                    ),
                })
                self.env.cr.commit()

    def _process(self):
        self.ensure_one()
        record = self._record()
        if record is None:
            self.write({"state": "skipped",
                        "error": _("Het record bestaat niet meer.")})
            return
        schedule = self.schedule_id
        reason = (
            schedule._placement_block_reason()
            or schedule._budget_block_reason()
        )
        if reason:
            self.write({"state": "skipped", "error": reason[:250]})
            return
        run = schedule.with_context(
            daadit_automation_event_id=self.id,
        )._execute(trigger="automation")
        vals = {"run_id": run.id, "attempts": self.attempts + 1}
        if run.state == "done":
            vals.update(self._deliver(record, run))
        elif run.state == "disputed":
            vals.update({
                "state": "skipped",
                "error": _(
                    "Betwist: de collega beweerde een handeling zonder "
                    "geslaagde tool-actie; er is niets op het record "
                    "geplaatst."
                ),
            })
        else:
            vals.update({
                "state": "failed",
                "error": (run.error or _("De run is niet afgerond."))[:250],
            })
        self.write(vals)

    def _deliver(self, record, run):
        """Zet de uitkomst bij het record; wijzigt geen enkel veld ervan."""
        self.ensure_one()
        body = Markup(html_sanitize(run.findings_html or ""))
        if not body:
            return {"state": "done"}
        agent = self.schedule_id.agent_id
        outcome = self.action_id.daadit_outcome
        if outcome == "activity" and "activity_ids" in record._fields:
            activity = record.activity_schedule(
                "mail.mail_activity_data_todo",
                summary=_("%s vraagt je dit te bekijken") % agent.name,
                note=body,
                user_id=self.schedule_id.user_id.id,
            )
            return {"state": "done", "activity_id": activity.id}
        if "message_ids" in record._fields:
            message = record.with_context(
                mail_post_autofollow=False,
                mail_post_autofollow_author_skip=True,
            ).message_post(
                body=body,
                author_id=agent.partner_id.id or False,
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
            return {"state": "done", "message_id": message.id}
        return {
            "state": "skipped",
            "error": _("Dit soort record heeft geen chatter of "
                       "activiteiten."),
        }
