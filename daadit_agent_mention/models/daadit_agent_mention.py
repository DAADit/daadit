# -*- coding: utf-8 -*-
"""Queue of chatter mentions waiting for an agent to answer.

Answering runs through a queue rather than inline, for one practical
reason: an LLM call takes seconds, and nobody should watch a spinner
after typing a note. The mention is recorded, the cron is nudged, and
the reply lands in the chatter moments later — exactly how a colleague
who is momentarily busy would behave.
"""

import logging

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.tools import html_sanitize

_logger = logging.getLogger(__name__)

# An agent that keeps failing on the same note would otherwise retry for
# ever, spending tokens each round.
MAX_ATTEMPTS = 3


class DaaditAgentMention(models.Model):
    _name = "daadit.agent.mention"
    _description = "AI-agent vermelding in chatter"
    _order = "id desc"

    message_id = fields.Many2one(
        "mail.message", string="Bericht", required=True, ondelete="cascade",
        index=True,
    )
    agent_id = fields.Many2one(
        "ai.agent", string="Agent", required=True, ondelete="cascade",
        index=True,
    )
    res_model = fields.Char(string="Model", readonly=True)
    res_id = fields.Integer(string="Record", readonly=True)
    state = fields.Selection(
        selection=[
            ("pending", "Wacht op verwerking"),
            ("done", "Beantwoord"),
            ("failed", "Mislukt"),
            ("skipped", "Overgeslagen"),
        ],
        default="pending", required=True, index=True,
    )
    attempts = fields.Integer(default=0)
    error = fields.Char(readonly=True)
    reply_message_id = fields.Many2one(
        "mail.message", string="Antwoord", readonly=True,
    )

    @api.model
    def _cron_process(self, limit=10):
        """Answer the mentions that are still waiting."""
        pending = self.search(
            [("state", "=", "pending"), ("attempts", "<", MAX_ATTEMPTS)],
            limit=limit,
        )
        for mention in pending:
            # Commit per mention: one agent erroring must not roll back
            # the answers its colleagues already produced.
            try:
                mention._process()
                self.env.cr.commit()
            except Exception as exc:  # noqa: BLE001
                self.env.cr.rollback()
                _logger.exception(
                    "daadit_agent_mention: mention %s failed", mention.id
                )
                mention.sudo().write({
                    "attempts": mention.attempts + 1,
                    "error": str(exc)[:250],
                    "state": (
                        "failed" if mention.attempts + 1 >= MAX_ATTEMPTS
                        else "pending"
                    ),
                })
                self.env.cr.commit()
        return True

    def _build_prompt(self):
        """The question, with enough context to answer it properly."""
        self.ensure_one()
        message = self.message_id
        author = message.author_id.name or "Een collega"
        vraag = message.body or ""

        record_omschrijving = ""
        eerdere = ""
        if self.res_model and self.res_id:
            record = self.env[self.res_model].sudo().browse(self.res_id)
            if record.exists():
                record_omschrijving = (
                    f"Je bent getagd op het record '{record.display_name}' "
                    f"(model {self.res_model}, id {self.res_id})."
                )
                # The conversation so far, oldest first, so the agent can
                # see what it produced earlier and what the feedback is
                # about. Capped: chatters get long, prompts cost money.
                history = self.env["mail.message"].sudo().search(
                    [
                        ("model", "=", self.res_model),
                        ("res_id", "=", self.res_id),
                        ("id", "!=", message.id),
                        ("message_type", "in", ("comment", "notification")),
                    ],
                    order="id desc", limit=8,
                )
                regels = []
                for oud in reversed(history):
                    tekst = (oud.body or "").strip()
                    if tekst:
                        regels.append(
                            f"- {oud.author_id.name or 'onbekend'}: {tekst}"
                        )
                if regels:
                    eerdere = (
                        "Eerdere berichten in deze chatter "
                        "(oudste eerst):\n" + "\n".join(regels)
                    )

        return (
            f"{record_omschrijving}\n\n"
            f"{author} schrijft je nu aan:\n{vraag}\n\n"
            f"{eerdere}\n\n"
            f"Beantwoord dit als de collega die je bent. Ga concreet in "
            f"op wat er gevraagd wordt. Kun je iets aanpassen of "
            f"opleveren, doe dat dan en beschrijf wat je hebt gedaan. "
            f"Antwoord in het Nederlands en houd het kort en bruikbaar.\n\n"
            f"Vorm: dit antwoord komt als notitie in de chatter te staan. "
            f"Schrijf in gewone alinea's en eventueel een korte "
            f"opsomming. Gebruik GEEN codeblokken, geen tabellen en geen "
            f"HTML-broncode in je antwoord — beschrijf een wijziging in "
            f"woorden in plaats van de code te tonen."
        ).strip()

    @api.model
    def _to_chatter_html(self, raw):
        """Markdown → plain, readable chatter HTML.

        No syntax highlighting: paragraphs, bold and simple lists are
        what a note between colleagues needs. Falls back to escaped
        text with line breaks when markdown2 is unavailable.
        """
        raw = (raw or "").strip()
        if not raw:
            return "<p>Ik kon hier geen antwoord op formuleren.</p>"
        try:
            from markdown2 import markdown
            html = markdown(raw, extras=["strike"])
        except Exception:  # noqa: BLE001
            html = "<p>%s</p>" % (
                escape(raw).replace("\n", Markup("<br/>"))
            )
        return html_sanitize(html)

    def _process(self):
        self.ensure_one()
        agent = self.agent_id.sudo()
        if not agent.exists():
            self.sudo().write({"state": "skipped", "error": "Agent bestaat niet"})
            return

        prompt = self._build_prompt()
        if "daadit.ai.werkafspraak" in self.env:
            agent = agent.with_context(
                daadit_werkafspraak_message_id=self.message_id.id,
            )
            prompt = agent._daadit_apply_werkafspraken(prompt)
        # Deliberately NOT stock's enable_html_response: that pipeline
        # syntax-highlights fenced code blocks into a forest of spans,
        # which then lands in a chatter note as unreadable soup — the
        # exact wall of markup Nick screenshotted. We take the raw
        # markdown and convert it plainly ourselves.
        antwoorden = agent.get_direct_response(prompt) or []
        tekst = self._to_chatter_html(
            "\n\n".join(a for a in antwoorden if a)
        )

        if not (self.res_model and self.res_id):
            self.sudo().write({"state": "skipped", "error": "Geen record"})
            return

        record = self.env[self.res_model].sudo().browse(self.res_id)
        if not record.exists() or not hasattr(record, "message_post"):
            self.sudo().write({"state": "skipped", "error": "Record weg"})
            return

        reply = record.message_post(
            # get_direct_response already ran the answer through
            # html_sanitize; posting it as a plain str would make
            # message_post escape it a second time, so the chatter shows
            # literal <p> tags instead of formatted text. Markup says
            # "this is HTML, leave it be".
            body=Markup(tekst),
            author_id=agent.partner_id.id,
            message_type="comment",
            # A log note, matching how the question was asked: this is
            # internal coordination, not something a customer should be
            # notified about.
            subtype_xmlid="mail.mt_note",
        )
        self.sudo().write({
            "state": "done",
            "attempts": self.attempts + 1,
            "reply_message_id": reply.id,
            "error": False,
        })
