# -*- coding: utf-8 -*-
"""Scheduling layer for ``ai.agent`` (Mistral provider).

A ``daadit.ai.agent.schedule`` ties an agent to a standing instruction
and a recurrence. A single ``ir.cron`` scans for due schedules and runs
each headless through the audited path of whichever provider serves
the agent (daadit_ai_mistral / daadit_ai_loes / daadit_ai_claude / a
future add-on, resolved by ``services.provider_bridge``), so every
security gate that protects interactive
chat (model allow/block lists, field-level PII blocklist, domain
validation, per-tool RBAC) is enforced for scheduled runs too.

Each execution writes one ``daadit.ai.agent.schedule.run`` with the
agent's findings (its written answer) and one
``daadit.ai.agent.schedule.run.action`` per tool call it performed.
"""
import json
import logging
import random
import re
import time
import traceback
from datetime import datetime, time as dtime, timedelta

import psycopg2
from dateutil.relativedelta import relativedelta
from markupsafe import Markup, escape as _escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import mute_logger

from ..services import (
    claim_ledger, eval_scoring, run_capture, source_citation,
)

_logger = logging.getLogger(__name__)

STRUCTURE_RETRY = (
    "Je antwoord mist de verplichte onderdelen: %s. Schrijf je antwoord "
    "opnieuw met precies de opbouw uit je instructies, elk onderdeel als "
    "eigen kopje. Gebruik geen tools en voeg geen nieuw werk toe; neem "
    "het ```claims-blok ongewijzigd over."
)

# Max chars stored per captured tool argument / result blob. Tool
# results are already capped at 50k by tool_dispatch; we store a
# further-trimmed copy so the run log table stays lean.
_MAX_ACTION_BLOB = 20_000

# Fase-0 guardrails from Governance & guardrails (knowledge id 173) —
# the two items the earlier v19.0.2.1.0 commit explicitly deferred:
# a circuit breaker on consecutive failures + failure-alerting via
# mail. Plus two availability rules from the same article: only one
# concurrent scheduled run across all workers, and cron jitter so
# schedules with the same cadence don't cluster on the second.
#
# Global run lock: stable arbitrary bigint used by
# ``pg_try_advisory_lock``. Session-level advisory locks are
# per-connection and survive commits; released explicitly in a
# finally-block. v19.0.2.5.0: taken by the RUNNER (``_execute``), not
# just the cron entrypoint — 'Run now' and test runs from an HTTP
# worker previously bypassed the lock entirely, which is how parallel
# runs 31∥32 happened live. Session locks are reentrant, so the cron
# path (which already holds it) still works unchanged.
_ADVISORY_LOCK_ID = 8675309

# Verbruikstabellen van de providers. Een agent kost geld in de chat
# én in geplande runs; alleen de runtabel optellen laat precies de
# duurste helft buiten beeld.
_USAGE_MODELS = (
    "daadit_ai_mistral.usage",
    "daadit_ai_claude.usage",
    "daadit_ai_loes.usage",
)

# Cron jitter: max seconds of random offset appended to ``nextcall``
# after each run. Spreads schedules that share an interval so a batch
# of hourly schedules doesn't all fire on the exact top of the hour.
_NEXTCALL_JITTER_SECONDS = 59

# Tool calls that don't mutate database state. Used to classify each
# captured action as read-only vs. write so the UI can hide the noise
# (search/get/read_group/open_menu/...) and surface only changes the
# agent actually made. Anything not in this set is treated as a write
# — that includes custom server actions like creating leads or
# scheduling activities.
_READONLY_TOOL_NAMES = frozenset({
    "ir_actions_server_search_knowledge",
    "ir_actions_server_search",
    "ir_actions_server_read_group",
    "ir_actions_server_get_fields",
    "ir_actions_server_get_menu_details",
    "ir_actions_server_open_menu_kanban",
    "ir_actions_server_open_menu_list",
    "ir_actions_server_open_menu_graph",
    "ir_actions_server_open_menu_pivot",
    "ir_actions_server_adjust_search",
    "ir_actions_server_compute_report_measures",
    # daadit_agent_hire: leestools van een ingehuurde collega op de
    # administratie van zijn klant (search_read via de koppeling, langs
    # de tenant-poort). Zonder deze regels simuleert een testrun ze en
    # kan de collega in zo'n run niets bekijken.
    "ir_actions_server_admin_models",
    "ir_actions_server_admin_fields",
    "ir_actions_server_admin_search",
})

# The same read-only actions by xmlid. The dispatch slug follows the
# action *name* in the language of the run, so "AI: Get Fields" is
# recorded as ``ir_actions_server_velden_oproepen`` on a Dutch agent
# and falls out of the set above (observed on run 1676: sixteen field
# look-ups counted as sixteen writes). These xmlids are resolved to
# their current name in every installed language at run time.
_READONLY_TOOL_XMLIDS = (
    "ai.ir_actions_server_search",
    "ai.ir_actions_server_read_group",
    "ai.ir_actions_server_get_fields",
    "ai.ir_actions_server_get_menu_details",
    "ai.ir_actions_server_open_menu_kanban",
    "ai.ir_actions_server_open_menu_list",
    "ai.ir_actions_server_open_menu_graph",
    "ai.ir_actions_server_open_menu_pivot",
    "ai.ir_actions_server_adjust_search",
    "ai.ir_actions_server_compute_report_measures",
    "daadit_agent_hire.ir_actions_server_admin_models",
    "daadit_agent_hire.ir_actions_server_admin_fields",
    "daadit_agent_hire.ir_actions_server_admin_search",
    "daadit_helpdesk_agent_flow.ir_actions_server_similar_tickets",
    "daadit_helpdesk_agent_flow.ir_actions_server_helpdesk_rules",
    "daadit_project_agent_bridge.ir_actions_server_project_overrun",
    "daadit_sales_agent_bridge.ir_actions_server_lead_summary",
    "daadit_sales_agent_bridge.ir_actions_server_quotations_waiting",
)


def readonly_tool_names(env):
    """All tool names that only read: the stock set, the renamed
    read-only actions and the ``readonly_tools`` parameter."""
    names = set(_READONLY_TOOL_NAMES)
    langs = [code for code, _name in env["res.lang"].sudo().get_installed()]
    for xmlid in _READONLY_TOOL_XMLIDS:
        action = env.ref(xmlid, raise_if_not_found=False)
        if not action:
            continue
        action = action.sudo()
        for lang in langs or [None]:
            slug = _slug_tool_name(action.with_context(lang=lang).name)
            if slug:
                names.add(slug)
    param = env["ir.config_parameter"].sudo().get_param(
        _READONLY_CLASSIFY_PARAM, "",
    )
    for piece in (param or "").split(","):
        piece = piece.strip()
        if piece:
            names.add(piece)
    return frozenset(names)


# Tools that only read, but are ours rather than stock. These are used
# for CLASSIFICATION ONLY — deliberately not for the dry-run
# interception above, so a mistake here can never let a real write
# through in test mode.
#
# Why this exists: everything outside the stock set counted as a write,
# so a run that only called ``campaign_calendar`` reported "1 write
# action" — and ``claims_unverified`` (which fires on a work claim with
# zero writes) stayed silent. On 2-8-2026 Nova reported blogpost 82 as
# published in a run that never called publish_blogpost; the fabrication
# passed unflagged for exactly this reason. Verified read-only by
# reading each action's code: both only search and aggregate.
#
# Extendable without a deploy via the ``daadit_ai_agent_schedule.
# readonly_tools`` system parameter (comma-separated tool names).
_READONLY_CLASSIFY_PARAM = "daadit_ai_agent_schedule.readonly_tools"
_READONLY_CLASSIFY_ONLY = frozenset({
    "ir_actions_server_campaign_calendar_marketing_office",
    "ir_actions_server_campaign_performance_mark_kees",
    # Live Claude Design fetch — read-only brand context for content agents.
    "ir_actions_server_haal_claude_design",
    "ir_actions_server_ai_marketing_haal_claude_design",
})

# Verb fragments in a tool name that say what a write did. Only used
# when the tool result doesn't say it itself — see
# ``_compute_change_kind``. Ordered longest-intent-first so
# "unpublish" is not read as "publish".
_UNLINK_VERBS = ("delete", "unlink", "remove", "verwijder", "archive",
                 "archiveer", "unpublish", "depubliceer")
_CREATE_VERBS = ("create", "add", "new", "schedule", "plan", "maak",
                 "aanmaak", "nieuw", "post", "publish", "publiceer",
                 "send", "verstuur", "log_note")

# Een PUBLICATIE is een schrijfactie die iets echt naar buiten zet. We
# onderscheiden hem van een concept, een geplande post of een
# de-publicatie, want de bewering "gepubliceerd" mag alleen blijven
# staan als er ook echt live is gezet — een concept klaarzetten is geen
# publicatie. ``_NON_PUBLISH_VERBS`` gaat vóór ``_PUBLISH_VERBS``, zodat
# "depubliceer" en "schedule_..._post" niet als publicatie tellen.
_NON_PUBLISH_VERBS = ("draft", "concept", "unpublish", "depubliceer",
                      "schedule", "inplan", "plan")
_PUBLISH_VERBS = ("publish", "publiceer", "go_live")

_INTERVAL_SELECTION = [
    ("minutes", "Minutes"),
    ("hours", "Hours"),
    ("days", "Days"),
    ("weeks", "Weeks"),
    ("months", "Months"),
]


def _slug_tool_name(action_name):
    """Map an ``ir.actions.server`` name to the tool name the Mistral
    dispatch expects.

    Stock names its AI tool actions ``"AI: Search"``, ``"AI: Read group"``
    etc. on model ``ai.agent``; ``tool_dispatch`` maps
    ``ir_actions_server_<slug>`` → ``ai.agent._ai_tool_<slug>``. So:

        "AI: Read group"  → "ir_actions_server_read_group"
        "AI: Open Menu Kanban" → "ir_actions_server_open_menu_kanban"
    """
    name = (action_name or "").strip()
    # Drop a leading "AI:" / "AI CRM:" style prefix (everything up to and
    # including the first colon).
    if ":" in name:
        name = name.split(":", 1)[1]
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return "ir_actions_server_" + slug if slug else ""


def _action_tool_name(action):
    """Tool name for an ``ir.actions.server`` record, independent of the
    language of whoever triggers the run.

    ``name`` is translated: for a Dutch user "AI: Get Fields" reads
    "AI: Velden oproepen" and the slug became
    ``ir_actions_server_velden_oproepen`` — a name no classifier, scope
    list or readonly set knows. The source (English) name is stable.
    """
    return _slug_tool_name(action.with_context(lang="en_US").name)


