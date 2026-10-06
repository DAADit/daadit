# Copyright 2026 DAADit
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0.html).

import importlib
import inspect
import json
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

RAW_NOISE_PATTERNS = [
    r"\bfrom:\b",
    r"\bvan:\b",
    r"\bsent:\b",
    r"\bverzonden:\b",
    r"\bsubject:\b",
    r"\bonderwerp:\b",
    r"\bto:\b",
    r"\baan:\b",
    r"reacted to your message",
    r"wrote:",
    r"schreef:",
    r"kind regards",
    r"met vriendelijke groet",
]

ALLOWED_CATEGORIES = {
    "support",
    "incident",
    "service_request",
    "integration",
    "customization",
    "configuration",
    "process",
    "training",
    "project",
    "other",
}
ALLOWED_IMPORTANCE = {"low", "normal", "high"}


class AiCustomerMemory(models.Model):
    _name = "daadit.ai.customer.memory"
    _description = "AI Customer Memory"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "indexed_at desc, id desc"
    _rec_name = "display_name"

    display_name = fields.Char(compute="_compute_display_name", store=True)
    partner_id = fields.Many2one("res.partner", string="Customer", required=True, index=True, ondelete="cascade")
    commercial_partner_id = fields.Many2one(
        "res.partner",
        string="Commercial Customer",
        related="partner_id.commercial_partner_id",
        store=True,
        index=True,
    )
    source_model = fields.Selection(
        selection=[("helpdesk.ticket", "Helpdesk Ticket"), ("project.task", "Project Task"), ("manual", "Manual")],
        default="manual",
        required=True,
        index=True,
    )
    source_res_id = fields.Integer(string="Source Record ID", index=True)
    ticket_id = fields.Many2one("helpdesk.ticket", string="Ticket", ondelete="set null")
    task_id = fields.Many2one("project.task", string="Task", ondelete="set null")

    category = fields.Selection(
        selection=[
            ("support", "Support"),
            ("incident", "Incident"),
            ("service_request", "Service Request"),
            ("integration", "Integration"),
            ("customization", "Customization"),
            ("configuration", "Configuration"),
            ("process", "Process"),
            ("training", "Training"),
            ("project", "Project"),
            ("other", "Other"),
        ],
        default="other",
        required=True,
        index=True,
    )
    importance = fields.Selection(
        selection=[("low", "Low"), ("normal", "Normal"), ("high", "High")],
        default="normal",
        required=True,
    )
    memory = fields.Text(required=True, tracking=True)
    solution_summary = fields.Text(string="Solution / Work Done", tracking=True)
    confidence = fields.Integer(default=0)
    indexed_at = fields.Datetime(default=fields.Datetime.now, required=True, index=True)
    active = fields.Boolean(default=True, index=True)
    state = fields.Selection(
        selection=[("active", "Active"), ("archived", "Archived"), ("error", "Error")],
        default="active",
        required=True,
        index=True,
        tracking=True,
    )
    error_message = fields.Text()
    log_id = fields.Many2one("daadit.ai.customer.memory.log", string="Indexing Log", ondelete="set null")

    @api.depends("partner_id", "category", "memory")
    def _compute_display_name(self):
        for rec in self:
            bits = [rec.partner_id.display_name or "Customer", dict(self._fields["category"].selection).get(rec.category, rec.category or "Other")]
            if rec.memory:
                bits.append(rec.memory[:80])
            rec.display_name = " - ".join(bits)

    def action_open_source(self):
        self.ensure_one()
        if not self.source_model or self.source_model == "manual" or not self.source_res_id:
            raise UserError(_("No source record is linked."))
        return {"type": "ir.actions.act_window", "res_model": self.source_model, "res_id": self.source_res_id, "view_mode": "form", "target": "current"}

    def action_archive(self):
        self.write({"active": False, "state": "archived"})
        return True

    def action_activate(self):
        self.write({"active": True, "state": "active"})
        return True

    @api.model
    def _settings(self):
        return self.env["daadit.ai.customer.memory.settings"].get_singleton()

    @api.model
    def _plain(self, value):
        if not value:
            return ""
        text = value
        if not isinstance(value, str):
            text = str(value)
        try:
            text = html2plaintext(text)
        except Exception:  # pragma: no cover
            text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @api.model
    def _sanitize_personal_data(self, text):
        """Sanitize one text value.

        This method intentionally accepts only scalar values. Lists/dicts are
        handled by `_sanitize_value_for_ai`, so we never pass a chatter list
        directly into `re.sub`.
        """
        if not text:
            return ""
        sanitized = self._plain(text)
        patterns = [
            (r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[EMAIL]"),
            (r"(?<!\w)(?:\+31|0031|0)\s?(?:6|[1-9][0-9])(?:[\s.-]?\d){7,8}(?!\w)", "[PHONE]"),
            (r"\b\d{4}\s?[A-Z]{2}\b", "[POSTCODE]"),
            (r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{10}\b", "[IBAN]"),
            (r"\b(?:NL)?\d{9}B\d{2}\b", "[VAT_NUMBER]"),
        ]
        for pattern, replacement in patterns:
            sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
        return sanitized

    @api.model
    def _sanitize_value_for_ai(self, value):
        """Recursively sanitize values before they are sent to the agent.

        Chatter is often a list of dictionaries. Earlier versions tried to pass
        that list into `_sanitize_personal_data`, which caused:
        `TypeError: expected string or bytes-like object, got 'list'`.
        """
        if value in (False, None, ""):
            return ""
        if isinstance(value, list):
            return [self._sanitize_value_for_ai(item) for item in value]
        if isinstance(value, tuple):
            return [self._sanitize_value_for_ai(item) for item in value]
        if isinstance(value, dict):
            return {key: self._sanitize_value_for_ai(val) for key, val in value.items()}
        return self._sanitize_personal_data(value)

    @api.model
    def _strip_email_noise(self, text):
        if not text:
            return ""
        lines = []
        for line in text.splitlines() if "\n" in text else text.split("  "):
            clean = line.strip()
            if not clean:
                continue
            if re.match(r"^(from|van|sent|verzonden|to|aan|subject|onderwerp|cc):", clean, flags=re.IGNORECASE):
                continue
            if any(re.search(pattern, clean, flags=re.IGNORECASE) for pattern in [r"reacted to your message", r"^>+"]):
                continue
            lines.append(clean)
        return re.sub(r"\s+", " ", " ".join(lines)).strip()

    @api.model
    def _is_raw_noise(self, text):
        if not text:
            return False
        lowered = text.lower()
        if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in RAW_NOISE_PATTERNS):
            return True
        if len(text) > 700:
            return True
        if isinstance(text, (dict, list)):
            return True
        return False

    @api.model
    def _safe_short_text(self, value, max_len=500):
        if not value or isinstance(value, (dict, list)):
            return ""
        text = self._strip_email_noise(self._plain(value))
        if self._is_raw_noise(text):
            return ""
        return text[:max_len].strip()

    @api.model
    def _extract_json(self, value):
        if not value:
            return {}
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return {}
        text = value.strip()
        # Remove common markdown code fences if present.
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
        try:
            return json.loads(text)
        except Exception:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except Exception:
                    return {}
        return {}

    @api.model
    def _prepare_source_for_ai(self, source_values):
        settings = self._settings()
        source = dict(source_values)
        if settings.anonymize or not settings.send_personal_data:
            for key in ("title", "description", "solution", "chatter", "partner_name", "commercial_partner_name", "assigned_user"):
                if key in source:
                    source[key] = self._sanitize_value_for_ai(source.get(key))
        if not settings.send_personal_data:
            source["partner_name"] = "[CUSTOMER]"
            source["commercial_partner_name"] = "[CUSTOMER]"
            source["assigned_user"] = "[USER]" if source.get("assigned_user") else ""
        return source

    @api.model
    def _prepare_prompt(self, source_for_ai):
        settings = self._settings()
        return "%s\n\nSOURCE JSON:\n%s" % (
            settings.system_prompt,
            json.dumps(source_for_ai, ensure_ascii=False, indent=2),
        )

    @api.model
    def _extract_text_fragments(self, value, depth=0):
        """Return all likely text fragments from an unknown Odoo AI response shape.

        Odoo 19 AI internals and the DAADit Mistral patch may return different
        structures depending on the called route: strings, dicts, lists,
        objects with `.content`, OpenAI-like `choices`, or chunk objects. This
        helper is intentionally defensive so we can parse the JSON text instead
        of incorrectly marking the run as skipped.
        """
        if value in (False, None, "") or depth > 6:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (int, float, bool)):
            return []
        texts = []
        if isinstance(value, dict):
            priority_keys = (
                "content",
                "text",
                "answer",
                "response",
                "result",
                "output",
                "message",
                "messages",
                "choices",
                "data",
            )
            for key in priority_keys:
                if key in value:
                    texts += self._extract_text_fragments(value.get(key), depth + 1)
            # Some providers return content blocks: {"type":"text", "text":"..."}
            if value.get("type") == "text" and value.get("text"):
                texts.append(value.get("text"))
            return texts
        if isinstance(value, (list, tuple, set)):
            for item in value:
                texts += self._extract_text_fragments(item, depth + 1)
            return texts

        # Objects returned by Odoo services can expose attributes instead of dicts.
        for attr in (
            "content",
            "text",
            "answer",
            "response",
            "result",
            "output",
            "message",
            "messages",
            "choices",
            "data",
        ):
            try:
                if hasattr(value, attr):
                    texts += self._extract_text_fragments(getattr(value, attr), depth + 1)
            except Exception:  # pragma: no cover
                continue
        if not texts:
            try:
                rendered = str(value)
                if rendered and rendered != object.__repr__(value):
                    texts.append(rendered)
            except Exception:  # pragma: no cover
                pass
        return texts

    @api.model
    def _normalise_ai_result(self, result):
        """Return (payload, raw_response) from many possible AI response shapes."""
        if result in (False, None, ""):
            return {}, ""
        try:
            raw_response = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            raw_response = repr(result)

        if isinstance(result, dict) and "memories" in result:
            return result, raw_response

        # First try common structured shapes directly.
        if isinstance(result, dict):
            for key in ("content", "result", "response", "answer", "output", "message", "choices", "data"):
                if key in result:
                    parsed = self._extract_json(result.get(key))
                    if parsed:
                        return parsed, raw_response

        # Then recursively scan all text fragments for a JSON object.
        for fragment in self._extract_text_fragments(result):
            parsed = self._extract_json(fragment)
            if parsed:
                return parsed, raw_response
        return {}, raw_response

    @api.model
    def _get_selected_ai_agent(self):
        settings = self._settings()
        if not settings.ai_agent_ref or "ai.agent" not in self.env:
            return self.env["ai.agent"] if "ai.agent" in self.env else False
        try:
            return self.env["ai.agent"].sudo().browse(int(settings.ai_agent_ref)).exists()
        except Exception:
            return self.env["ai.agent"]

    @api.model
    def _call_odoo_llm_service(self, prompt):
        """Call Odoo's low-level AI LLM service.

        The selected Odoo AI Agent is kept as configuration/context, but Odoo
        Agent records are not guaranteed to be directly callable from Python.
        This method therefore uses the low-level LLMApiService that is patched by
        `daadit_ai_mistral` in your database. It tries multiple Odoo 19 / patched
        signatures and parses many possible response shapes.
        """
        try:
            module = importlib.import_module("odoo.addons.ai.utils.llm_api_service")
            service_class = getattr(module, "LLMApiService", None)
        except Exception as exc:  # pylint: disable=broad-except
            return {}, "", "", "Could not import Odoo LLMApiService: %s" % exc
        if not service_class:
            return {}, "", "", "Odoo LLMApiService class was not found."

        service = None
        init_errors = []
        for args in ((self.env,), (self.env.cr, self.env.uid, self.env.context), ()):  # tolerate minor API changes
            try:
                service = service_class(*args)
                break
            except TypeError as exc:
                init_errors.append(str(exc))
                continue
            except Exception as exc:  # pylint: disable=broad-except
                init_errors.append(str(exc))
                continue
        if not service:
            return {}, "", "", "Could not initialize Odoo LLMApiService: %s" % " | ".join(init_errors)

        method = getattr(service, "request_llm", None)
        if not callable(method):
            return {}, "", "", "Odoo LLMApiService.request_llm was not found."

        agent = self._get_selected_ai_agent()
        messages = [
            {"role": "system", "content": "Return only valid JSON. Do not use markdown."},
            {"role": "user", "content": prompt},
        ]

        # Common and patched signatures. Keep the agent in kwargs for DAADit
        # Mistral patch paths that look for agent context.
        attempts = [
            ("messages+agent", (), {"messages": messages, "agent": agent, "temperature": 0.1, "response_format": {"type": "json_object"}}),
            ("messages+agent_ref", (), {"messages": messages, "agent_ref": getattr(agent, "id", False), "temperature": 0.1, "response_format": {"type": "json_object"}}),
            ("prompt+agent", (), {"prompt": prompt, "agent": agent, "temperature": 0.1, "response_format": {"type": "json_object"}}),
            ("messages", (), {"messages": messages, "temperature": 0.1, "response_format": {"type": "json_object"}}),
            ("prompt", (), {"prompt": prompt, "temperature": 0.1, "response_format": {"type": "json_object"}}),
            ("pos_messages_agent", (messages, agent), {}),
            ("pos_messages", (messages,), {}),
            ("pos_prompt_agent", (prompt, agent), {}),
            ("pos_prompt", (prompt,), {}),
        ]
        errors = []
        last_raw = ""
        for label, args, kwargs in attempts:
            try:
                result = method(*args, **kwargs)
                payload, raw_response = self._normalise_ai_result(result)
                last_raw = raw_response or last_raw
                if payload:
                    return payload, raw_response, "LLMApiService.request_llm:%s" % label, ""
                errors.append("%s returned no parseable JSON. Raw=%s" % (label, (raw_response or "")[:500]))
            except TypeError as exc:
                errors.append("%s TypeError: %s" % (label, exc))
                continue
            except Exception as exc:  # pylint: disable=broad-except
                errors.append("%s Error: %s" % (label, exc))
                _logger.exception("Odoo LLM service call failed using %s", label)
                continue
        return {}, last_raw, "LLMApiService.request_llm", " | ".join(errors[-5:]) or "Odoo LLM service could not be called."

    @api.model
    def _call_selected_agent(self, prompt):
        """Call AI for memory extraction.

        Important: do NOT mark the run as skipped just because the selected
        `ai.agent` has no direct Python callable. In Odoo 19 those agents are
        usually UI / server-action concepts. For batch indexing, the reliable
        callable layer in this database is the patched LLMApiService.
        """
        settings = self._settings()
        agent_note = ""
        if settings.ai_agent_ref and "ai.agent" in self.env:
            try:
                agent = self.env["ai.agent"].sudo().browse(int(settings.ai_agent_ref)).exists()
                if agent:
                    agent_note = "Selected agent: %s. " % agent.display_name
            except Exception as exc:
                agent_note = "Invalid selected agent reference: %s. " % exc
        elif not settings.ai_agent_ref:
            agent_note = "No AI agent configured. "

        payload, raw_response, method_name, llm_error = self._call_odoo_llm_service(prompt)
        if payload:
            return payload, raw_response, method_name, ""
        return {}, raw_response, method_name, agent_note + (llm_error or "No parseable JSON returned by AI service.")

    @api.model
    def _create_index_log(self, source_record, source_for_ai, prompt, payload, raw_response, state, message, method_name=""):
        settings = self._settings()
        if not settings.log_ai_requests:
            return self.env["daadit.ai.customer.memory.log"]
        if settings.log_only_when_no_memory and state == "success":
            return self.env["daadit.ai.customer.memory.log"]
        partner = source_record.partner_id if source_record and source_record.partner_id else False
        agent_name = ""
        if settings.ai_agent_ref and "ai.agent" in self.env:
            try:
                agent = self.env["ai.agent"].sudo().browse(int(settings.ai_agent_ref)).exists()
                agent_name = agent.display_name if agent else ""
            except Exception:
                agent_name = ""
        return self.env["daadit.ai.customer.memory.log"].sudo().create({
            "partner_id": partner.id if partner else False,
            "source_model": source_record._name if source_record else "manual",
            "source_res_id": source_record.id if source_record else 0,
            "ticket_id": source_record.id if source_record and source_record._name == "helpdesk.ticket" else False,
            "task_id": source_record.id if source_record and source_record._name == "project.task" else False,
            "state": state,
            "agent_ref": settings.ai_agent_ref or "",
            "agent_name": agent_name or method_name,
            "message": message or "",
            "source_json": json.dumps(source_for_ai or {}, ensure_ascii=False, indent=2),
            "prompt": prompt or "",
            "raw_response": raw_response or "",
            "parsed_response": json.dumps(payload or {}, ensure_ascii=False, indent=2),
        })

    @api.model
    def _clean_memory_items(self, payload, source_title):
        settings = self._settings()
        memories = payload.get("memories") if isinstance(payload, dict) else []
        if not isinstance(memories, list):
            return []
        clean_items = []
        source_title_clean = self._plain(source_title).lower().strip()
        for item in memories[: max(settings.max_memories_per_source or 3, 1)]:
            if not isinstance(item, dict):
                continue
            memory = self._safe_short_text(item.get("memory"), max_len=500)
            solution = self._safe_short_text(item.get("solution_summary"), max_len=500)
            if not memory:
                continue
            if memory.lower().strip() == source_title_clean:
                continue
            if len(memory) < 15:
                continue
            category = item.get("category") or "other"
            importance = item.get("importance") or "normal"
            if category not in ALLOWED_CATEGORIES:
                category = "other"
            if importance not in ALLOWED_IMPORTANCE:
                importance = "normal"
            try:
                confidence = int(item.get("confidence") or 0)
            except (TypeError, ValueError):
                confidence = 0
            clean_items.append({
                "category": category,
                "importance": importance,
                "memory": memory,
                "solution_summary": solution,
                "confidence": max(0, min(confidence, 100)),
            })
        return clean_items

    @api.model
    def index_source_record(self, source_record, force=False):
        if source_record._name not in ("helpdesk.ticket", "project.task"):
            raise UserError(_("Unsupported source model: %s") % source_record._name)
        partner = source_record.partner_id
        if not partner:
            return self.env[self._name]
        commercial_partner = partner.commercial_partner_id or partner
        if commercial_partner.ai_memory_opt_out:
            return self.env[self._name]
        settings = self._settings()
        if not settings.enabled:
            return self.env[self._name]

        existing = self.search([("source_model", "=", source_record._name), ("source_res_id", "=", source_record.id)])
        if existing and not force:
            return existing
        if existing and force:
            existing.unlink()

        source_values = source_record._prepare_ai_memory_source_values()
        source_for_ai = self._prepare_source_for_ai(source_values)
        prompt = self._prepare_prompt(source_for_ai)
        payload, raw_response, method_name, error_message = self._call_selected_agent(prompt)
        clean_items = self._clean_memory_items(payload, source_values.get("title"))

        if not clean_items:
            message = error_message or "No reusable customer memory found or AI output was not valid JSON."
            self._create_index_log(source_record, source_for_ai, prompt, payload, raw_response, "skipped", message, method_name)
            return self.env[self._name]

        log = self._create_index_log(
            source_record,
            source_for_ai,
            prompt,
            payload,
            raw_response,
            "success",
            "Created %s memory item(s)." % len(clean_items),
            method_name,
        )
        created = self.env[self._name]
        for item in clean_items:
            vals = {
                "partner_id": partner.id,
                "source_model": source_record._name,
                "source_res_id": source_record.id,
                "ticket_id": source_record.id if source_record._name == "helpdesk.ticket" else False,
                "task_id": source_record.id if source_record._name == "project.task" else False,
                "category": item["category"],
                "importance": item["importance"],
                "memory": item["memory"],
                "solution_summary": item["solution_summary"],
                "confidence": item["confidence"],
                "state": "active",
                "active": True,
                "log_id": log.id if log else False,
            }
            created |= self.create(vals)
        return created