class AiAgentSchedule(models.Model):
    _name = "daadit.ai.agent.schedule"
    _description = "AI Agent Schedule"
    _order = "active desc, name"

    name = fields.Char(
        string="Name", required=True,
        help="Label for this schedule (e.g. 'Hourly SLA sweep').",
    )
    active = fields.Boolean(string="Active", default=True)
    agent_id = fields.Many2one(
        "ai.agent", string="AI Agent", required=True, ondelete="cascade",
        index=True,
        help="The agent that will be run on this schedule.",
    )
    agent_llm_model = fields.Selection(
        related="agent_id.llm_model", string="LLM Model", readonly=True,
    )
    daily_cost_cap_eur = fields.Float(
        string="Dagbudget (EUR)", default=0.0,
        help="Maximale kosten per dag voor deze planning. 0 = geen "
             "eigen grens; dan gelden alleen de agent- en de "
             "systeemgrens.",
    )
    monthly_cost_cap_eur = fields.Float(
        string="Maandbudget (EUR)", default=0.0,
        help="Maximale kosten per kalendermaand voor deze planning. "
             "0 = geen eigen grens.",
    )
    prompt = fields.Text(
        string="Instruction", required=True,
        help="The message sent to the agent on every run. The agent's "
             "own system prompt still applies; this is the standing "
             "'user' instruction for the scheduled task.",
    )
    required_markers = fields.Text(
        string="Verplichte opbouw",
        help="Eén kopje per regel, bijvoorbeeld 'Gedaan:'. Mist een "
             "antwoord een van deze kopjes, dan krijgt de collega één "
             "herkansing zonder tools; mist het daarna nog steeds iets, "
             "dan wordt de run betwist en gaat er niets naar de klant.",
    )
    user_id = fields.Many2one(
        "res.users", string="Run as", required=True,
        default=lambda self: self.env.user,
        domain="[('share', '=', False)]",
        help="Scheduled runs execute with this user's access rights. "
             "Every tool the agent calls is subject to that user's "
             "Odoo permissions, record rules and company access.",
    )
    company_id = fields.Many2one(
        "res.company", string="Company",
        default=lambda self: self.env.company,
        help="Company context the run executes in.",
    )

    interval_number = fields.Integer(
        string="Every", default=1, required=True,
    )
    interval_type = fields.Selection(
        _INTERVAL_SELECTION, string="Interval Unit",
        default="hours", required=True,
    )
    nextcall = fields.Datetime(
        string="Next Run", required=True,
        default=fields.Datetime.now,
        help="The cron picks up this schedule once 'Next Run' is in the "
             "past. It is advanced automatically after each run.",
    )
    last_run = fields.Datetime(string="Last Run", readonly=True)
    last_status = fields.Selection(
        [("never", "Never run"),
         ("done", "Success"),
         ("error", "Error")],
        string="Last Status", default="never", readonly=True, copy=False,
    )

    run_ids = fields.One2many(
        "daadit.ai.agent.schedule.run", "schedule_id", string="Runs",
    )
    run_count = fields.Integer(
        string="# Runs", compute="_compute_run_count",
    )

    # Taak 736: het werkobject van deze planning. Leeg = de planning is
    # zijn eigen opdracht (het gedrag van vóór deze versie). Gevuld =
    # de taak wordt bij elke run atomair uitgecheckt, de agent krijgt de
    # doelketen van die taak mee en het rapport komt op de taak te staan.
    task_id = fields.Many2one(
        "project.task", string="Werkobject", ondelete="set null",
        help="De taak waar deze planning aan werkt. De taak wordt bij "
             "elke run uitgecheckt, zodat twee runs nooit hetzelfde "
             "werk oppakken, en het runrapport komt op de taak te "
             "staan in plaats van alleen in de runlog.",
    )

    # ------------------------------------------------------------------
    # Fase-0 guardrails: circuit breaker + failure alerting.
    #
    # The v19.0.2.1.0 commit surfaced Mistral-side cap-hits as run
    # errors but explicitly deferred these two items. This is that
    # follow-up.
    # ------------------------------------------------------------------
    failure_threshold = fields.Integer(
        string="Circuit breaker threshold", default=3, required=True,
        help="After this many consecutive failed runs the schedule is "
             "automatically disabled and the 'Notify on failure' users "
             "receive an email. Set to 0 to disable the circuit "
             "breaker (not recommended for writing agents).",
    )
    consecutive_failures = fields.Integer(
        string="Consecutive failures", default=0, readonly=True,
        copy=False,
        help="Failed runs since the last successful run. "
             "Reset to 0 on any successful run.",
    )
    alert_user_ids = fields.Many2many(
        "res.users", "daadit_ai_agent_schedule_alert_user_rel",
        "schedule_id", "user_id",
        string="Notify on failure",
        default=lambda self: [(6, 0, [self.env.user.id])],
        domain="[('share', '=', False)]",
        help="Internal users who receive an email when a scheduled run "
             "fails, and again when the circuit breaker trips.",
    )

    # v19.0.2.5.0: converted from ``_sql_constraints`` — Odoo 19's
    # registry warns "no longer supported" and silently SKIPS that
    # attribute, so these two checks were not being enforced at all.
    _interval_number_positive = models.Constraint(
        "CHECK(interval_number > 0)",
        "The interval must be a positive number.",
    )
    _failure_threshold_non_negative = models.Constraint(
        "CHECK(failure_threshold >= 0)",
        "The circuit breaker threshold cannot be negative "
        "(0 disables it, any positive number enables it).",
    )

    # ------------------------------------------------------------------
    @api.depends("run_ids")
    def _compute_run_count(self):
        data = self.env["daadit.ai.agent.schedule.run"]._read_group(
            [("schedule_id", "in", self.ids)],
            groupby=["schedule_id"], aggregates=["__count"],
        )
        counts = {sched.id: cnt for sched, cnt in data}
        for rec in self:
            rec.run_count = counts.get(rec.id, 0)

    @api.constrains("user_id")
    def _check_user_internal(self):
        for rec in self:
            if rec.user_id and rec.user_id.share:
                raise ValidationError(_(
                    "'Run as' must be an internal user — portal/public "
                    "users cannot run scheduled agents."
                ))

    @api.onchange("agent_id")
    def _onchange_agent_id(self):
        for rec in self:
            if rec.agent_id and not rec.name:
                rec.name = _("Schedule — %s") % rec.agent_id.name

    # ------------------------------------------------------------------
    # Recurrence helpers
    # ------------------------------------------------------------------
    def _compute_next_call(self, anchor=None):
        """Return the next run datetime = ``anchor`` + interval + jitter.

        Fase-0: the jitter (0..N seconds) spreads schedules that share
        a cadence so a batch of hourly schedules doesn't all fire on
        the exact top of the hour and starve the LLM provider with a
        spike (Governance & guardrails, knowledge id 173).
        """
        self.ensure_one()
        anchor = anchor or fields.Datetime.now()
        delta = relativedelta(**{self.interval_type: self.interval_number})
        jitter = timedelta(
            seconds=random.randint(0, _NEXTCALL_JITTER_SECONDS)
        )
        return anchor + delta + jitter

    def _claim_for_run(self):
        """Advance ``nextcall`` before running so a slow run can't be
        double-picked by the next cron tick. Committed by the caller."""
        self.ensure_one()
        now = fields.Datetime.now()
        self.write({
            "nextcall": self._compute_next_call(now),
            "last_run": now,
        })

    # ------------------------------------------------------------------
    # Tool list reconstruction
    # ------------------------------------------------------------------
    def _get_tool_names(self):
        """Stock tool-name strings for this schedule's agent.

        Covers the ``ir.actions.server`` tools defined on model
        ``ai.agent`` — the ``_ai_tool_*`` family, which every provider
        annotates with rich JSON-Schema parameters from its own
        ``TOOL_SCHEMAS``. Custom tools live on other models and are
        handled by :meth:`_get_custom_tool_defs` instead; they are
        returned here as ``skipped`` only for logging.
        """
        self.ensure_one()
        names, skipped = [], []
        actions = self.agent_id.topic_ids.tool_ids
        for action in actions:
            model = action.model_id.model if action.model_id else ""
            tool_name = _action_tool_name(action)
            if model == "ai.agent" and tool_name:
                if tool_name not in names:
                    names.append(tool_name)
            else:
                skipped.append("%s (%s)" % (action.name, model or "?"))
        return names, skipped

    def _get_custom_tool_defs(self):
        """Full tool definitions for the agent's CUSTOM tools.

        Custom tools are ``ir.actions.server`` records on a model other
        than ``ai.agent``: they have no ``_ai_tool_*`` method, so the
        provider dispatchers resolve them by the stock-advertised name
        ``action_<id>`` and execute the action body (see
        ``tool_dispatch._resolve_tool_action``).

        Scheduled runs used to drop these entirely, so an agent with
        custom tools (e.g. a content agent that may update a draft blog
        post) could do strictly less on a schedule than in chat — the
        model was told about none of its real work tools. We now
        advertise them with their own ``ai_tool_schema`` so the model
        knows the parameters instead of having to discover them through
        missing-argument errors.
        """
        self.ensure_one()
        defs = []
        for action in self.agent_id.sudo().topic_ids.tool_ids:
            model = action.model_id.model if action.model_id else ""
            if model == "ai.agent":
                continue  # stock tool, advertised by name
            params = None
            raw = getattr(action, "ai_tool_schema", None)
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict) and isinstance(
                        parsed.get("properties"), dict
                    ):
                        params = parsed
                except (ValueError, TypeError):
                    _logger.warning(
                        "daadit_ai_agent_schedule: custom tool %s "
                        "(action %s) has an unparseable ai_tool_schema "
                        "— advertising it without parameters",
                        action.name, action.id,
                    )
            if params is None:
                # No usable schema: advertise an empty object schema.
                # The dispatcher's missing-required-args feedback then
                # guides the model on the next turn.
                params = {"type": "object", "properties": {}, "required": []}
            defs.append({
                "type": "function",
                "function": {
                    "name": "action_%d" % action.id,
                    "description": (
                        action.ai_tool_description or action.name or ""
                    ),
                    "parameters": params,
                },
            })
        return defs

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    @api.model
    def _get_max_run_seconds(self):
        """Hard per-run time budget in seconds (Fase 0-gate).

        ICP ``daadit_ai_agent_schedule.max_run_seconds``, default 900.
        Enforced by the Mistral loop between round-trips (a run can
        overshoot by at most one LLM call + its tools) and by the
        stale-run sweeper for runs whose worker died mid-flight.
        """
        icp = self.env["ir.config_parameter"].sudo()
        try:
            val = int(icp.get_param(
                "daadit_ai_agent_schedule.max_run_seconds", "900",
            ) or 900)
        except (TypeError, ValueError):
            val = 900
        return max(60, val)

    def _daily_spend_eur(self, agent=None):
        """Wat er vandaag is uitgegeven, in euro.

        Zonder ``agent`` telt dit alleen deze planning; met ``agent``
        telt het alles wat die agent vandaag deed, ook zijn chatbeurten.
        """
        self.ensure_one()
        return self._spend_eur(
            self._today_start_utc(),
            agent=agent,
            schedule=None if agent is not None else self,
        )

    def _monthly_spend_eur(self, agent=None):
        """Wat er deze kalendermaand is uitgegeven, in euro."""
        self.ensure_one()
        return self._spend_eur(
            self._month_start_utc(),
            agent=agent,
            schedule=None if agent is not None else self,
        )

    @api.model
    def _spend_eur(self, since, agent=None, schedule=None):
        """Kosten sinds ``since``, in euro.

        Voor een agent telt dit álles wat hij deed — geplande runs én
        chatbeurten. De runtabel dekt alleen het eerste; de
        verbruiksregels van de providers dekken beide. Een agent die de
        hele dag in de chat hangt zou anders nooit tegen zijn grens
        aanlopen, en juist die beurten zijn de duurste.

        Optellen gebeurt in Python en niet met read_group: de kosten op
        een run zijn een related-veld op de verbruiksregel en dus niet
        opgeslagen. Het gaat om enkele tientallen rijen per dag, dus dat
        is goedkoop genoeg en het blijft leesbaar.
        """
        usd = 0.0
        if agent is not None:
            for model in _USAGE_MODELS:
                if model not in self.env:
                    continue
                rows = self.env[model].sudo().search(
                    self._spend_agent_domain(agent, model)
                    + [("create_date", ">=", since)]
                )
                usd += sum(r.estimated_cost_usd or 0.0 for r in rows)
        else:
            runs = self.env["daadit.ai.agent.schedule.run"].sudo().search([
                ("start_date", ">=", since),
                ("schedule_id", "=", schedule.id),
            ])
            usd = sum(r.estimated_cost_usd or 0.0 for r in runs)
        icp = self.env["ir.config_parameter"].sudo()
        try:
            rate = float(icp.get_param("daadit_ai_mistral.usd_eur_rate") or 0)
        except (TypeError, ValueError):
            rate = 0.0
        return usd * (rate if rate > 0 else 0.92)

    @api.model
    def _spend_agent_domain(self, agent, usage_model):
        """Domein op ``usage_model`` van wat ``agent`` uitgaf.

        Haakje: een module die één agent voor meer opdrachtgevers laat
        werken telt hier alleen wat hij voor de opdrachtgever uit de
        context van ``agent`` uitgaf.
        """
        return [("agent_id", "=", agent.id)]

    @api.model
    def _default_caps_eur(self):
        """Standaardgrenzen per agent, als er geen eigen grens staat.

        Een agent wordt verkocht voor €599 per maand. Bij €45 per maand
        aan modelkosten blijft daar ruim 92% van over; dat is de marge
        waar de investering op gebaseerd is. Nul zetten schakelt de
        standaard uit — dan geldt alleen wat er op de agent zelf staat.
        """
        icp = self.env["ir.config_parameter"].sudo()

        def _param(name, fallback):
            try:
                return float(icp.get_param(name) or fallback)
            except (TypeError, ValueError):
                return fallback

        return {
            "daily": _param(
                "daadit_ai_agent_schedule.default_agent_daily_cap_eur", 8.0),
            "monthly": _param(
                "daadit_ai_agent_schedule.default_agent_monthly_cap_eur", 45.0),
        }

    @api.model
    def _fair_use_warn_ratio(self):
        icp = self.env["ir.config_parameter"].sudo()
        try:
            val = float(icp.get_param(
                "daadit_ai_agent_schedule.fair_use_warn_ratio") or 0.6)
        except (TypeError, ValueError):
            val = 0.6
        return min(max(val, 0.1), 0.99)

    @api.model
    def _month_start_utc(self):
        """Eerste van de maand, middernacht in de tijdzone van de
        gebruiker, als naive UTC."""
        try:
            import pytz
            tz = pytz.timezone(self.env.user.tz or "Europe/Amsterdam")
            now_local = fields.Datetime.now().replace(
                tzinfo=pytz.utc
            ).astimezone(tz)
            start_local = tz.localize(datetime.combine(
                now_local.date().replace(day=1), dtime.min
            ))
            return start_local.astimezone(pytz.utc).replace(tzinfo=None)
        except Exception:  # noqa: BLE001
            now = fields.Datetime.now()
            return datetime.combine(now.date().replace(day=1), dtime.min)

    @api.model
    def _today_start_utc(self):
        """Middernacht in de tijdzone van de gebruiker, als naive UTC."""
        try:
            import pytz
            tz = pytz.timezone(self.env.user.tz or "Europe/Amsterdam")
            now_local = fields.Datetime.now().replace(
                tzinfo=pytz.utc
            ).astimezone(tz)
            start_local = tz.localize(datetime.combine(
                now_local.date(), dtime.min
            ))
            return start_local.astimezone(pytz.utc).replace(tzinfo=None)
        except Exception:  # noqa: BLE001
            now = fields.Datetime.now()
            return datetime.combine(now.date(), dtime.min)

    @api.model
    def _agent_budget_state(self, agent):
        """Budgetstand van één agent: verbruik, grenzen, en wat dat betekent.

        Vier grenzen, de strengste wint: dag en maand per agent, en dag
        en maand voor de hele vestiging. Eén database is één klant, dus
        de vestigingsgrens is de klantgrens — dat is de grens die de
        marge op €599 per agent per maand bewaakt, terwijl de
        agentgrens voorkomt dat één doorgeslagen collega het budget van
        alle andere opeet.

        Retourneert::

            {"ratio": 0.0..n, "blocked": bool, "message": str|False,
             "scope": "agent-dag"|"agent-maand"|"vestiging-dag"|...,
             "spent": float, "cap": float}

        ``ratio`` is het hoogste verbruik als fractie van zijn grens.
        Vanaf de fair-use-drempel hoort de gebruiker het te merken;
        vanaf 1.0 gaat de kraan dicht.
        """
        if not agent:
            return {"ratio": 0.0, "blocked": False, "message": False,
                    "scope": False, "spent": 0.0, "cap": 0.0}
        day_start = self._today_start_utc()
        month_start = self._month_start_utc()

        agent_day_cap, agent_month_cap = self._agent_caps_eur(agent)
        tenant_day_cap = self._tenant_cap_eur("daily")
        tenant_month_cap = self._tenant_cap_eur("monthly")

        checks = []
        if agent_day_cap > 0:
            checks.append((
                "agent-dag", self._spend_eur(day_start, agent=agent),
                agent_day_cap,
                _("Dagbudget van %s") % (agent.name or _("deze collega")),
            ))
        if agent_month_cap > 0:
            checks.append((
                "agent-maand", self._spend_eur(month_start, agent=agent),
                agent_month_cap,
                _("Maandbudget van %s") % (agent.name or _("deze collega")),
            ))
        if tenant_day_cap > 0:
            checks.append((
                "vestiging-dag", self._tenant_spend_eur(day_start),
                tenant_day_cap, _("Dagbudget van deze vestiging"),
            ))
        if tenant_month_cap > 0:
            checks.append((
                "vestiging-maand", self._tenant_spend_eur(month_start),
                tenant_month_cap, _("Maandbudget van deze vestiging"),
            ))
        if not checks:
            return {"ratio": 0.0, "blocked": False, "message": False,
                    "scope": False, "spent": 0.0, "cap": 0.0}

        scope, spent, cap, label = max(
            checks, key=lambda c: (c[1] / c[2]) if c[2] else 0.0
        )
        ratio = (spent / cap) if cap else 0.0
        message = False
        if ratio >= 1.0:
            message = _(
                "%(label)s is bereikt: %(spent).2f van %(cap).2f EUR. "
                "Geplande taken staan tot de volgende periode stil; in "
                "het gesprek kan ik nog wel meedenken, maar geen "
                "acties meer uitvoeren in Odoo."
            ) % {"label": label, "spent": spent, "cap": cap}
        elif ratio >= self._fair_use_warn_ratio():
            message = _(
                "Fair use: %(label)s staat op %(pct)d%% "
                "(%(spent).2f van %(cap).2f EUR). Bij 100%% pauzeert "
                "deze collega tot de volgende periode."
            ) % {"label": label, "pct": int(ratio * 100),
                 "spent": spent, "cap": cap}
        return {
            "ratio": ratio,
            "blocked": ratio >= 1.0,
            "message": message,
            "scope": scope,
            "spent": spent,
            "cap": cap,
        }

    @api.model
    def _agent_caps_eur(self, agent):
        """(daggrens, maandgrens) in euro voor deze agent; 0 = geen grens.

        Haakje: een module die één agent voor meer opdrachtgevers laat
        werken leest hier de grens van de opdrachtgever uit de context.
        """
        defaults = self._default_caps_eur()
        day_cap = (
            getattr(agent, "daadit_daily_cost_cap_eur", 0.0) or 0.0
        ) or defaults["daily"]
        month_cap = (
            getattr(agent, "daadit_monthly_cost_cap_eur", 0.0) or 0.0
        ) or defaults["monthly"]
        return day_cap, month_cap

    @api.model
    def _tenant_cap_eur(self, period):
        """Grens voor de hele vestiging (= deze database = deze klant).

        Eén database per klant, dus er is geen company-as nodig: alles
        wat in deze database aan AI wordt uitgegeven, is van deze klant.
        0 = geen vestigingsgrens.

        Staat er een abonnement ingesteld, dan is de maandgrens niet
        langer een los bedrag maar volgt hij uit het pakket: de
        inbegrepen werk-eenheden plus de toegestane overschrijding.
        Zo bewaakt dezelfde meter zowel de kosten als de facturering.
        """
        icp = self.env["ir.config_parameter"].sudo()
        if period == "monthly":
            plan = self._tenant_plan()
            if plan["units_included"] > 0:
                return (
                    plan["units_included"] * plan["unit_cost_eur"]
                    * plan["hard_stop_ratio"]
                )
        name = "daadit_ai_agent_schedule.tenant_%s_cap_eur" % period
        try:
            return float(icp.get_param(name) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @api.model
    def _tenant_plan(self):
        """Het abonnement van deze vestiging, in werk-eenheden.

        Klanten betalen niet per agent maar naar gebruik: een kleine
        vestiging die twee vragen per dag stelt hoort niet hetzelfde te
        betalen als een die de hele dag laat doorwerken. De meeteenheid
        is een *werk-eenheid* — grofweg één agentbeurt van gemiddelde
        zwaarte — en niet een token: tokens zijn onverkoopbaar en
        verschillen een factor 15 tussen modellen, waardoor de klant
        zou meebetalen aan onze modelkeuze in plaats van aan geleverd
        werk. Kiezen wij intern een goedkoper model, dan groeit de
        marge zonder dat de prijs of de rekening verandert.

        Alle waarden staan in ir.config_parameter zodat de prijzen te
        wijzigen zijn zonder release.
        """
        icp = self.env["ir.config_parameter"].sudo()

        def _param(name, fallback):
            try:
                return float(icp.get_param(
                    "daadit_ai_agent_schedule." + name) or fallback)
            except (TypeError, ValueError):
                return fallback

        return {
            "name": icp.get_param(
                "daadit_ai_agent_schedule.plan_name") or _("Onbekend pakket"),
            "units_included": _param("plan_units_included", 0.0),
            # Interne kostprijs van één werk-eenheid. Gemeten over 210
            # echte calls: gemiddeld $0,021, dus ~€0,02.
            "unit_cost_eur": _param("plan_unit_cost_eur", 0.02) or 0.02,
            # Wat de klant betaalt boven zijn bundel.
            "overage_price_eur": _param("plan_overage_price_eur", 0.15),
            # Tot hoever boven de bundel we door laten lopen voordat de
            # kraan dichtgaat. 2.0 = tot het dubbele; dan is er iets
            # structureel mis en is doorgaan geen dienstverlening meer.
            "hard_stop_ratio": _param("plan_hard_stop_ratio", 2.0) or 2.0,
        }

    @api.model
    def _tenant_units_used(self, since=None):
        """Verbruikte werk-eenheden sinds ``since`` (default: deze maand).

        Eén eenheid is de gemiddelde kostprijs van een agentbeurt, dus
        een zware beurt telt zwaarder dan een lichte. Dat is precies de
        bedoeling: de klant die grote analyses laat draaien verbruikt
        meer van zijn bundel dan de klant die een adres opzoekt.
        """
        plan = self._tenant_plan()
        spend = self._tenant_spend_eur(since or self._month_start_utc())
        return spend / plan["unit_cost_eur"]

    @api.model
    def tenant_usage_state(self):
        """Wat deze vestiging deze maand verbruikte, in klanttaal.

        Gebruikt door de fair-use-melding, het klantoverzicht en de
        facturering — één bron, zodat wat de klant in de chat leest
        hetzelfde is als wat er op zijn rekening komt.
        """
        plan = self._tenant_plan()
        used = self._tenant_units_used()
        included = plan["units_included"]
        over = max(0.0, used - included) if included > 0 else 0.0
        return {
            "plan": plan["name"],
            "units_used": round(used, 1),
            "units_included": included,
            "units_over": round(over, 1),
            "overage_eur": round(over * plan["overage_price_eur"], 2),
            "ratio": (used / included) if included > 0 else 0.0,
        }

    @api.model
    def _tenant_spend_eur(self, since):
        """Alles wat er sinds ``since`` in deze database aan AI is
        uitgegeven, in euro — alle agents, chat en planning."""
        usd = 0.0
        for model in _USAGE_MODELS:
            if model not in self.env:
                continue
            rows = self.env[model].sudo().search([
                ("create_date", ">=", since),
            ])
            usd += sum(r.estimated_cost_usd or 0.0 for r in rows)
        icp = self.env["ir.config_parameter"].sudo()
        try:
            rate = float(icp.get_param("daadit_ai_mistral.usd_eur_rate") or 0)
        except (TypeError, ValueError):
            rate = 0.0
        return usd * (rate if rate > 0 else 0.92)

    def _placement_block_reason(self):
        """De reden dat deze planning voor niemand mag werken, of None.

        Een plaatsing bij een klant die (nog) niet aan het werk mag —
        bijvoorbeeld omdat hij wacht op betaling — levert hier een reden
        op. Anders dan een budgetstop laat dat geen run achter en zet het
        de wachtrij van de agent niet stil: dezelfde collega werkt door
        voor haar andere klanten.
        """
        self.ensure_one()
        return None

    def _budget_block_reason(self):
        """De reden om deze run niet te starten, of None.

        De planningsgrenzen staan naast de agent- en vestigingsgrenzen:
        een planning die per ongeluk elke vijf minuten draait wordt
        hier gestopt voordat hij het budget van de hele agent opmaakt.
        """
        self.ensure_one()
        cap = self.daily_cost_cap_eur or 0.0
        if cap > 0:
            spent = self._daily_spend_eur()
            if spent >= cap:
                return _(
                    "Dagbudget van deze planning bereikt: "
                    "%(spent).4f van %(cap).2f EUR."
                ) % {"spent": spent, "cap": cap}
        month_cap = self.monthly_cost_cap_eur or 0.0
        if month_cap > 0:
            spent = self._monthly_spend_eur()
            if spent >= month_cap:
                return _(
                    "Maandbudget van deze planning bereikt: "
                    "%(spent).4f van %(cap).2f EUR."
                ) % {"spent": spent, "cap": month_cap}
        state = self._agent_budget_state(
            self.agent_id.with_context(**self._run_context())
        )
        if state["blocked"]:
            return state["message"]
        return None

    def _budget_siblings_domain(self):
        """Domein van de planningen die dezelfde budgetmeter delen."""
        self.ensure_one()
        return [("agent_id", "=", self.agent_id.id)]

    def _pause_agent_queue(self, reason):
        """Schuif al het geplande werk van deze agent naar de volgende
        periode, en geef terug hoeveel planningen dat waren.

        Bij een maandgrens is dat de eerste van de volgende maand, bij
        een daggrens morgenochtend. De planning wordt niet uitgezet: hij
        hervat vanzelf zodra de teller op nul staat, zodat een
        budgetstop geen handmatige heractivatie vraagt.
        """
        self.ensure_one()
        state = self._agent_budget_state(
            self.agent_id.with_context(**self._run_context())
        )
        monthly = (state.get("scope") or "").endswith("maand")
        if monthly:
            resume = (
                self._month_start_utc() + relativedelta(months=1)
            ) + timedelta(minutes=5)
        else:
            resume = self._today_start_utc() + timedelta(days=1, minutes=5)
        queued = self.sudo().search(
            self._budget_siblings_domain() + [("active", "=", True)],
        )
        moved = queued.filtered(
            lambda s: not s.nextcall or s.nextcall < resume
        )
        if moved:
            try:
                moved.write({"nextcall": resume})
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "daadit_ai_agent_schedule: kon de wachtrij van agent "
                    "%s niet verschuiven na budgetstop",
                    self.agent_id.id,
                )
                return 0
        _logger.warning(
            "daadit_ai_agent_schedule: budgetstop voor agent %s — %d "
            "planning(en) verschoven naar %s (%s)",
            self.agent_id.id, len(moved), resume, reason,
        )
        return len(moved)

    def _notify_budget_stop(self, run, reason):
        """Eén mail per agent per periode, niet één per planning.

        Een agent met acht planningen zou anders acht identieke mails
        sturen op de dag dat zijn budget op is — waarna niemand ze nog
        leest.
        """
        self.ensure_one()
        Run = self.env["daadit.ai.agent.schedule.run"].sudo()
        state = self._agent_budget_state(
            self.agent_id.with_context(**self._run_context())
        )
        monthly = (state.get("scope") or "").endswith("maand")
        since = (
            self._month_start_utc() if monthly else self._today_start_utc()
        )
        earlier = Run.search_count([
            ("schedule_id", "in", self.sudo().with_context(
                active_test=False,
            ).search(self._budget_siblings_domain()).ids),
            ("state", "=", "budget"),
            ("start_date", ">=", since),
            ("id", "!=", run.id),
        ])
        if earlier:
            return False
        users = self.sudo().alert_user_ids
        emails = sorted({u.email for u in users if u.email})
        if not emails:
            return False
        usage = self.tenant_usage_state()
        from markupsafe import escape as _escape
        body_html = (
            "<p>%(intro)s</p>"
            "<ul>"
            "<li><b>Collega:</b> %(agent)s</li>"
            "<li><b>Reden:</b> %(reason)s</li>"
            "<li><b>Verbruik deze maand:</b> %(used)s van %(incl)s "
            "werk-eenheden</li>"
            "<li><b>Boven de bundel:</b> %(over)s eenheden "
            "(\u20ac%(overage).2f)</li>"
            "</ul>"
            "<p>%(outro)s</p>"
        ) % {
            "intro": _escape(_(
                "Een AI-collega heeft zijn budget bereikt en is "
                "gepauzeerd. Zijn geplande taken zijn doorgeschoven "
                "naar de volgende periode; in het gesprek kan hij nog "
                "wel antwoorden, maar geen acties meer uitvoeren."
            )),
            "agent": _escape(self.agent_id.name or ""),
            "reason": _escape(reason or ""),
            "used": _escape(str(usage["units_used"])),
            "incl": _escape(str(usage["units_included"] or "—")),
            "over": _escape(str(usage["units_over"])),
            "overage": usage["overage_eur"],
            "outro": _escape(_(
                "Verhoog het budget op de agent, of stap de klant een "
                "pakket omhoog als dit structureel is."
            )),
        }
        try:
            self.env["mail.mail"].sudo().create({
                "subject": _("[Budget] %s is gepauzeerd") % (
                    self.agent_id.name or _("een AI-collega")
                ),
                "body_html": body_html,
                "email_to": ", ".join(emails),
                "auto_delete": True,
            }).send()
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_agent_schedule: budgetmelding kon niet worden "
                "verstuurd voor agent %s", self.agent_id.id,
            )
            return False
        return True

    def _execute(self, trigger="cron"):
        """Run the agent once and record a run row.

        Fase-0 run-lock (gate punt 8): EVERY execution path — cron,
        'Run now', test run — takes the same global advisory lock, so
        at most one agent run executes at any moment across all
        workers. The cron entrypoint already holds the lock on this
        session (advisory locks are reentrant), so its path is
        unchanged; a manual/test run while another run is busy gets a
        clear UserError instead of running in parallel (live
        waargenomen: runs 31∥32).

        Never raises for cron triggers — failures are captured on the
        run record so the cron loop keeps going.
        """
        self.ensure_one()
        if trigger != "test":
            refused = self._placement_block_reason()
            if refused:
                _logger.info(
                    "daadit_ai_agent_schedule: schedule %s niet gestart — %s",
                    self.id, refused,
                )
                if trigger == "manual":
                    raise UserError(refused)
                return self.env["daadit.ai.agent.schedule.run"]
        # Budget vóór de lock: een geblokkeerde run hoeft niet te wachten
        # op een andere run om te mogen constateren dat hij niet mag.
        reason = self._budget_block_reason()
        if reason:
            _logger.warning(
                "daadit_ai_agent_schedule: schedule %s overgeslagen — %s",
                self.id, reason,
            )
            # Een geblokkeerde run laat een spoor na. Zonder dat spoor
            # ziet niemand het verschil tussen "de agent heeft niets te
            # doen" en "de agent mag niet meer" — en dat verschil is
            # precies waar een klant over belt.
            run = self.env["daadit.ai.agent.schedule.run"].sudo().create({
                "schedule_id": self.id,
                "trigger": trigger,
                "user_id": self.user_id.id,
                "company_id": self.company_id.id,
                "state": "budget",
                "start_date": fields.Datetime.now(),
                "end_date": fields.Datetime.now(),
                "model": self.agent_id.llm_model or "",
                "error": reason,
            })
            if trigger == "cron":
                # Uitputting stopt niet één run maar de hele wachtrij:
                # alle planningen van deze agent schuiven door tot na de
                # volgende periodegrens. Alleen de eerstvolgende call
                # weigeren betekent dat de cron elke tick opnieuw
                # aanklopt en de agent bij de eerste euro ruimte weer
                # vol doorgaat.
                self._pause_agent_queue(reason)
                self._notify_budget_stop(run, reason)
                return run
            raise UserError(reason)

        cr = self.env.cr
        cr.execute(
            "SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_ID,)
        )
        if not cr.fetchone()[0]:
            if trigger == "cron":
                # Defensive: the cron entrypoint holds the lock on this
                # session, so this branch should be unreachable.
                _logger.warning(
                    "daadit_ai_agent_schedule: run lock busy for cron "
                    "execution of schedule %s — skipping this tick",
                    self.id,
                )
                return self.env["daadit.ai.agent.schedule.run"]
            raise UserError(_(
                "Er draait op dit moment al een agent-run (geplande "
                "runs draaien maximaal één tegelijk). Probeer het "
                "over een paar minuten opnieuw."
            ))
        try:
            return self._execute_locked(trigger=trigger)
        finally:
            try:
                cr.execute(
                    "SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_ID,)
                )
                cr.fetchone()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "daadit_ai_agent_schedule: failed to release run "
                    "lock after execution — cursor close should still "
                    "free it"
                )

    def _execute_locked(self, trigger="cron"):
        """Body of :meth:`_execute` — the caller holds the run lock."""
        self.ensure_one()
        Run = self.env["daadit.ai.agent.schedule.run"].sudo()
        run = Run.create(self._run_vals(trigger))

        # Taak 736: uitchecken vóór het werk. De taak is het werkobject,
        # dus twee runs die hem tegelijk willen oppakken mogen niet
        # beide doorgaan — de claim zit in de WHERE van één UPDATE, dus
        # de database wijst de winnaar aan.
        # Een testrun schrijft niets, dus die checkt ook niets uit: hij
        # leest de taak wel (voor de doelketen in de opdracht) maar laat
        # status en claim ongemoeid.
        task = self.env["project.task"] if trigger == "test" else self.task_id
        if task and not task._ai_try_claim(
            run, self._get_max_run_seconds() * 3,
        ):
            busy = _(
                "Taak %(name)s (#%(id)s) is al uitgecheckt door een "
                "andere run; deze run doet niets zodat hetzelfde werk "
                "niet dubbel gebeurt.",
                name=task.display_name, id=task.id,
            )
            run.write({
                "state": "done",
                "end_date": fields.Datetime.now(),
                "findings": busy,
            })
            _logger.info(
                "daadit_ai_agent_schedule: schedule %s — task %s already "
                "claimed, run %s stopped before starting",
                self.id, task.id, run.id,
            )
            return run

        from ..services import provider_bridge, run_capture

        agent = self.agent_id.with_context(**self._run_context())
        # Provider-agnostic since v19.0.3.0.0: resolve the provider the
        # same way live chat does (ai.agent._get_provider — the Mistral
        # module's override returns 'mistral' for its models; stock
        # returns 'openai' / 'google'). Any model the agent form
        # accepts can be scheduled.
        try:
            provider = agent._get_provider()
        except Exception as exc:  # noqa: BLE001
            run._mark_error(_(
                "Could not resolve an LLM provider for agent "
                "'%(agent)s' (model '%(model)s'): %(err)s",
                agent=agent.name, model=agent.llm_model or "?",
                err=exc,
            ))
            self._post_run_status(run)
            return run
        # Resolve which DAADit provider add-on serves this provider
        # (daadit_ai_mistral / daadit_ai_loes / daadit_ai_claude / a
        # future one), or a NullBridge for stock providers. See
        # services/provider_bridge.py — adding a provider needs no
        # change here as long as it keeps the family conventions.
        llm_model = self._run_llm_model(agent)
        bridge = provider_bridge.resolve(self.env, provider, llm_model)
        run.provider = provider or ""

        tool_names, skipped = self._get_tool_names()
        custom_tool_defs = self._get_custom_tool_defs()
        # Combine only when the provider can annotate the stock names
        # itself: mixing plain names and full defs in one list makes a
        # provider fall back to its generic normaliser, which would
        # strip the rich stock schemas. All-dicts keeps both rich.
        tools_arg = tool_names
        if custom_tool_defs:
            annotated = bridge.annotate_tools(tool_names)
            if annotated or not tool_names:
                tools_arg = list(annotated) + custom_tool_defs
            else:
                _logger.info(
                    "daadit_ai_agent_schedule: schedule %s — provider "
                    "%r cannot annotate tool names, so %d custom tool(s) "
                    "stay unadvertised this run",
                    self.id, provider, len(custom_tool_defs),
                )
        if skipped:
            _logger.info(
                "daadit_ai_agent_schedule: schedule %s — %d custom tool "
                "action(s) advertised as action_<id>: %s",
                self.id, len(skipped), ", ".join(skipped),
            )

        messages = []
        sys_prompt = agent._daadit_apply_werkafspraken(
            (self._run_system_prompt(agent) or "").strip(),
        )
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})
        instructions = [self._run_prompt(), claim_ledger.INSTRUCTION]
        if self._has_knowledge_sources(agent):
            instructions.append(source_citation.INSTRUCTION)
        messages.append({
            "role": "user", "content": "\n\n".join(instructions),
        })

        run_capture.install(bridge.tool_dispatch)
        for dispatch in provider_bridge.installed_dispatchers(self.env):
            run_capture.install(dispatch)
        bridge.patch_llm_service()

        # Fase-0 API-key-splitsing: markeer deze env als batch-verkeer.
        # Elke provider leest zijn eigen vlag (daadit_<code>_batch) en
        # gebruikt dan de aparte batch-key uit Instellingen -> AI, zodat
        # geplande runs de klant-facing key (Ask AI / Milo) nooit kunnen
        # leegtrekken.
        run_env = self.with_context(
            **self._run_context(), **bridge.batch_context()
        ).env(user=self.user_id.id)
        run_agent = run_env["ai.agent"].with_company(
            self.company_id
        ).browse(agent.id)

        # Snapshot the latest provider usage id so we can link the row
        # the run creates (best-effort — runs are serialised per
        # schedule). Providers without a usage model simply have none.
        usage_model = bridge.usage_model
        prev_usage_ids = {}
        for model_name in set(_USAGE_MODELS) | {usage_model}:
            if model_name and model_name in self.env:
                prev_usage = self.env[model_name].sudo().search(
                    [], order="id desc", limit=1,
                )
                prev_usage_ids[model_name] = prev_usage.id
        prev_usage_id = prev_usage_ids.get(usage_model, 0)

        # Test run (dry mode): read/search tools execute normally so
        # the agent reasons on real data, but anything outside
        # ``_READONLY_TOOL_NAMES`` is intercepted by run_capture and
        # answered with a simulated success — nothing is written to
        # the system. The run log still records every intended action.
        is_test = trigger == "test"
        # Mistral runs are captured at the dispatcher chokepoint
        # (run_tool_call — also sees hallucinated names). Stock
        # providers (OpenAI/Gemini) never pass through that dispatcher,
        # so those runs capture at the _ai_tool_* method level instead.
        # method_capture=False for Mistral prevents double records.
        readonly_tools = readonly_tool_names(self.env)
        readonly_methods = frozenset(
            "_ai_tool_" + n[len("ir_actions_server_"):]
            for n in readonly_tools
        )
        run_capture.install_method_hooks(type(run_agent))
        # Cheap no-op once armed; covers the case where this add-on
        # loaded before the provider did.
        run_capture.install_turn_observer()
        run_capture.install_delegation_observer()
        run_capture.start_capture(
            test_mode=is_test,
            readonly_tools=readonly_tools,
            # Providers with their own dispatcher are captured there
            # (richer: also sees hallucinated tool names and policy
            # denials). Everything else captures at the _ai_tool_*
            # method level. Never both — that would double-record.
            method_capture=not bridge.has_dispatcher,
            readonly_methods=readonly_methods,
        )
        # Alles wat de run lokaal wijzigt staat achter één savepoint:
        # loopt de run vast, dan blijft er geen halve keten staan.
        savepoint = None if is_test else self.env.cr.savepoint()
        rollback_marks = {} if is_test else run._rollback_marks()
        max_run_seconds = self._get_max_run_seconds()
        try:
            bridge.set_current_agent(run_agent)
            # Fase-0 harde totaal-timeout per run: providerlussen die
            # deze monotonic deadline ondersteunen breken tussen
            # round-trips af met reason='deadline'. Gewist in de finally
            # zodat interactieve chat op dezelfde worker-thread er nooit
            # een verlopen deadline van erft. Providers zonder
            # router_state negeren dit stilzwijgend.
            bridge.set_deadline(time.monotonic() + max_run_seconds)
            # v19.0.2.1.0: Fase 0 governance (Knowledge id 173). Reset
            # the top-level exhaustion flag a provider sets when its
            # tool-call loop bails on the iteration cap or hallucinated
            # tool names. Without the reset, exhausted state from a
            # previous run on the same worker thread would leak into
            # this run and mark it 'error' spuriously.
            bridge.reset_exhaustion()
            bridge.reset_fallback()
            try:
                from odoo.addons.ai.utils.llm_api_service import (
                    LLMApiService,
                )
            except ImportError as exc:
                raise UserError(_(
                    "Odoo AI LLMApiService not importable (%s).", exc,
                ))
            service = LLMApiService(env=run_env, provider=provider)
            # Stock request_llm's exact signature is closed-source and
            # differs per Odoo release; pass only kwargs it accepts so
            # a stock provider never dies on an unexpected keyword.
            import inspect as _inspect
            call_kwargs = {
                "model": llm_model,
                "inputs": messages,
                "tools": tools_arg,
            }
            try:
                params = _inspect.signature(service.request_llm).parameters
                has_var_kw = any(
                    p.kind == p.VAR_KEYWORD for p in params.values()
                )
                if not has_var_kw:
                    call_kwargs = {
                        k: v for k, v in call_kwargs.items() if k in params
                    }
            except (TypeError, ValueError):
                pass
            findings = self._llm_text(service.request_llm(**call_kwargs))
            findings, missing = self._enforce_structure(
                service, call_kwargs, findings, run, bridge,
            )
            findings = self._enforce_citations(
                service, call_kwargs, findings, run, bridge, agent,
            )
            if savepoint:
                run._close_run_savepoint(
                    savepoint, rollback_marks,
                    rollback=bridge.read_exhaustion()[0],
                )
            actions = run_capture.stop_capture()
            run._record_actions(actions)
            run._record_delegations(run_capture.take_delegations())
            run._link_usage(prev_usage_id, usage_model)
            run._record_fallback(bridge, prev_usage_ids)
            # v19.0.2.1.0: check whether the Mistral tool-call loop
            # bailed out (MAX_ITER hit or hallucinated tools). Without
            # this the schedule.run always ended 'done' with the cap-
            # hit sentence sitting in `findings` — misleading because
            # the agent didn't actually complete its task.
            exhausted, reason = bridge.read_exhaustion()
            if exhausted:
                if reason == "deadline":
                    err_msg = _(
                        "Gestopt: harde tijdslimiet van %s seconden "
                        "per run bereikt. Verklein de opdracht of "
                        "verhoog 'daadit_ai_agent_schedule."
                        "max_run_seconds' (Instellingen → Technisch → "
                        "Systeemparameters)."
                    ) % max_run_seconds
                elif reason == "max_iter":
                    err_msg = _(
                        "Gestopt: het maximum aantal LLM-calls voor "
                        "deze run is bereikt. Vereenvoudig de agent-"
                        "prompt, verklein het bereik van zijn tools, "
                        "of verhoog de iteratielimiet van de provider "
                        "(Instellingen → Technisch → Systeemparameters, "
                        "'daadit_ai_%s.max_tool_iterations').",
                        bridge.code or "mistral",
                    )
                elif reason == "unknown_tools":
                    err_msg = _(
                        "Gestopt: de agent probeerde herhaaldelijk "
                        "niet-bestaande tools aan te roepen. Check "
                        "de topic-configuratie van de agent."
                    )
                else:
                    err_msg = _(
                        "Gestopt: LLM-lus vroegtijdig afgebroken."
                    )
                run.write({
                    "state": "error",
                    "end_date": fields.Datetime.now(),
                    "error": err_msg,
                    "findings": (findings or "") + run._factual_footer(),
                })
                _logger.warning(
                    "daadit_ai_agent_schedule: run %s for schedule %s "
                    "bailed out reason=%s — marked as error",
                    run.id, self.id, reason,
                )
            else:
                run.write({
                    "state": "done",
                    "end_date": fields.Datetime.now(),
                    "findings": (findings or _(
                        "(The agent returned no text.)"
                    )) + run._factual_footer(),
                })
                run._apply_claim_ledger()
                if missing and run.state == "done":
                    run.write({"state": "disputed"})
                    _logger.warning(
                        "daadit_ai_agent_schedule: run %s betwist — "
                        "verplichte opbouw ontbreekt na herkansing: %s",
                        run.id, ", ".join(missing),
                    )
        except Exception as exc:  # noqa: BLE001
            if savepoint:
                run._close_run_savepoint(
                    savepoint, rollback_marks, rollback=True,
                )
            actions = run_capture.stop_capture()
            run._record_actions(actions)
            run._record_delegations(run_capture.take_delegations())
            run._link_usage(prev_usage_id, usage_model)
            run._record_fallback(bridge, prev_usage_ids)
            _logger.exception(
                "daadit_ai_agent_schedule: run %s for schedule %s failed",
                run.id, self.id,
            )
            run._mark_error(
                "%s: %s\n\n%s" % (
                    type(exc).__name__, exc, traceback.format_exc(),
                )
            )
        finally:
            if savepoint and not savepoint.closed:
                run._close_savepoint(savepoint, rollback=False)
            run_capture.stop_capture()
            run_capture.take_delegations()
            bridge.clear_deadline()
            bridge.reset_fallback()
            bridge.clear_current_agent()
            if task:
                # Vrijgeven hoort in de finally: een run die omvalt mag
                # de taak niet tot de verjaringstermijn vasthouden.
                try:
                    task._ai_release(
                        run,
                        "failed" if run.state == "error" else "done",
                    )
                except Exception:  # noqa: BLE001
                    _logger.exception(
                        "daadit_ai_agent_schedule: kon taak %s niet "
                        "vrijgeven na run %s", task.id, run.id,
                    )

        self._post_run_status(run)
        return run

    def _run_vals(self, trigger):
        """De waarden waarmee de run van deze planning wordt aangemaakt."""
        self.ensure_one()
        return {
            "schedule_id": self.id,
            "trigger": trigger,
            "user_id": self.user_id.id,
            "company_id": self.company_id.id,
            "state": "running",
            "start_date": fields.Datetime.now(),
            "model": self._run_llm_model(self.agent_id),
            "task_id": self.task_id.id,
        }

    def _run_llm_model(self, agent):
        """Het model voor deze run: dat van de agent, of het model dat de
        kwaliteitsmeting per model opgeeft."""
        self.ensure_one()
        return (
            self.env.context.get("daadit_eval_llm_model")
            or agent.llm_model or ""
        )

    @staticmethod
    def _llm_text(result):
        if isinstance(result, (list, tuple)):
            return "\n\n".join(
                str(x) for x in result if x is not None
            ).strip()
        return str(result or "").strip()

    def _required_marker_list(self):
        self.ensure_one()
        return [
            line.strip()
            for line in (self.required_markers or "").splitlines()
            if line.strip()
        ]

    def _missing_markers(self, findings):
        """De verplichte kopjes die in het antwoord ontbreken."""
        self.ensure_one()
        markers = self._required_marker_list()
        if not markers:
            return []
        text, _claims = claim_ledger.split(findings)
        return eval_scoring.missing_markers(text, markers)

    def _enforce_structure(self, service, call_kwargs, findings, run, bridge):
        """Eén herkansing als de verplichte opbouw ontbreekt.

        De herkansing krijgt geen tools: ze mag het antwoord alleen in de
        juiste vorm zetten, niet opnieuw werk doen. Geeft het (eventueel
        herschreven) antwoord en de kopjes die daarna nog ontbreken.
        """
        self.ensure_one()
        missing = self._missing_markers(findings)
        if (not missing or not findings or "inputs" not in call_kwargs
                or bridge.read_exhaustion()[0]):
            return findings, missing
        retry_kwargs = dict(call_kwargs)
        retry_kwargs["inputs"] = list(call_kwargs["inputs"]) + [
            {"role": "assistant", "content": findings},
            {"role": "user",
             "content": STRUCTURE_RETRY % ", ".join(missing)},
        ]
        if "tools" in retry_kwargs:
            retry_kwargs["tools"] = []
        try:
            second = self._llm_text(service.request_llm(**retry_kwargs))
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_agent_schedule: herkansing voor de opbouw van "
                "run %s mislukte", run.id,
            )
            second = ""
        if second:
            findings = second
        missing = self._missing_markers(findings)
        run.write({
            "instruction_retried": True,
            "instruction_missing": ", ".join(missing),
        })
        return findings, missing

    @staticmethod
    def _has_knowledge_sources(agent):
        return "sources_ids" in agent._fields and bool(agent.sources_ids)

    @staticmethod
    def _restricts_to_sources(agent):
        return (
            "restrict_to_sources" in agent._fields
            and bool(agent.restrict_to_sources)
        )

    def _enforce_citations(self, service, call_kwargs, findings, run,
                           bridge, agent):
        """Zonder bron geen bewering uit de kennisbronnen.

        Zocht de run kennis op en staat er geen echte bron bij, of noemt
        het antwoord een bron die niet is opgezocht, dan krijgt een
        collega die alleen uit haar bronnen mag antwoorden één
        herkansing zonder tools. Blijft het daarna mis, of noemt een
        andere collega een verzonnen bron, dan is de run ongefundeerd.
        """
        self.ensure_one()
        sources = source_citation.consulted(run_capture.peek())
        result = source_citation.check(findings, sources)
        restricted = self._restricts_to_sources(agent)
        retried = False
        if (not result["ok"] and restricted and findings
                and "inputs" in call_kwargs
                and not bridge.read_exhaustion()[0]):
            retry_kwargs = dict(call_kwargs)
            retry_kwargs["inputs"] = list(call_kwargs["inputs"]) + [
                {"role": "assistant", "content": findings},
                {"role": "user", "content": source_citation.RETRY % (
                    ", ".join(sources) or "geen"
                )},
            ]
            if "tools" in retry_kwargs:
                retry_kwargs["tools"] = []
            try:
                second = self._llm_text(service.request_llm(**retry_kwargs))
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "daadit_ai_agent_schedule: herkansing voor de "
                    "bronvermelding van run %s mislukte", run.id,
                )
                second = ""
            retried = True
            if second:
                findings = second
                result = source_citation.check(findings, sources)
        unfounded = not result["ok"] and (restricted or result["unknown"])
        if sources or result["cited"]:
            run.write({
                "citation_sources": json.dumps(sources, ensure_ascii=False),
                "citation_retried": retried,
                "citation_unfounded": unfounded,
                "citation_unknown": ", ".join(result["unknown"]),
            })
        if unfounded:
            _logger.warning(
                "daadit_ai_agent_schedule: run %s ongefundeerd — bronnen "
                "%s, genoemd %s", run.id, list(sources), result["cited"],
            )
        return findings

    def _run_context(self):
        """Extra context waarmee de agent en zijn tools deze run draaien.

        Haakje voor modules die één agent voor meer opdrachtgevers laten
        werken: zij zetten hier aan wie deze planning toebehoort, zodat
        elke tool dat uit ``env.context`` kan lezen zonder dat er iets
        op het agentrecord hoeft te veranderen.
        """
        self.ensure_one()
        return {}

    def _run_system_prompt(self, agent):
        """De systeemopdracht voor deze run; standaard die van de agent."""
        self.ensure_one()
        return agent.system_prompt or ""

    def _run_prompt(self):
        """De opdracht voor deze run, inclusief de doelketen.

        Hangt de planning aan een taak, dan krijgt de agent het "waarom"
        mee uit die taak (project, bovenliggende doelen, omschrijving,
        deadline) in plaats van erom te moeten vragen of het te verzinnen.
        """
        self.ensure_one()
        prompt = self.prompt or ""
        if not self.task_id:
            return prompt
        goal_context = self.task_id._ai_goal_context()
        if not goal_context:
            return prompt
        # self.env._() i.p.v. de kale _(): Odoo 19's kale _() leidt de taal af
        # via frame-inspectie en pikt de lokale string-variabele `goal_context`
        # op ("context" in de naam) → .get('lang') op een str → AttributeError.
        # env-gebaseerde vertaling omzeilt die inspectie volledig.
        return "%s\n\n%s\n%s\n\n%s" % (
            prompt,
            self.env._("WERKOBJECT VAN DEZE RUN — dit is het doel waar je aan "
                       "werkt. Rapporteer je resultaat op deze taak en hang wat "
                       "je maakt aan dit doel:"),
            goal_context,
            self.env._("Zet je uitkomst zo neer dat een lezer van deze taak "
                       "zonder de chat begrijpt wat er is gebeurd."),
        )

    def _post_run_status(self, run):
        """Finalise the schedule after a run finishes.

        Fase-0 circuit breaker: increment ``consecutive_failures`` on
        error, reset on success, and disable the schedule + email the
        alert users when the threshold is reached. Every failed run
        also triggers a push notification (not just circuit-break
        events) so nobody has to poll the run list.

        Test runs are exempt: they don't update last_run/last_status,
        don't count toward the circuit breaker, and don't email — a
        dry run is diagnostics, not an operational signal.
        """
        self.ensure_one()
        if run.trigger == "test":
            return
        is_error = run.state == "error"
        vals = {
            "last_status": "error" if is_error else "done",
            "last_run": run.start_date or fields.Datetime.now(),
        }
        new_failure_count = self.consecutive_failures or 0
        if is_error:
            new_failure_count += 1
            vals["consecutive_failures"] = new_failure_count
        elif self.consecutive_failures:
            vals["consecutive_failures"] = 0

        tripped = (
            is_error
            and self.failure_threshold > 0
            and new_failure_count >= self.failure_threshold
        )
        if tripped:
            vals["active"] = False

        self.sudo().write(vals)

        if is_error:
            try:
                self._notify_failure(run, tripped=tripped)
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "daadit_ai_agent_schedule: failure notification "
                    "raised — swallowing so the cron keeps going"
                )
            try:
                self._trigger_incident_analysis(run)
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "daadit_ai_agent_schedule: incident-analysis "
                    "trigger raised — swallowing so the cron keeps going"
                )

    def _trigger_incident_analysis(self, run):
        """Wake the assurance agent the moment a run fails.

        The assurance schedule is *named* "event-getriggerd" but was
        only ever a time-based cron. It last analysed an incident on
        27-07-2026 and would not have looked at the 01-08 failure until
        03-08 — nothing connected "a run failed" to the agent whose one
        job is diagnosing failed runs, so every incident sat waiting for
        a human to read the alert mail.

        We move its ``nextcall`` to now and let the ordinary cron tick
        pick it up (within a minute) rather than running it inline: a
        failing run must never block on a second LLM run, and the cron
        already serialises per schedule.

        Which schedule to wake is a System Parameter
        (``daadit_ai_agent_schedule.incident_schedule_id``) so this
        stays configuration, not a hardcoded id.
        """
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        raw = icp.get_param(
            "daadit_ai_agent_schedule.incident_schedule_id"
        ) or "0"
        try:
            target_id = int(raw)
        except (TypeError, ValueError):
            return
        if not target_id or target_id == self.id:
            # Unset, or the assurance schedule is itself the one that
            # failed. Never let it diagnose its own failure — that is a
            # loop that ends in a circuit breaker.
            return
        target = self.sudo().browse(target_id)
        if not target.exists() or not target.active:
            return
        now = fields.Datetime.now()
        if target.nextcall and target.nextcall <= now:
            # Already due: a burst of failures should wake it once, not
            # once per failing schedule.
            return
        target.write({"nextcall": now})
        _logger.info(
            "daadit_ai_agent_schedule: run %s of schedule %s failed — "
            "queued incident analysis on schedule %s",
            run.id, self.id, target.id,
        )

    def _notify_failure(self, run, tripped=False):
        """Push an email to ``alert_user_ids`` for a failed run, and a
        stronger one when the circuit breaker tripped."""
        self.ensure_one()
        users = self.sudo().alert_user_ids
        emails = sorted({u.email for u in users if u.email})
        if not emails:
            return
        base_subject = _(
            "[Circuit breaker] Schedule '%s' disabled after %d "
            "consecutive failures"
        ) % (self.name, self.failure_threshold) if tripped else (
            _("Scheduled agent run failed: %s") % self.name
        )
        error_snippet = (run.error or "")[:2000]
        preamble = (
            _(
                "Circuit breaker tripped — this schedule has been "
                "switched off. Fix the underlying cause and reactivate "
                "it to resume the cron. This is failure #%d in a row."
            ) % (run.schedule_id.consecutive_failures or 0)
            if tripped else
            _("A scheduled agent run failed. Details below.")
        )
        from markupsafe import escape as _escape
        body_html = (
            "<p>%(preamble)s</p>"
            "<ul>"
            "<li><b>Schedule:</b> %(name)s (id=%(sid)s)</li>"
            "<li><b>Agent:</b> %(agent)s</li>"
            "<li><b>Run id:</b> %(rid)s</li>"
            "<li><b>Trigger:</b> %(trigger)s</li>"
            "<li><b>Started:</b> %(started)s</li>"
            "<li><b>Duration:</b> %(dur).1fs</li>"
            "<li><b>Iterations:</b> %(iter)s</li>"
            "<li><b>Tokens (prompt / completion):</b> "
            "%(pt)s / %(ct)s</li>"
            "</ul>"
            "<p><b>Error:</b></p>"
            "<pre style=\"background:#f6f8fa;padding:8px;"
            "border-radius:4px;white-space:pre-wrap;\">%(err)s</pre>"
        ) % {
            "preamble": _escape(preamble),
            "name": _escape(self.name or ""),
            "sid": self.id,
            "agent": _escape(self.agent_id.name or ""),
            "rid": run.id,
            "trigger": _escape(run.trigger or ""),
            "started": _escape(str(run.start_date or "?")),
            "dur": run.duration or 0.0,
            "iter": run.iterations,
            "pt": run.prompt_tokens,
            "ct": run.completion_tokens,
            "err": _escape(error_snippet or "(no error text)"),
        }
        try:
            self.env["mail.mail"].sudo().create({
                "subject": base_subject,
                "body_html": body_html,
                "email_to": ", ".join(emails),
                "auto_delete": True,
            }).send()
            _logger.info(
                "daadit_ai_agent_schedule: %s failure notification "
                "sent for schedule %s to %s",
                "circuit-break" if tripped else "run",
                self.id, emails,
            )
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_agent_schedule: mail.mail send failed for "
                "schedule %s", self.id,
            )

    # ------------------------------------------------------------------
    # Cron entrypoint
    # ------------------------------------------------------------------
    @api.model
    def _sweep_stale_runs(self):
        """Mark runs stuck on 'running' as error (Fase 0-gate).

        A worker kill (deploy, limit_time_real, OOM) leaves the run
        row on 'running' forever — exactly how the 31∥32 parallel-run
        incident presented. The caller holds the global run lock, so
        nothing can actually be executing; any 'running' row older
        than the hard run budget plus grace is dead. Mark it and send
        the schedule's normal failure notification (no circuit-breaker
        increment: a killed worker is an ops signal, not an agent
        failure streak).
        """
        max_run_seconds = self._get_max_run_seconds()
        cutoff = fields.Datetime.now() - timedelta(
            seconds=max_run_seconds + 300,
        )
        stale = self.env["daadit.ai.agent.schedule.run"].sudo().search([
            ("state", "=", "running"),
            ("start_date", "<", cutoff),
        ])
        for run in stale:
            _logger.warning(
                "daadit_ai_agent_schedule: stale run %s (schedule %s, "
                "started %s) — marking as error",
                run.id, run.schedule_id.id, run.start_date,
            )
            run._mark_error(_(
                "Run afgebroken: stond na de harde tijdslimiet "
                "(%s s + 300 s marge) nog op 'running'. "
                "Waarschijnlijke oorzaak: de worker is herstart of "
                "gekilld tijdens de run."
            ) % max_run_seconds)
            try:
                run.schedule_id._notify_failure(run, tripped=False)
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "daadit_ai_agent_schedule: stale-run notification "
                    "failed for run %s — continuing", run.id,
                )
        return len(stale)

    @api.model
    def _cron_run_due_schedules(self):
        """Cron entrypoint — dispatch due schedules serially.

        Fase-0: uses a PostgreSQL session-level advisory lock so at
        most one worker executes scheduled agent runs across the whole
        database. A second worker firing the same tick logs and
        returns, satisfying "geplande runs ... maximaal één tegelijk"
        (Governance & guardrails, knowledge id 173).
        """
        cr = self.env.cr
        cr.execute(
            "SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_ID,)
        )
        if not cr.fetchone()[0]:
            _logger.info(
                "daadit_ai_agent_schedule: another worker already "
                "holds the run lock — skipping this cron tick"
            )
            return
        try:
            try:
                self._sweep_stale_runs()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "daadit_ai_agent_schedule: stale-run sweep raised "
                    "— continuing with the due schedules"
                )
            now = fields.Datetime.now()
            due = self.search([
                ("active", "=", True),
                ("nextcall", "<=", now),
            ], order="nextcall")
            if not due:
                return
            _logger.info(
                "daadit_ai_agent_schedule: cron picked up %d due "
                "schedule(s)", len(due),
            )
            for sched in due:
                try:
                    sched._claim_for_run()
                    cr.commit()
                except Exception:  # noqa: BLE001
                    cr.rollback()
                    _logger.exception(
                        "daadit_ai_agent_schedule: failed to claim "
                        "schedule %s — skipping this tick", sched.id,
                    )
                    continue
                try:
                    sched._execute(trigger="cron")
                    cr.commit()
                except Exception:  # noqa: BLE001
                    # _execute is designed not to raise; this is the
                    # last safety net so one bad schedule never
                    # aborts the batch.
                    cr.rollback()
                    _logger.exception(
                        "daadit_ai_agent_schedule: unhandled error "
                        "running schedule %s", sched.id,
                    )
        finally:
            try:
                cr.execute(
                    "SELECT pg_advisory_unlock(%s)",
                    (_ADVISORY_LOCK_ID,),
                )
                cr.fetchone()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "daadit_ai_agent_schedule: failed to release "
                    "run lock — cursor close should still free it"
                )

    # ------------------------------------------------------------------
    # UI actions
    # ------------------------------------------------------------------
    def action_run_now(self):
        """Run the schedule immediately (does not change ``nextcall``)."""
        self.ensure_one()
        if not self.active:
            raise UserError(_(
                "This schedule is archived. Reactivate it before running."
            ))
        run = self._execute(trigger="manual")
        return {
            "type": "ir.actions.act_window",
            "res_model": "daadit.ai.agent.schedule.run",
            "res_id": run.id,
            "view_mode": "form",
            "target": "current",
            "name": _("Schedule Run"),
        }

    def action_test_run(self):
        """Run the agent once in dry mode: read/search tools execute,
        write tools are intercepted and simulated. The run log shows
        the findings and every action the agent *would* have taken —
        nothing is changed in the system. Does not touch ``nextcall``,
        the circuit breaker, or last-run bookkeeping."""
        self.ensure_one()
        if not self.active:
            raise UserError(_(
                "This schedule is archived. Reactivate it before "
                "running a test."
            ))
        run = self._execute(trigger="test")
        return {
            "type": "ir.actions.act_window",
            "res_model": "daadit.ai.agent.schedule.run",
            "res_id": run.id,
            "view_mode": "form",
            "target": "current",
            "name": _("Test Run"),
        }

    def action_view_runs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Runs — %s") % self.name,
            "res_model": "daadit.ai.agent.schedule.run",
            "view_mode": "list,form",
            "domain": [("schedule_id", "=", self.id)],
            "context": {"default_schedule_id": self.id},
        }


class AiAgentScheduleRun(models.Model):
    _name = "daadit.ai.agent.schedule.run"
    # v19.0.2.5.0: chatter + activities on the run itself, so the
    # watchdog (and humans) can leave alerts and follow-ups exactly
    # where the evidence lives instead of on a detached record.
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "AI Agent Schedule Run"
    _order = "start_date desc, id desc"
    _rec_name = "display_name"

    display_name = fields.Char(compute="_compute_display_name")
    schedule_id = fields.Many2one(
        "daadit.ai.agent.schedule", string="Schedule",
        ondelete="cascade", index=True,
        help="Leeg bij een chatbeurt: die hoort bij geen planning.",
    )
    # Was a stored related on schedule_id. Made a field of its own so a
    # chat turn — which has no schedule — can still record who answered,
    # and so an old run keeps naming the agent that actually ran it
    # rather than following a later edit of the schedule.
    agent_id = fields.Many2one(
        "ai.agent", string="AI Agent", store=True, index=True,
        compute="_compute_agent_id", readonly=False, precompute=True,
    )
    trigger = fields.Selection(
        [("cron", "Scheduled"),
         ("manual", "Manual"),
         ("test", "Test run"),
         ("chat", "Chat")],
        string="Trigger", default="cron", readonly=True,
        help="'Test run' = dry mode: read tools executed, write tools "
             "simulated — nothing was changed in the system. "
             "'Chat' = a conversation turn, captured so a spoken claim "
             "can be checked against what the agent actually did.",
    )

    @api.depends("schedule_id")
    def _compute_agent_id(self):
        for rec in self:
            if rec.schedule_id:
                rec.agent_id = rec.schedule_id.agent_id
            elif not rec.agent_id:
                rec.agent_id = False
    user_id = fields.Many2one(
        "res.users", string="Ran as", readonly=True,
    )
    turn_uuid = fields.Char(
        string="Chatbeurt-id", readonly=True, index=True, copy=False,
        help="Het id van de live chatbeurt waar deze run bij hoort. "
             "De denkstappen die de gebruiker tijdens het antwoord zag "
             "zijn vluchtig (bus); dit id koppelt ze aan deze "
             "vastgelegde run zodat ze achteraf terugleesbaar zijn — uit "
             "dezelfde bron als de audit. Leeg voor geplande runs.",
    )
    company_id = fields.Many2one(
        "res.company", string="Company", readonly=True,
    )
    task_id = fields.Many2one(
        "project.task", string="Werkobject", readonly=True,
        ondelete="set null", index=True,
    )

    state = fields.Selection(
        [("running", "Running"),
         ("done", "Success"),
         ("error", "Error"),
         # Geen fout: de agent mocht niet draaien. Als dit als 'error'
         # zou tellen, tikt de circuit breaker een gezonde planning uit
         # omdat de klant zijn bundel op heeft.
         ("budget", "Budget bereikt"),
         # Afgerond, maar een beweerde handeling heeft geen geslaagde
         # tool-actie achter zich. Het verslag gaat niet naar de klant.
         ("disputed", "Betwist")],
        string="Status", default="running", required=True, readonly=True,
        index=True,
    )
    rolled_back = fields.Boolean(
        string="Teruggedraaid", readonly=True, copy=False,
        help="De run liep vast; wat hij in deze database had gewijzigd "
             "is teruggedraaid.",
    )
    undo_plan = fields.Text(
        string="Herstelvoorstel", readonly=True, copy=False,
        help="Wat er buiten deze database al was gedaan voordat de run "
             "vastliep, en hoe dat terug kan. Wordt nooit automatisch "
             "uitgevoerd.",
    )
    start_date = fields.Datetime(string="Started", readonly=True)
    end_date = fields.Datetime(string="Finished", readonly=True)
    duration = fields.Float(
        string="Duration (s)", compute="_compute_duration", store=True,
    )
    # Uitzendkracht.ai verkoopeenheid (CFO fase 8 / taak 1061): elk
    # begonnen kwartier van runlooptijd telt als billable. Geen factuur
    # hier — alleen de meetbasis waarop portal/SO later leunt.
    billable_quarters = fields.Integer(
        string="Billable quarters",
        compute="_compute_billable_time", store=True,
        help="Aantal begonnen kwartieren runlooptijd (ceil(duration/900)). "
             "0 als de run geen eindtijd heeft.",
    )
    billable_hours = fields.Float(
        string="Billable hours",
        compute="_compute_billable_time", store=True, digits=(16, 2),
        help="billable_quarters × 0,25 uur — uitzendkracht.ai-eenheid.",
    )

    findings = fields.Text(
        string="Findings (raw)", readonly=True,
        help="The agent's written answer for this run (raw markdown).",
    )
    findings_html = fields.Html(
        string="Findings", readonly=True, sanitize=True,
        compute="_compute_findings_html", store=True,
        help="Findings rendered as HTML for nicer display in Odoo.",
    )
    error = fields.Text(string="Error", readonly=True)

    denkstappen_html = fields.Html(
        string="Denkstappen (terugleesbaar)", readonly=True, sanitize=True,
        compute="_compute_denkstappen_html",
        help="De denkstappen die de gebruiker tijdens dit antwoord live "
             "zag, opnieuw opgebouwd uit de vastgelegde tool-aanroepen. "
             "Dezelfde bron als de audit, dus wat je hier terugleest kan "
             "niet afwijken van wat er echt gebeurde.",
    )

    @api.depends("action_ids", "action_ids.tool_name",
                 "action_ids.arguments", "action_ids.sequence")
    def _compute_denkstappen_html(self):
        for rec in self:
            steps = rec._replay_steps()
            if not steps:
                rec.denkstappen_html = False
                continue
            rows = []
            for step in steps:
                indent = "· " if step.get("kind") == "route" else ""
                rows.append(
                    "<li>%s%s</li>" % (indent, _escape(step["text"]))
                )
            rec.denkstappen_html = "<ol class='mb-0'>%s</ol>" % "".join(rows)

    model = fields.Char(string="LLM Model", readonly=True)
    provider = fields.Char(
        string="Provider", readonly=True,
        help="De provider die deze run bediende (mistral, claude, "
             "loes, …), zoals ai.agent._get_provider hem koos.",
    )
    fallback_provider = fields.Char(string="Uitwijkprovider", readonly=True)
    fallback_model = fields.Char(string="Uitwijkmodel", readonly=True)
    fallback_reason = fields.Char(
        string="Reden uitwijken", readonly=True,
        help="Gevuld als een beurt van deze run door een andere "
             "provider werd beantwoord omdat de eigen provider "
             "onbereikbaar was.",
    )
    iterations = fields.Integer(
        string="Iterations", readonly=True,
        help="Mistral round-trips (>1 means the agent used tools).",
    )
    prompt_tokens = fields.Integer(string="Prompt tokens", readonly=True)
    completion_tokens = fields.Integer(
        string="Completion tokens", readonly=True,
    )
    total_tokens = fields.Integer(
        string="Total tokens", compute="_compute_total_tokens", store=True,
    )
    usage_model = fields.Char(
        string="Usage model", readonly=True,
        help="Technical name of the provider usage model these token "
             "counts came from (e.g. daadit_ai_mistral.usage). Empty "
             "for providers without usage tracking.",
    )
    usage_row_id = fields.Integer(
        string="Usage row id", readonly=True,
        help="Database id of the provider usage row for this run, in "
             "the model named by 'usage_model'.",
    )
    estimated_cost_usd = fields.Float(
        string="Est. cost (USD)", readonly=True,
        compute="_compute_estimated_cost_usd",
    )

    @api.depends("usage_model", "usage_row_id")
    def _compute_estimated_cost_usd(self):
        for run in self:
            row = run._usage_row()
            run.estimated_cost_usd = (
                row.estimated_cost_usd if row else 0.0
            ) or 0.0

    def _usage_row(self):
        """De verbruiksregel van deze run, bij welke provider ook."""
        self.ensure_one()
        if not self.usage_model or self.usage_model not in self.env \
                or not self.usage_row_id:
            return None
        row = self.env[self.usage_model].sudo().browse(
            self.usage_row_id
        ).exists()
        if not row or "estimated_cost_usd" not in row._fields:
            return None
        return row
    estimated_cost_eur = fields.Float(
        string="Geschatte kosten (EUR)",
        compute="_compute_estimated_cost_eur",
        help="Omgerekend uit de dollarprijs van de provider tegen de koers "
             "in systeemparameter daadit_ai_mistral.usd_eur_rate.",
    )

    @api.depends("estimated_cost_usd")
    def _compute_estimated_cost_eur(self):
        """DAADit invoices in euros; the providers bill in dollars.

        The USD amount stays the stored truth — it is what the invoice
        will say. This is presentation only.

        Deliberately not res.currency._convert: the USD rate in this
        database is 1.00 because no rate source was ever configured, so
        that route would quietly price a dollar at a euro. An explicit
        parameter is one place to maintain and one number to check.
        """
        icp = self.env["ir.config_parameter"].sudo()
        try:
            rate = float(icp.get_param("daadit_ai_mistral.usd_eur_rate") or 0)
        except (TypeError, ValueError):
            rate = 0.0
        if rate <= 0:
            rate = 0.92
        for run in self:
            run.estimated_cost_eur = (run.estimated_cost_usd or 0.0) * rate

    action_ids = fields.One2many(
        "daadit.ai.agent.schedule.run.action", "run_id",
        string="Actions",
    )
    delegation_ids = fields.One2many(
        "daadit.ai.agent.schedule.run.delegation", "run_id",
        string="Delegaties",
    )
    delegation_count = fields.Integer(
        string="# Delegaties", compute="_compute_delegation_cost",
    )
    delegation_cost_usd = fields.Float(
        string="Kosten delegaties (USD)", compute="_compute_delegation_cost",
        help="Wat de collega's kostten aan wie deze run een vraag "
             "doorgaf, op elk niveau.",
    )
    total_cost_usd = fields.Float(
        string="Kosten incl. delegaties (USD)",
        compute="_compute_delegation_cost",
    )

    @api.depends("delegation_ids.estimated_cost_usd", "estimated_cost_usd")
    def _compute_delegation_cost(self):
        for run in self:
            cost = sum(run.delegation_ids.mapped("estimated_cost_usd"))
            run.delegation_count = len(run.delegation_ids)
            run.delegation_cost_usd = cost
            run.total_cost_usd = (run.estimated_cost_usd or 0.0) + cost
    action_count = fields.Integer(
        string="# Actions", compute="_compute_action_count",
    )
    write_action_count = fields.Integer(
        string="# Write actions", compute="_compute_action_count",
        help="Tool calls that mutated database state — read/get/search "
             "and menu-navigation calls are not counted.",
    )
    lost_write_count = fields.Integer(
        string="# Lost writes",
        compute="_compute_action_count",
        help="Schrijfpogingen die niet zijn vastgelegd "
             "(write_attempt_count − write_action_count). Taak 802: "
             "zichtbaar ook als er wél iets slaagde.",
    )

    # ------------------------------------------------------------------
    @api.depends("schedule_id.name", "start_date", "state")
    def _compute_display_name(self):
        for rec in self:
            label = rec.schedule_id.name or _("Run")
            rec.display_name = "%s — %s [%s]" % (
                label, rec.start_date or "?", rec.state,
            )

    @api.depends("start_date", "end_date")
    def _compute_duration(self):
        for rec in self:
            if rec.start_date and rec.end_date:
                rec.duration = (
                    rec.end_date - rec.start_date
                ).total_seconds()
            else:
                rec.duration = 0.0

    @api.depends("start_date", "end_date", "duration")
    def _compute_billable_time(self):
        # Elk begonnen kwartier telt; een run zonder eindtijd nog niet.
        for rec in self:
            seconds = rec.duration if rec.end_date else 0.0
            quarters = int(-(-seconds // 900)) if seconds > 0 else 0
            rec.billable_quarters = quarters
            rec.billable_hours = quarters * 0.25

    @api.depends("prompt_tokens", "completion_tokens")
    def _compute_total_tokens(self):
        for rec in self:
            rec.total_tokens = (
                (rec.prompt_tokens or 0) + (rec.completion_tokens or 0)
            )

    write_attempt_count = fields.Integer(
        string="# Write attempts",
        compute="_compute_action_count",
        help="Schrijfpogingen, inclusief de geweigerde.",
    )

    @api.depends("action_ids", "action_ids.is_write",
                 "action_ids.is_effective_write")
    def _compute_action_count(self):
        for rec in self:
            rec.action_count = len(rec.action_ids)
            # write_action_count telt voortaan alleen wat er echt is
            # gewijzigd; pogingen staan apart. Voorheen telde een
            # geweigerde schrijfpoging mee, waardoor claims_unverified
            # groen licht gaf aan een run die niets deed.
            rec.write_attempt_count = len(
                rec.action_ids.filtered("is_write")
            )
            rec.write_action_count = len(
                rec.action_ids.filtered("is_effective_write")
            )
            rec.lost_write_count = max(
                0, rec.write_attempt_count - rec.write_action_count,
            )

    def action_view_all_actions(self):
        """Open the full action list (read + write) for this run."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("All tool calls — %s") % self.display_name,
            "res_model": "daadit.ai.agent.schedule.run.action",
            "view_mode": "list,form",
            "domain": [("run_id", "=", self.id)],
            "context": {"default_run_id": self.id},
        }

    @api.depends("findings")
    def _compute_findings_html(self):
        """Render the agent's markdown answer as HTML.

        The agent typically returns markdown (``**bold**``, bullet lists,
        emoji, fenced code blocks). Storing both raw text and a rendered
        HTML view lets the form show a readable answer while keeping the
        raw text available for export / copy.

        Uses ``markdown2`` (already a dependency of stock Odoo's ``ai``
        module). Falls back to a ``<pre>`` block if the library is not
        importable so the field is never empty when findings exist.
        """
        try:
            from markdown2 import markdown as _md
        except ImportError:
            _md = None
        for rec in self:
            text = rec.findings or ""
            if not text:
                rec.findings_html = False
                continue
            if _md is None:
                rec.findings_html = "<pre>%s</pre>" % _escape(text)
                continue
            try:
                rec.findings_html = _md(
                    text,
                    extras=[
                        "tables",
                        "fenced-code-blocks",
                        "code-friendly",
                        "break-on-newline",
                    ],
                )
            except Exception:  # noqa: BLE001
                rec.findings_html = "<pre>%s</pre>" % _escape(text)

    def _task_report_html(self):
        """Het resultaat van deze run, voor de chatter van het werkobject.

        Kort: wie deed het en hoe liep het af, de eerste zinnen van de
        agent als platte tekst, en de feitenregel van het systeem. Het
        hele verslag met de gewijzigde records staat achter de link.
        """
        self.ensure_one()
        state_label = dict(self._fields["state"].selection).get(
            self.state, self.state,
        )
        head = _(
            "%(agent)s — %(state)s",
            agent=self.agent_id.display_name or "?", state=state_label,
        )
        story, facts = self._task_report_parts()
        html = Markup(
            '<p><b>%s</b> · <a href="/odoo/daadit.ai.agent.schedule.run/%s">'
            "%s</a></p>"
        ) % (head, self.id, _("volledig verslag"))
        if story:
            html += Markup("<p>%s</p>") % story
        if facts:
            html += Markup("<p><i>%s</i></p>") % facts
        return html

    # Een chatterbericht is een notitie, geen rapport. Meer dan dit leest
    # niemand in een taak; het hele verslag staat een klik verder.
    _TASK_REPORT_CHARS = 500

    def _task_report_parts(self):
        """De tekst van de agent in het kort, en de feitenregel apart.

        ``findings`` is markdown met daarachter onze eigen voetregel en de
        lijst gewijzigde records. Voor de chatter blijft alleen platte
        tekst over: kopjes, sterretjes, streepjes en code-tekens gaan
        eruit, de eerste zinnen blijven staan tot de grens.
        """
        text = (self.findings or "").strip()
        if not text:
            return (self.error or _("(geen tekst)")).strip(), ""
        facts = ""
        idx = text.find(self._FOOTER_MARK)
        if idx >= 0:
            tail = text[idx:]
            facts = tail.split("\n", 1)[0].strip().strip("_").strip()
            text = text[:idx].rstrip().rstrip("-_ \n")
        story = self._markdown_to_plain(text)
        if len(story) > self._TASK_REPORT_CHARS:
            cut = story[: self._TASK_REPORT_CHARS]
            stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
            story = (cut[: stop + 1] if stop > 200 else cut.rstrip()) + " …"
        return story, facts

    @staticmethod
    def _markdown_to_plain(text):
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"^\s{0,3}(#{1,6}\s*|[-*+]\s+|\d+\.\s+|>\s?)", "",
                      text, flags=re.M)
        text = re.sub(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", "", text, flags=re.M)
        text = re.sub(r"[*_`~]+", "", text)
        text = re.sub(r"\s*\n\s*", " ", text)
        return re.sub(r"\s{2,}", " ", text).strip()

    # ------------------------------------------------------------------
    def _mark_error(self, message):
        self.ensure_one()
        self.write({
            "state": "error",
            "end_date": fields.Datetime.now(),
            "error": (message or "")[:60_000],
        })

    # Woorden waarmee een agent beweert iets te hebben gedaan. Niet
    # bedoeld om taal te begrijpen — alleen om de combinatie "ik heb X
    # gedaan" + "nul schrijfacties" te kunnen zien, want dat is precies
    # de bewering die niet waar kan zijn.
    _CLAIM_WORDS = (
        "aangemaakt", "toegewezen", "ingepland", "klaargezet",
        "doorgevoerd", "bijgewerkt", "verstuurd", "gepubliceerd",
        "bevestigde wijziging", "heb ik gewijzigd", "is gewijzigd",
        "klaargemaakt", "concept voorbereid", "concepten voorbereid",
        "conceptfactuur opgesteld", "conceptfacturen opgesteld",
        "heb ik vastgelegd",
    )
    # Beweringen dat iets echt naar buiten is gezet. Een aparte lijst,
    # want zo'n bewering vraagt méér dan een willekeurige schrijfactie:
    # ze blijft alleen staan als er ook een echte publicatie tegenover
    # staat (zie ``_compute_claims_unverified``). "gepubliceerd" staat
    # ook in ``_CLAIM_WORDS`` — de frasen hier maken "staat live" en
    # "online gezet" óók tot een (publicatie)bewering.
    _PUBLICATION_CLAIM_WORDS = (
        "gepubliceerd", "live gezet", "staat live", "online gezet",
        "live gestaan", "gepubliceerd op",
    )
    # Alles vanaf deze zin is door ons geschreven, niet door het model.
    # Zonder deze grens zou de voetregel zichzelf als bewering lezen.
    _FOOTER_MARK = "Feitelijk vastgelegd door het systeem"

    claims_unverified = fields.Boolean(
        string="Bewering niet gedekt",
        compute="_compute_claims_unverified", store=True,
        help="De tekst beweert werk te hebben verricht dat niet door een "
             "vastgelegd effect wordt gedekt: geen enkele geslaagde "
             "schrijfactie, of minder effecten dan beweerd.",
    )

    # Woorden die een bewering ontkennen of op nul zetten. "0 activiteiten
    # aangemaakt" is een correcte melding, geen claim — zonder deze lijst
    # vlagde de eerste versie precies de agents die eerlijk rapporteerden.
    _NEGATIONS = ("geen", "niet", "nul", "0", "niets", "zonder")

    # Een voornemen is geen bewering. Zonder deze lijst leest "de post
    # wordt morgen gepubliceerd" als een uitgevoerde publicatie, en dat
    # is precies het soort valse beschuldiging dat deze controle
    # onbruikbaar maakt.
    _FUTURE_MARKERS = (
        "wordt ", "worden ", "zal ", "zullen ", "moet ", "moeten ",
        "voorstel", "advies", "nog te ", "gaan we ", "kan worden",
    )

    # Aantallen in woorden. Alleen tot twaalf: daarboven schrijft een
    # rapport het als cijfer, en elk extra woord vergroot alleen de kans
    # op een verkeerd gelezen aantal.
    _NUMBER_WORDS = {
        "een": 1, "één": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5,
        "zes": 6, "zeven": 7, "acht": 8, "negen": 9, "tien": 10,
        "elf": 11, "twaalf": 12,
    }
    # Woorden waarna een getal een verwijzing is en geen aantal: "taken
    # 5, 6 en 7 toegewezen" claimt niet vijf taken.
    _REFERENCE_WORDS = (
        "taak", "taken", "record", "records", "id", "nr", "nummer",
        "artikel", "ticket", "lead", "post", "regel", "versie", "run",
    )

    claim_count = fields.Integer(
        string="# Beweringen",
        compute="_compute_claims_unverified", store=True,
        help="Aantal beweringen over uitgevoerd werk dat de "
             "verificatiestap in de rapporttekst heeft gevonden.",
    )
    claim_covered_count = fields.Integer(
        string="# Beweringen gedekt",
        compute="_compute_claims_unverified", store=True,
        help="Beweringen waarvoor een vastgelegd effect bestaat "
             "(een geslaagde schrijfactie met model en record).",
    )
    claim_verification = fields.Text(
        string="Verificatie van beweringen",
        compute="_compute_claims_unverified", store=True,
        help="Door code geschreven regel: hoeveel beweringen gevonden, "
             "hoeveel gedekt door een vastgelegd effect, en welke niet.",
    )
    publication_unverified = fields.Boolean(
        string="Publicatie niet bewezen",
        compute="_compute_claims_unverified", store=True,
        help="De tekst beweert dat iets is gepubliceerd, maar geen "
             "enkele schrijfactie zette echt live (een concept of een "
             "geplande post is geen publicatie).",
    )

    claims_structured = fields.Boolean(
        string="Verantwoording aangeleverd", readonly=True,
        help="De agent leverde een beweringenlijst (```claims) met per "
             "handeling de tool-actie die haar bewijst.",
    )
    claims_ledger = fields.Text(
        string="Verantwoording (JSON)", readonly=True,
        help="Elke bewering met soort, verwijzing en de uitkomst van de "
             "toets, zoals het systeem hem heeft vastgesteld.",
    )
    claims_disputed_count = fields.Integer(
        string="# Handelingen zonder bewijs", readonly=True,
    )
    instruction_retried = fields.Boolean(
        string="Herkansing voor de opbouw", readonly=True,
    )
    instruction_missing = fields.Char(
        string="Ontbrekende opbouw", readonly=True,
        help="De verplichte kopjes die na de herkansing nog ontbraken.",
    )
    citation_sources = fields.Text(
        string="Geraadpleegde bronnen (JSON)", readonly=True,
        help="De kennisbronnen die deze run opzocht, met hun link.",
    )
    citation_retried = fields.Boolean(
        string="Herkansing voor de bronvermelding", readonly=True,
    )
    citation_unfounded = fields.Boolean(
        string="Ongefundeerd", readonly=True, index=True,
        help="Het antwoord steunt op kennis zonder echte bron, of noemt "
             "een bron die in deze run niet is opgezocht.",
    )
    citation_unknown = fields.Char(
        string="Genoemde bronnen zonder opzoeking", readonly=True,
    )
    claims_ledger_text = fields.Text(
        string="Toets van de verantwoording",
        compute="_compute_claims_ledger_text",
    )

    @api.depends("claims_ledger")
    def _compute_claims_ledger_text(self):
        for rec in self:
            try:
                rows = json.loads(rec.claims_ledger or "[]")
            except ValueError:
                rows = []
            rec.claims_ledger_text = "\n".join(
                "%s %s — %s%s" % (
                    "✓" if row.get("ok") else "✗",
                    row.get("kind") or "",
                    row.get("text") or "",
                    " (%s)" % row["reason"] if row.get("reason") else "",
                )
                for row in rows if isinstance(row, dict)
            )

    def _claim_ledger_actions(self):
        self.ensure_one()
        return [{
            "id": a.id,
            "sequence": a.sequence,
            "effective": bool(a.is_effective_write),
            "error": bool(a.is_error),
            "model": a.target_model or "",
            "ref": a.target_ref or 0,
        } for a in self.action_ids]

    def _apply_claim_ledger(self):
        """Toets de beweringenlijst van de agent en haal hem uit de tekst.

        Zonder lijst blijft alles zoals het was: dan beslist de
        woordcontrole (``claims_unverified``). Met lijst telt elke
        handeling zonder geslaagde, echte schrijfactie in deze run; één
        daarvan maakt een afgeronde run ``disputed``. In een testrun is
        niets geschreven, dus daar bewijst geen enkele actie iets.
        """
        for run in self:
            text, claims = claim_ledger.split(run.findings)
            if claims is None:
                continue
            rows = claim_ledger.verify(
                claims, run._claim_ledger_actions(),
                dry_run=run.trigger == "test",
            )
            bad = [r for r in rows if not r["ok"]]
            vals = {
                "findings": text,
                "claims_structured": True,
                "claims_ledger": json.dumps(rows, ensure_ascii=False),
                "claims_disputed_count": len(bad),
            }
            if bad and run.state == "done":
                vals["state"] = "disputed"
            run.write(vals)
            if bad:
                _logger.warning(
                    "daadit_ai_agent_schedule: run %s betwist — %d "
                    "handeling(en) zonder bewijs", run.id, len(bad),
                )

    def _extract_claims(self):
        """De beweringen over uitgevoerd werk in de rapporttekst.

        Geen taalbegrip, en dat is opzet: dit zoekt per regel een
        werkwoord dat een doorgevoerde wijziging aanduidt, en het aantal
        dat daar direct bij hoort. Alles wat twijfelachtig is valt af —
        een gemiste bewering is goedkoper dan een valse beschuldiging.

        Levert per bewering een dict met ``line`` (de regel, afgekapt),
        ``word`` (het aangetroffen werkwoord) en ``amount`` (het
        beweerde aantal; 1 als er geen aantal staat).
        """
        self.ensure_one()
        text = self.findings or ""
        cut = text.find(self._FOOTER_MARK)
        if cut != -1:
            # Alles vanaf de feitenregel is door ons geschreven; dat als
            # bewering lezen zou de controle zichzelf laten vlaggen.
            text = text[:cut]
        claims = []
        for raw in text.splitlines():
            line = raw.strip()
            low = line.lower()
            # Tabelregels zijn data, geen bewering. Een kolomkop
            # "Toegewezen aan" vlagde anders elk rapport met een nette
            # tabel erin — de tweede valse melding op rij.
            if not low or low.startswith("|"):
                continue
            hits = [w for w in self._CLAIM_WORDS if w in low]
            pub_hits = [w for w in self._PUBLICATION_CLAIM_WORDS if w in low]
            if not hits and not pub_hits:
                continue
            if any(n in low for n in self._NEGATIONS):
                continue
            if any(m in low for m in self._FUTURE_MARKERS):
                continue
            word = min(hits + pub_hits, key=low.find)
            claims.append({
                "line": line[:120],
                "word": word,
                "amount": self._claimed_amount(low[:low.find(word)]),
                "publication": bool(pub_hits),
            })
        return claims

    @classmethod
    def _claimed_amount(cls, fragment):
        """Het beweerde aantal in het stuk regel vóór het werkwoord.

        Alleen een getal dat direct voor een woord staat en niet achter
        een verwijzing ("taken 5, 6 en 7") telt mee. Staat er geen
        bruikbaar aantal, dan is de bewering één wijziging waard — nooit
        meer, want een te hoog gelezen aantal is een valse aanklacht.
        """
        pattern = r"(?<![\w#])(\d{1,3}|%s)\s+([a-z]{3,})" % "|".join(
            cls._NUMBER_WORDS
        )
        for match in re.finditer(pattern, fragment):
            before = fragment[:match.start()].rstrip(" ,:;(-").split()
            if before and before[-1].strip(".,:;") in cls._REFERENCE_WORDS:
                continue
            token = match.group(1)
            amount = cls._NUMBER_WORDS.get(token)
            if amount is None:
                try:
                    amount = int(token)
                except ValueError:
                    continue
            if amount >= 1:
                return amount
        return 1

    @api.depends("findings", "write_action_count", "action_ids",
                 "action_ids.is_effective_write",
                 "action_ids.change_kind", "action_ids.is_publication",
                 "action_ids.target_model", "action_ids.target_ref")
    def _compute_claims_unverified(self):
        """Zet de bewering naast het effect en leg het verschil vast.

        De feitenregel onder elk rapport noemt de aantallen al, maar
        niemand vergeleek ze met de tekst. Pim meldde drie "door de tool
        bevestigde" toewijzingen in een run met nul schrijfacties, Lux
        acht concepten bij twee. Dit doet die vergelijking machinaal:
        het aantal beweerde wijzigingen tegen het aantal vastgelegde
        effecten, per run, zonder tweede model.

        Het beweerde aantal is het hóógste aantal uit één regel, niet de
        som over alle regels: een rapport dat hetzelfde werk in een kop
        en in een opsomming noemt, beweert het niet twee keer.

        Een PUBLICATIEbewering telt apart. Zij blijft alleen staan als er
        een effect tegenover staat dat ook echt live zette; een concept
        klaarzetten of een post inplannen dekt haar niet. Nova meldde
        blog.post 83 als "gepubliceerd" in een run met een concept-effect
        maar zonder publicatie — het aantal klopte, de daad niet. Daarom
        vlaggen we een publicatiebewering zonder publicatie-effect ook
        als de aantallen sluiten.
        """
        for rec in self:
            claims = rec._extract_claims()
            changes = rec._verified_changes()
            effects = rec.write_action_count or 0
            claimed = max([c["amount"] for c in claims] or [0])
            pub_claims = [c for c in claims if c.get("publication")]
            has_publication = bool(
                rec.action_ids.filtered("is_publication")
            )
            # Een publicatiebewering zonder een effect dat live zette is
            # nooit gedekt, ongeacht andere schrijfacties.
            pub_unverified = bool(pub_claims) and not has_publication
            if not claims:
                covered = 0
            elif claimed <= effects:
                covered = len(claims)
            else:
                # Conservatief tellen: elk vastgelegd effect dekt een
                # bewering, de rest blijft ongedekt.
                covered = min(len(claims), effects)
            if pub_unverified:
                # De ongedekte publicatiebeweringen tellen niet als
                # gedekt, ook niet als de aantallen toevallig sluiten.
                covered = min(covered, len(claims) - len(pub_claims))
            rec.claim_count = len(claims)
            rec.claim_covered_count = max(covered, 0)
            rec.publication_unverified = pub_unverified
            rec.claims_unverified = (
                (bool(claims) and claimed > effects) or pub_unverified
            )
            rec.claim_verification = rec._verification_line(
                claims, changes, claimed, effects, pub_unverified,
            )

    def _verification_line(self, claims, changes, claimed, effects,
                           pub_unverified=False):
        """De verificatieregel: door code geschreven, niet door het model."""
        self.ensure_one()
        if not claims:
            return _(
                "Verificatie: geen beweringen over uitgevoerd werk in de "
                "tekst gevonden; %s vastgelegde schrijfactie(s)."
            ) % effects
        parts = [_(
            "Verificatie: %(claims)s bewering(en) gevonden, "
            "%(effects)s vastgelegde schrijfactie(s), hoogste beweerde "
            "aantal %(claimed)s.",
            claims=len(claims), effects=effects, claimed=claimed,
        )]
        if claimed > effects:
            parts.append(_(
                "Niet gedekt (%(short)s wijziging(en) te weinig bewijs): "
                "%(line)s",
                short=claimed - effects,
                line=" / ".join(c["line"] for c in claims[:3]),
            ))
        elif pub_unverified:
            pub_lines = " / ".join(
                c["line"] for c in claims if c.get("publication")
            )
            parts.append(_(
                "Publicatie beweerd zonder een effect dat live zette "
                "(een concept of geplande post is geen publicatie): "
                "%(line)s",
                line=pub_lines[:400],
            ))
        else:
            parts.append(_("Elke bewering wordt door een effect gedekt."))
        if changes:
            proof = ", ".join(
                "%s#%s" % (c["model"] or c["tool"], c["id"] or "?")
                for c in changes[:5]
            )
            parts.append(_("Bewijs: %s.") % proof)
        else:
            parts.append(_("Er is geen enkel effect vastgelegd."))
        return " ".join(parts)[:8000]

    error_action_count = fields.Integer(
        string="# Mislukte tool-acties",
        compute="_compute_error_action_count", store=True,
        help="Tool-aanroepen die met een fout terugkwamen — ook in een "
             "run die als geslaagd is afgesloten.",
    )

    @api.depends("action_ids", "action_ids.is_error")
    def _compute_error_action_count(self):
        for rec in self:
            rec.error_action_count = len(
                rec.action_ids.filtered("is_error")
            )

    needs_attention = fields.Boolean(
        string="Vraagt aandacht",
        compute="_compute_needs_attention", store=True, index=True,
        help="Er is iets misgegaan in deze run, ook als hij als "
             "geslaagd is afgesloten.",
    )
    attention_reason = fields.Char(
        string="Waarom aandacht",
        compute="_compute_needs_attention", store=True,
    )

    # Een enkele mislukte tool-aanroep is normaal: een agent tast af,
    # krijgt een weigering en corrigeert. Vanaf drie is het geen
    # aftasten meer maar een patroon — dat is de grens waarop de
    # restlijst mag piepen. Instelbaar, want hij is gekozen op één week
    # logboek.
    _ATTENTION_ERROR_ICP = "daadit_ai_agent_schedule.attention_error_floor"
    _ATTENTION_ERROR_DEFAULT = 3

    @api.depends("state", "claims_unverified", "publication_unverified",
                 "claim_count", "error_action_count",
                 "claims_disputed_count",
                 "write_attempt_count", "write_action_count",
                 "action_ids", "action_ids.is_write",
                 "action_ids.is_effective_write", "action_ids.result")
    def _compute_needs_attention(self):
        """Waarom dit veld bestaat, en niet alleen ``state``.

        De dagelijkse restlijst zocht op ``state = error`` en kreeg op
        3-8 terecht een lege lijst terug: nul gefaalde runs. In diezelfde
        24 uur, allemaal in runs met ``state = done``, deed Mark 11
        schrijfpogingen met 1 geslaagde schrijfactie, had Eva 12 mislukte
        tool-acties, rapporteerde Nova een gepubliceerde blogpost bij nul
        schrijfacties, en stond Hilda op ``claims_unverified``. Vier
        agents leverden gedeeltelijk of verzonnen werk en de controle
        meldde nul problemen — een watchdog die betrouwbaar rust
        uitstraalt is de gevaarlijkste variant.

        Taak 802: de vlag ging alleen af bij *nul* vastgelegde writes.
        Zodra één poging slaagde (run 609: 8 pogingen, 2 vastgelegd)
        verdwenen de verloren schrijfacties uit ``needs_attention`` en
        dus uit de restlijst. Verloren = attempts − effects, ook als er
        wél iets door kwam.
        """
        try:
            floor = int(self.env["ir.config_parameter"].sudo().get_param(
                self._ATTENTION_ERROR_ICP, self._ATTENTION_ERROR_DEFAULT,
            ))
        except (TypeError, ValueError):
            floor = self._ATTENTION_ERROR_DEFAULT
        for rec in self:
            reasons = []
            if rec.state == "error":
                reasons.append(_("run afgebroken met een fout"))
            if rec.publication_unverified:
                reasons.append(_(
                    "rapport claimt een publicatie zonder een effect dat "
                    "live zette"
                ))
            if rec.claims_unverified and not rec.publication_unverified:
                if rec.write_action_count:
                    reasons.append(_(
                        "rapport claimt meer werk dan vastgelegd "
                        "(%(claims)s bewering(en), %(effects)s effect(en))",
                        claims=rec.claim_count,
                        effects=rec.write_action_count,
                    ))
                else:
                    reasons.append(_(
                        "rapport claimt werk zonder schrijfactie"
                    ))
            if rec.state == "disputed":
                reasons.append(_(
                    "%s beweerde handeling(en) zonder geslaagde "
                    "tool-actie",
                ) % rec.claims_disputed_count)
            if rec.error_action_count >= max(1, floor):
                reasons.append(_(
                    "%s mislukte tool-acties",
                ) % rec.error_action_count)
            lost = max(
                0,
                (rec.write_attempt_count or 0) - (rec.write_action_count or 0),
            )
            if lost:
                uniq = rec._lost_write_reasons()
                reason_suffix = (
                    " (%s)" % ", ".join(uniq[:4]) if uniq else ""
                )
                if rec.write_action_count:
                    reasons.append(_(
                        "%(lost)s van %(n)s schrijfacties niet vastgelegd"
                        "%(detail)s",
                        lost=lost,
                        n=rec.write_attempt_count,
                        detail=reason_suffix,
                    ))
                elif uniq:
                    reasons.append(_(
                        "%(n)s schrijfpogingen, niets vastgelegd "
                        "(%(reasons)s)",
                        n=rec.write_attempt_count,
                        reasons=", ".join(uniq[:4]),
                    ))
                else:
                    reasons.append(_(
                        "%s schrijfpogingen, niets vastgelegd",
                    ) % rec.write_attempt_count)
            rec.needs_attention = bool(reasons)
            rec.attention_reason = "; ".join(reasons)[:255]

    # Foutteksten van de dispatcher die zeggen dat de aanroep zelf niet
    # af was. Zonder deze herkenning viel zo'n verloren schrijfactie
    # naamloos weg: run 825 verloor er vier en de reden noemde alleen
    # "scope", terwijl drie van de vier onvolledige aanroepen waren.
    _INCOMPLETE_CALL_MARKERS = (
        "missing required argument",
        "signature error",
        "is missing required",
    )

    def _lost_write_reasons(self):
        """Waarom schrijfpogingen niets vastlegden, met aantal per reden.

        Elke verloren poging krijgt een naam. Een poging die alleen een
        ``error`` terugkreeg had er geen: de reden bleef leeg en viel uit
        de regel weg. Op run 825 las de restlijst daardoor "4 van 7
        schrijfacties niet vastgelegd (scope)", terwijl er één scope-
        weigering was en drie aanroepen zonder verplicht argument. Wie
        dat leest gaat een rechtenprobleem onderzoeken dat er niet is.
        """
        self.ensure_one()
        tally = {}
        order = []
        for act in self.action_ids.filtered("is_write"):
            if act.is_effective_write:
                continue
            try:
                payload = json.loads(act.result or "{}")
            except Exception:  # noqa: BLE001
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            reason = str(payload.get("reason") or "")
            if not reason and payload.get("blocked_by_scope_guard"):
                reason = "scope"
            if not reason:
                error_text = str(payload.get("error") or "").lower()
                if any(m in error_text for m in
                       self._INCOMPLETE_CALL_MARKERS):
                    reason = _("onvolledige aanroep")
                elif error_text or act.is_error:
                    reason = _("fout")
            if not reason:
                reason = _("reden onbekend")
            if reason not in tally:
                order.append(reason)
            tally[reason] = tally.get(reason, 0) + 1
        return [
            ("%s\u00d7 %s" % (tally[r], r)) if tally[r] > 1 else r
            for r in order
        ]

    # ------------------------------------------------------------------
    # "Is dit inmiddels opgelost?" — als feit, niet als conclusie
    # ------------------------------------------------------------------
    resolved_by_run_id = fields.Many2one(
        "daadit.ai.agent.schedule.run", string="Opgelost door run",
        compute="_compute_resolution", store=True, ondelete="set null",
        help="De eerste LATERE geslaagde run van dezelfde planning die "
             "geen aandacht vraagt. Leeg wanneer die er niet is.",
    )
    resolved_on = fields.Datetime(
        string="Opgelost op", compute="_compute_resolution", store=True,
    )
    is_resolved = fields.Boolean(
        string="Inmiddels opgelost", compute="_compute_resolution",
        store=True,
        help="Door code vastgesteld: dezelfde planning had ná deze run "
             "een geslaagde run zonder aandachtsvlag. Een run met een "
             "onverifieerbare bewering geldt nooit als opgelost.",
    )

    resolution_kind = fields.Selection(
        [("opgelost", "Opgelost"),
         ("niet_opnieuw", "Niet opnieuw voorgekomen")],
        string="Opvolging", compute="_compute_resolution", store=True,
        help="Opgelost: een latere run deed hetzelfde soort werk wél met "
             "succes. Niet opnieuw voorgekomen: de planning draaide daarna "
             "zonder fout, maar zonder bewijs dat het probleem weg is; de "
             "run blijft dan op de restlijst staan.",
    )

    # Hoeveel latere runs we hoogstens bekijken op zoek naar bewijs.
    _RESOLUTION_LOOKAHEAD = 20

    def _proves_resolution_of(self, broken):
        """Bewijst deze latere run dat het probleem van ``broken`` weg is?

        Een opvolgrun die zelf schrijfpogingen verloor, bewijst niets.
        Verloor ``broken`` schrijfwerk, dan moet deze run zelf iets
        hebben vastgelegd: rustig draaien zonder iets te doen is
        "niet opnieuw voorgekomen", geen herstel.
        """
        self.ensure_one()
        if self.lost_write_count:
            return False
        return not broken.write_attempt_count or self.write_action_count > 0

    def _resolution_domain(self):
        """Waaraan een run moet voldoen om iets op te lossen."""
        self.ensure_one()
        return [
            ("schedule_id", "=", self.schedule_id.id),
            ("start_date", ">", self.start_date),
            ("state", "=", "done"),
            ("needs_attention", "=", False),
            # ``needs_attention`` dekt dit normaal al; expliciet omdat
            # een onverifieerbare bewering nooit iets mag oplossen, ook
            # niet als de aandachtlogica later verandert.
            ("claims_unverified", "=", False),
            ("id", "!=", self.id),
        ]

    @api.depends("schedule_id", "start_date", "claims_unverified", "state",
                 "needs_attention", "action_ids.is_write",
                 "action_ids.is_effective_write")
    def _compute_resolution(self):
        """Stel het feit vast in plaats van het aan het model te laten.

        Waargenomen 3-8-2026 in run 571: Argus verklaarde run 570
        "inmiddels opgelost" en noemde als bewijs de runs 568, 561, 551
        en 546 — alle vier gestart vóór 570. Een taalmodel dat vier
        datums moet vergelijken doet dat soms goed; de instructie is
        inmiddels aangescherpt, maar een instructie is geen garantie.

        Daarom staat het hier: dezelfde planning, een LATERE start, de
        run is geslaagd en vraagt zelf geen aandacht. Stond op deze run
        ``claims_unverified``, dan blijft het veld leeg — een run
        waarvan we niet weten of het beweerde werk is gebeurd, is niet
        iets wat je afgehandeld kunt noemen.
        """
        for rec in self:
            rec.resolved_by_run_id = False
            rec.resolved_on = False
            rec.is_resolved = False
            rec.resolution_kind = False
            if rec.claims_unverified:
                continue
            if not rec.schedule_id or not rec.start_date:
                # Een chatbeurt of een losse run hoort bij geen enkele
                # cadans; daar bestaat "een volgende run" niet.
                continue
            later = self.search(
                rec._resolution_domain(), order="start_date asc, id asc",
                limit=self._RESOLUTION_LOOKAHEAD,
            ).filtered(lambda run: not run.lost_write_count)
            if not later:
                continue
            proof = later.filtered(
                lambda run, broken=rec: run._proves_resolution_of(broken)
            )[:1]
            if not proof:
                rec.resolution_kind = "niet_opnieuw"
                continue
            rec.resolved_by_run_id = proof
            rec.resolved_on = proof.start_date
            rec.is_resolved = True
            rec.resolution_kind = "opgelost"

    # Velden waarvan de waarde van een run bepaalt of hij een EERDERE
    # run oplost. Verandert er één, dan moeten de eerdere runs van
    # dezelfde planning opnieuw worden nagerekend — Odoo ziet die
    # afhankelijkheid tussen records onderling niet.
    _RESOLUTION_TRIGGERS = frozenset({
        "schedule_id", "start_date", "state", "needs_attention",
        "claims_unverified",
    })

    def _recompute_resolution_of_earlier_runs(self):
        """Trek de eerdere runs van dezelfde planning er weer bij.

        ``resolved_by_run_id`` is opgeslagen zodat je erop kunt filteren
        en groeperen zonder elke rij te berekenen. De prijs daarvan is
        deze methode: een run lost een *andere* run op, en zo'n
        afhankelijkheid tussen records leidt Odoo niet uit ``@api.depends``
        af. Zonder deze hercompute zou een run die vandaag slaagt de
        foutieve run van gisteren stil op "niet opgelost" laten staan —
        een verouderd opgeslagen feit is erger dan geen feit.
        """
        earlier = self.browse()
        for rec in self:
            if not rec.schedule_id or not rec.start_date:
                continue
            earlier |= self.search([
                ("schedule_id", "=", rec.schedule_id.id),
                ("start_date", "<", rec.start_date),
                ("id", "!=", rec.id),
            ])
        earlier -= self
        if earlier:
            earlier._compute_resolution()

    @api.model_create_multi
    def create(self, vals_list):
        runs = super().create(vals_list)
        runs._recompute_resolution_of_earlier_runs()
        return runs

    def write(self, vals):
        result = super().write(vals)
        if self._RESOLUTION_TRIGGERS.intersection(vals):
            self._recompute_resolution_of_earlier_runs()
        return result

    def _replay_steps(self):
        """De denkstappen van deze beurt, opnieuw opgebouwd uit de audit.

        De live denkstappen (bus) zijn vluchtig: zodra het antwoord er
        staat, verdwijnen ze en kun je niet meer teruglezen wat de agent
        deed. Maar elke stap die een echte tool-aanroep was, staat óók in
        ``action_ids`` — dezelfde bron waar het feitelijke rapport uit
        wordt gebouwd. Deze methode maakt daar dezelfde regels weer van,
        met exact de gedeelde labelfunctie die de live-stap maakte
        (:func:`agent_steps.label_for_tool_call`). Zo is "wat je live zag"
        per definitie gelijk aan "wat er is vastgelegd": een terugleesbare
        lijst die niet kan afwijken van de audit.

        Bewust NIET gereconstrueerd: de cadans-regels ("Ik kijk ernaar")
        en de afsluiter ("Klaar"). Die horen bij geen enkele tool-aanroep
        en staan dus ook niet in de audit — het is pure UI-opvulling
        zonder controleerbare betekenis. De inspringing per sub-run
        (``depth``) is een live-only visueel hulpmiddel; de audit legt de
        volgorde vast, niet de nesting, dus de terugleesbare lijst is vlak
        (routing-stappen worden wel als zodanig gemarkeerd).

        Elke regel wordt, net als live, uit een vaste labelset gebouwd —
        nooit uit modeltekst, ruwe argumenten of tool-resultaten — zodat
        er geen intern verkeer of PII in de terugleesbare lijst kan
        belanden.
        """
        self.ensure_one()
        from ..services import agent_steps
        steps = []
        for act in self.action_ids.sorted(lambda a: (a.sequence, a.id)):
            name = act.tool_name or ""
            if not name:
                continue
            tool_call = {"function": {
                "name": name, "arguments": act.arguments or "",
            }}
            is_route = (
                name == agent_steps.ROUTER_TOOL_SLUG
                or name.startswith("ir_actions_server_ask_")
            )
            steps.append({
                "seq": act.sequence or (len(steps) + 1),
                "text": agent_steps.label_for_tool_call(tool_call),
                "kind": "route" if is_route else "tool",
                "depth": 0,
            })
        return steps

    def _verified_changes(self):
        """What this run demonstrably changed, read from the action log.

        The action log is the only account of a run that the model did
        not write. A write call counts here when the tool answered
        without an error and without refusing — a scope-guard refusal or
        a simulated test-run result is not a change, however the summary
        describes it.
        """
        self.ensure_one()
        out = []
        for act in self.action_ids:
            if not act.is_effective_write or not act.change_kind:
                continue
            out.append({
                "tool": act.tool_name or "?",
                "model": act.target_model or "",
                "id": act.target_ref or 0,
                "kind": act.change_kind,
            })
        return out

    def _factual_footer(self):
        """A code-generated statement of what this run actually did.

        The agent writes its own summary, and that summary is prose: it
        can claim work that never happened. Observed 01-08-2026 while
        validating a project agent that had just been given write
        access — it reported three "door de tool bevestigde" task
        assignments, naming the tool and the assignee, in a run whose
        captured action list held six read calls and not a single write.
        The records were untouched. The prompt had explicitly told it to
        report only tool-confirmed changes; instructions are not a
        control.

        So this line is not written by the model. It counts the captured
        tool calls, which is the same source the action log is built
        from, and states them under every report. A reader can then
        always separate narration from fact without opening the run.
        """
        self.ensure_one()
        try:
            self.invalidate_recordset(
                ["action_count", "write_action_count"]
            )
        except Exception:  # noqa: BLE001
            self.env.invalidate_all()
        total = self.action_count or 0
        writes = self.write_action_count or 0
        errors = len(self.action_ids.filtered(lambda a: a.is_error))
        attempts = self.write_attempt_count or 0
        parts = [
            _("%s tool-aanroepen") % total,
            _("%s schrijfacties") % writes,
        ]
        if attempts > writes:
            # Zwijgen over geweigerde pogingen zou de indruk wekken dat
            # de agent het niet probeerde, terwijl hij werd tegengehouden.
            # De reden staat erbij: "7 pogingen geweigerd" laat een lezer
            # gissen tussen een rechtenprobleem en werk dat al openstond,
            # en dat verschil bepaalt of er iets te doen valt (run 820).
            try:
                why = self._lost_write_reasons()
            except Exception:  # noqa: BLE001
                why = []
            parts.append(
                _("%(n)s poging(en) geweigerd of overgeslagen%(why)s",
                  n=attempts - writes,
                  why=(" (%s)" % ", ".join(why[:4])) if why else "")
            )
        if errors:
            parts.append(_("waarvan %s met een fout") % errors)
        line = _(
            "Feitelijk vastgelegd door het systeem: %s."
        ) % ", ".join(parts)
        if self.trigger == "test":
            line += " " + _(
                "Dit was een testrun: schrijfacties zijn gesimuleerd en "
                "NIET uitgevoerd, ook als de tekst hierboven anders "
                "suggereert."
            )
        elif writes == 0:
            line += " " + _(
                "Er is dus niets gewijzigd. Beschrijft de tekst hierboven "
                "wel doorgevoerde wijzigingen, dan klopt die tekst niet."
            )
        block = "\n\n---\n_" + line + "_"

        # De namen van de records die echt zijn geraakt. Een lezer hoeft
        # dan niet op het woord van de agent af te gaan en kan elk item
        # zelf openen.
        changes = self._verified_changes()
        if changes:
            labels = dict(
                self.env["daadit.ai.agent.schedule.run.action"]
                ._fields["change_kind"].selection
            )
            rows = []
            for chg in changes[:20]:
                where = chg["model"] or chg["tool"]
                kind = labels.get(chg["kind"], chg["kind"])
                if chg["model"] and chg["id"]:
                    # Markdown, because ``findings`` is rendered through
                    # markdown2 — the reader can open the record itself
                    # instead of taking the report's word for it.
                    ref = "[%s #%s](/odoo/%s/%s)" % (
                        chg["model"], chg["id"], chg["model"], chg["id"],
                    )
                else:
                    ref = "%s%s" % (
                        where, " #%s" % chg["id"] if chg["id"] else "",
                    )
                rows.append("- %s: %s" % (kind, ref))
            block += "\n\n_" + _("Feitelijk gewijzigde records:") + "_\n"
            block += "\n".join(rows)
            if len(changes) > 20:
                block += "\n- " + _("… en nog %s") % (len(changes) - 20)
        return block

    def _record_delegations(self, entries):
        """File delegated sub-runs under this run, parents first."""
        self.ensure_one()
        Delegation = self.env["daadit.ai.agent.schedule.run.delegation"].sudo()
        created = []
        for seq, entry in enumerate(entries or [], start=1):
            parent_index = entry.get("parent_index")
            parent = (
                created[parent_index]
                if parent_index is not None and parent_index < len(created)
                else Delegation
            )
            created.append(Delegation.create({
                "run_id": self.id,
                "parent_id": parent.id or False,
                "sequence": seq,
                "depth": entry.get("depth") or 1,
                "caller_agent_id": entry.get("caller_agent_id") or False,
                "agent_id": entry.get("agent_id") or False,
                "provider": entry.get("provider") or "",
                "question": entry.get("question") or "",
                "answer": (entry.get("answer") or "")[:_MAX_ACTION_BLOB],
                "error": (entry.get("error") or "")[:_MAX_ACTION_BLOB],
                "state": entry.get("state") or "failed",
                "tool_calls": entry.get("tool_calls") or 0,
                "write_calls": entry.get("write_calls") or 0,
                "start_date": entry.get("start_date") or False,
                "end_date": entry.get("end_date") or False,
                "prompt_tokens": entry.get("prompt_tokens") or 0,
                "completion_tokens": entry.get("completion_tokens") or 0,
                "estimated_cost_usd": entry.get("estimated_cost_usd") or 0.0,
            }))
        return Delegation.browse([d.id for d in created])

    def _record_actions(self, actions):
        """Persist captured tool calls as child action rows."""
        self.ensure_one()
        Action = self.env["daadit.ai.agent.schedule.run.action"].sudo()
        seq = 0
        for a in actions or []:
            seq += 1
            result = a.get("result")
            try:
                result_str = json.dumps(result, default=str, indent=2)
            except Exception:  # noqa: BLE001
                result_str = str(result)
            args = a.get("arguments")
            if not isinstance(args, str):
                try:
                    args = json.dumps(args, default=str, indent=2)
                except Exception:  # noqa: BLE001
                    args = str(args)
            Action.create({
                "run_id": self.id,
                "sequence": seq,
                "tool_name": a.get("tool_name") or "",
                "arguments": (args or "")[:_MAX_ACTION_BLOB],
                "result": (result_str or "")[:_MAX_ACTION_BLOB],
                "is_error": bool(a.get("is_error")),
            })
        if self.rolled_back:
            remote = self._remote_write_tool_names()
            self.action_ids.filtered(
                lambda act: act.is_effective_write
                and act.tool_name not in remote
            ).write({"rolled_back": True})

    # ------------------------------------------------------------------
    # Terugdraaien als een run halverwege vastloopt (taak 1488)
    # ------------------------------------------------------------------
    def _rollback_survivor_models(self):
        """Modellen waarvan wat de run aanmaakt een rollback overleeft:
        verbruik moet betaald blijven, een spoor van iets dat buiten deze
        database is gebeurd moet blijven staan."""
        return [m for m in _USAGE_MODELS if m in self.env]

    def _remote_write_tool_names(self):
        """Schrijftools die buiten deze database werken: een savepoint
        draait die niet terug, daar hoort een herstelvoorstel bij."""
        return frozenset()

    def _rollback_marks(self):
        self.ensure_one()
        marks = {}
        for model_name in self._rollback_survivor_models():
            last = self.env[model_name].sudo().search(
                [], order="id desc", limit=1,
            )
            marks[model_name] = last.id or 0
        return marks

    def _rollback_survivors(self, marks):
        """De nieuwe rijen van de overlevende modellen, als create-vals."""
        rows = {}
        for model_name, mark in marks.items():
            Model = self.env[model_name].sudo()
            names = [
                name for name, field in Model._fields.items()
                if field.store and not field.compute
                and field.type not in ("one2many", "many2many")
                and name not in models.MAGIC_COLUMNS
                and name not in ("id", "display_name")
            ]
            records = Model.search([("id", ">", mark)], order="id")
            if records:
                rows[model_name] = records.read(names, load=None)
        return rows

    def _close_run_savepoint(self, savepoint, marks, rollback):
        """Sluit de savepoint van de run; bij ``rollback`` gaat alles wat
        de run lokaal deed eruit, behalve de overlevende rijen."""
        self.ensure_one()
        if savepoint.closed:
            return
        if not rollback:
            self._close_savepoint(savepoint, rollback=False)
            return
        try:
            survivors = self._rollback_survivors(marks)
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_agent_schedule: run %s — overlevende rijen "
                "niet te lezen vóór de rollback", self.id,
            )
            survivors = {}
        if not self._close_savepoint(savepoint, rollback=True):
            return
        self.env.invalidate_all(flush=False)
        recreated = {}
        for model_name, rows in survivors.items():
            Model = self.env[model_name].sudo()
            vals_list = []
            for row in rows:
                vals = dict(row)
                vals.pop("id", None)
                for name, value in list(vals.items()):
                    field = Model._fields[name]
                    if field.type == "many2one" and value and not (
                        self.env[field.comodel_name].sudo()
                        .browse(value).exists()
                    ):
                        vals[name] = False
                vals_list.append(vals)
            recreated[model_name] = Model.create(vals_list)
        self.write({"rolled_back": True})
        _logger.warning(
            "daadit_ai_agent_schedule: run %s liep vast — lokale "
            "wijzigingen teruggedraaid", self.id,
        )
        try:
            self._after_local_rollback(recreated)
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_agent_schedule: run %s — herstelvoorstel niet "
                "gemaakt", self.id,
            )

    def _close_savepoint(self, savepoint, rollback):
        """Sluit de savepoint van de run en geef terug of dat lukte.

        Commit iets tijdens de run (een provider die zijn logregels
        wegschrijft), dan bestaat de savepoint niet meer. Een kale
        ``RELEASE`` zou dan de hele transactie laten vastlopen; achter
        een eigen bewaker blijft de transactie bruikbaar en wordt er
        niets teruggedraaid.
        """
        self.ensure_one()
        cr = self.env.cr
        if rollback:
            cr.clear()
        else:
            cr.flush()
        guard = "guard_%s" % savepoint.name
        try:
            with mute_logger("odoo.sql_db"):
                cr.execute('SAVEPOINT "%s"' % guard)
        except psycopg2.Error:
            # Afgebroken transactie: alleen terugdraaien kan nog.
            savepoint.close(rollback=True)
            return True
        try:
            with mute_logger("odoo.sql_db"):
                if rollback:
                    cr.execute('ROLLBACK TO SAVEPOINT "%s"' % savepoint.name)
                cr.execute('RELEASE SAVEPOINT "%s"' % savepoint.name)
        except psycopg2.Error:
            cr.execute('ROLLBACK TO SAVEPOINT "%s"' % guard)
            cr.execute('RELEASE SAVEPOINT "%s"' % guard)
            savepoint.closed = True
            _logger.warning(
                "daadit_ai_agent_schedule: run %s — savepoint verdwenen "
                "door een tussentijdse commit; niets teruggedraaid",
                self.id,
            )
            return False
        savepoint.closed = True
        return True

    def _after_local_rollback(self, recreated):
        """Haak voor wat een savepoint niet terugdraait."""
        return None

    def _link_usage(self, prev_usage_id, usage_model=None):
        """Best-effort: link the provider usage row this run created and
        copy token / iteration counts onto the run.

        ``usage_model`` is the provider add-on's usage model
        (``daadit_ai_<code>.usage``); providers without one — stock
        OpenAI / Gemini — simply skip this step and keep zero tokens.
        """
        self.ensure_one()
        usage_model = usage_model or "daadit_ai_mistral.usage"
        if usage_model not in self.env:
            return
        Usage = self.env[usage_model].sudo()
        row = Usage.search(
            [("id", ">", prev_usage_id or 0),
             ("kind", "=", "chat"),
             ("agent_id", "=", self.agent_id.id)],
            order="id desc", limit=1,
        )
        if not row:
            return
        vals = {
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "iterations": row.iterations,
            "model": row.model or self.model,
            "usage_model": usage_model,
            "usage_row_id": row.id,
        }
        self.write(vals)

    def _record_fallback(self, bridge, prev_usage_ids):
        """Leg vast dat een beurt door een andere provider liep.

        Nooit stil van model wisselen: de run noemt de uitwijkprovider,
        het model en de reden. Had de eigen provider geen verbruiksregel
        (hij lag eruit), dan telt die van de uitwijkprovider.
        """
        self.ensure_one()
        provider, model, reason = bridge.read_fallback()
        if not provider:
            return
        self.write({
            "fallback_provider": provider,
            "fallback_model": model or "",
            "fallback_reason": (reason or "")[:250],
        })
        _logger.warning(
            "daadit_ai_agent_schedule: run %s uitgeweken van %s naar "
            "%s/%s — %s", self.id, self.provider, provider, model,
            reason,
        )
        fallback_usage = "daadit_ai_%s.usage" % provider
        if not self.usage_row_id and fallback_usage in prev_usage_ids:
            self._link_usage(
                prev_usage_ids[fallback_usage], fallback_usage,
            )


class AiAgentScheduleRunDelegation(models.Model):
    _name = "daadit.ai.agent.schedule.run.delegation"
    _description = "AI Agent Schedule Run — Delegated sub-run"
    _order = "run_id, sequence, id"

    run_id = fields.Many2one(
        "daadit.ai.agent.schedule.run", string="Run", required=True,
        ondelete="cascade", index=True,
    )
    parent_id = fields.Many2one(
        "daadit.ai.agent.schedule.run.delegation", string="Doorgegeven door",
        ondelete="cascade", index=True,
    )
    child_ids = fields.One2many(
        "daadit.ai.agent.schedule.run.delegation", "parent_id",
        string="Verder doorgegeven",
    )
    sequence = fields.Integer(string="#", default=1)
    depth = fields.Integer(string="Niveau", default=1, readonly=True)
    caller_agent_id = fields.Many2one(
        "ai.agent", string="Gevraagd door", readonly=True, ondelete="set null",
    )
    agent_id = fields.Many2one(
        "ai.agent", string="Collega", readonly=True, ondelete="set null",
    )
    provider = fields.Char(string="Provider", readonly=True)
    question = fields.Text(string="Vraag", readonly=True)
    answer = fields.Text(string="Antwoord", readonly=True)
    error = fields.Text(string="Fout", readonly=True)
    state = fields.Selection(
        [("answered", "Beantwoord"), ("failed", "Mislukt")],
        string="Status", readonly=True, default="failed",
    )
    tool_calls = fields.Integer(string="Toolaanroepen", readonly=True)
    write_calls = fields.Integer(string="Schrijfacties", readonly=True)
    start_date = fields.Datetime(string="Start", readonly=True)
    end_date = fields.Datetime(string="Einde", readonly=True)
    prompt_tokens = fields.Integer(string="Prompt tokens", readonly=True)
    completion_tokens = fields.Integer(
        string="Completion tokens", readonly=True,
    )
    estimated_cost_usd = fields.Float(
        string="Geschatte kosten (USD)", readonly=True,
    )


class AiAgentScheduleRunAction(models.Model):
    _name = "daadit.ai.agent.schedule.run.action"
    _description = "AI Agent Schedule Run — Tool Action"
    _order = "run_id, sequence, id"

    run_id = fields.Many2one(
        "daadit.ai.agent.schedule.run", string="Run", required=True,
        ondelete="cascade", index=True,
    )
    sequence = fields.Integer(string="#", default=1)
    tool_name = fields.Char(string="Tool", readonly=True)
    arguments = fields.Text(string="Arguments", readonly=True)
    result = fields.Text(string="Result", readonly=True)
    result_preview = fields.Char(
        string="Result (preview)", readonly=True,
        compute="_compute_result_preview", store=True,
        help="First ~120 characters of the result, for the list view.",
    )
    is_error = fields.Boolean(string="Error", readonly=True)
    rolled_back = fields.Boolean(
        string="Teruggedraaid", readonly=True,
        help="Deze schrijfactie is teruggedraaid omdat de run vastliep.",
    )
    is_write = fields.Boolean(
        string="Write action", readonly=True,
        compute="_compute_is_write", store=True,
        help="True when the tool call mutates database state. "
             "False for read/get/search/group_by and menu-navigation "
             "calls, and also False for hallucinated tool names that "
             "were rejected by the dispatcher as 'Unknown tool' — "
             "those are model mistakes, not real write attempts.",
    )

    @api.depends("result")
    def _compute_result_preview(self):
        for rec in self:
            text = (rec.result or "").strip()
            # Collapse whitespace so JSON wrapping doesn't waste the
            # preview budget, then trim with ellipsis.
            text = " ".join(text.split())
            rec.result_preview = (text[:117] + "…") if len(text) > 120 else text

    is_effective_write = fields.Boolean(
        string="Heeft echt geschreven",
        compute="_compute_is_effective_write", store=True,
        help="Een schrijfactie die de tool ook daadwerkelijk heeft "
             "uitgevoerd. Een weigering, een overgeslagen actie of een "
             "gesimuleerd testresultaat telt hier niet mee.",
    )

    @api.depends("is_write", "is_error", "result")
    def _compute_is_effective_write(self):
        """Onderscheid een poging van een wijziging.

        Waargenomen 01-08-2026: run 523 stond op drie schrijfacties
        terwijl er nul waren. Twee ervan waren scope-guard-weigeringen,
        de derde kwam terug als ``{"ok": true, "skipped": true}`` —
        overgeslagen wegens een volle takenlijst, maar met een
        succesvlag. Zowel het model als de verificatielaag las dat als
        gedaan werk.
        """
        for rec in self:
            if not rec.is_write or rec.is_error:
                rec.is_effective_write = False
                continue
            try:
                payload = json.loads(rec.result or "{}")
            except Exception:  # noqa: BLE001
                payload = {}
            if not isinstance(payload, dict):
                rec.is_effective_write = True
                continue
            refused = (
                payload.get("ok") is False
                # Honest write envelope (taak 779 / 1060): written=false
                # means nothing landed, even if an older client omitted
                # skipped=true.
                or payload.get("written") is False
                or payload.get("skipped")
                or payload.get("blocked_by_scope_guard")
                or payload.get("throttled")
                or payload.get("test_run")
                or payload.get("status") == "simulated"
            )
            # An in-place refresh of an existing activity IS an effect.
            if payload.get("updated") and payload.get("ok") is True:
                refused = False
            rec.is_effective_write = not refused

    change_kind = fields.Selection(
        [("create", "Aangemaakt"),
         ("write", "Gewijzigd"),
         ("unlink", "Verwijderd")],
        string="Soort wijziging",
        compute="_compute_effect", store=True,
    )
    target_model = fields.Char(
        string="Doelmodel", compute="_compute_effect", store=True,
    )
    target_ref = fields.Integer(
        string="Doelrecord", compute="_compute_effect", store=True,
    )
    is_publication = fields.Boolean(
        string="Heeft gepubliceerd", compute="_compute_effect", store=True,
        help="Een geslaagde schrijfactie die iets echt naar buiten zette "
             "(live gezet). Een concept klaarzetten, een post inplannen "
             "of iets de-publiceren telt hier niet mee — die dekken de "
             "bewering 'gepubliceerd' dus niet.",
    )

    @api.depends("is_effective_write", "tool_name", "arguments", "result")
    def _compute_effect(self):
        """What this call changed, as data rather than as prose.

        Stored per action so the run report can be BUILT from the
        effects instead of describing them: model, record and kind of
        change, read from the call's own arguments and result. A run
        with no rows here changed nothing, whatever its summary says.

        Both sides are consulted because custom tools answer in their
        own shape: the arguments carry ``model_name``/``record_id`` on
        the stock tools, while a custom action usually returns the id it
        created (``{"ok": true, "task_id": 91}``).
        """
        for rec in self:
            if not rec.is_effective_write:
                rec.change_kind = False
                rec.target_model = False
                rec.target_ref = 0
                rec.is_publication = False
                continue
            args = rec._json_field("arguments")
            result = rec._json_field("result")
            model = (
                args.get("model_name")
                or result.get("model_name")
                or result.get("model")
                or ""
            )
            ref = rec._first_id(args) or rec._first_id(result)
            name = (rec.tool_name or "").lower()
            if any(v in name for v in _UNLINK_VERBS):
                kind = "unlink"
            elif result.get("created") or result.get("created_id"):
                kind = "create"
            elif any(v in name for v in _CREATE_VERBS):
                kind = "create"
            else:
                # A write tool that neither created nor removed anything
                # changed an existing record; that is the residual case,
                # not a guess.
                kind = "write"
            rec.change_kind = kind
            rec.target_model = model[:64] if model else False
            rec.target_ref = ref
            rec.is_publication = rec._is_publication_effect(name, result)

    @staticmethod
    def _is_publication_effect(name, result):
        """Zette deze schrijfactie iets echt live?

        Een de-publicatie, een concept of een geplande post lijken op een
        publicatie in de naam maar zijn het niet; die worden eerst
        uitgesloten. Blijft de naam een publicatiewerkwoord, of zegt het
        toolresultaat zelf dat het record nu gepubliceerd is, dan telt
        het als publicatie. Alleen zo dekt een effect de bewering
        'gepubliceerd'.
        """
        name = name or ""
        if any(v in name for v in _NON_PUBLISH_VERBS):
            return False
        if any(v in name for v in _PUBLISH_VERBS):
            return True
        if isinstance(result, dict):
            return (
                result.get("is_published") is True
                or result.get("published") is True
            )
        return False

    def _json_field(self, field_name):
        """Parse a JSON text field, returning {} for anything else."""
        self.ensure_one()
        try:
            value = json.loads(self[field_name] or "{}")
        except Exception:  # noqa: BLE001
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _first_id(payload):
        """The record id in a tool payload, or 0.

        ``record_id`` and ``id`` first; otherwise the first integer
        ``*_id`` key, because custom tools name their target after the
        thing they touch (``task_id``, ``ticket_id``, ``post_id``).
        """
        for key in ("record_id", "id"):
            value = payload.get(key)
            if isinstance(value, int) and value:
                return value
        for key, value in payload.items():
            if (
                key.endswith("_id")
                and isinstance(value, int)
                and value
                and key != "user_id"
            ):
                return value
        return 0

    @api.depends("tool_name", "result")
    def _compute_is_write(self):
        readonly = readonly_tool_names(self.env) | _READONLY_CLASSIFY_ONLY
        for rec in self:
            name = rec.tool_name or ""
            if not name or name in readonly:
                rec.is_write = False
                continue
            # Hallucinated tool names: the model invented a tool that
            # doesn't exist (often a translated/localised variant). The
            # dispatcher rejects these with ``{"error": "Unknown tool: ...}``
            # — no state was mutated, so don't surface as a write.
            if "Unknown tool" in (rec.result or ""):
                rec.is_write = False
                continue
            rec.is_write = True
