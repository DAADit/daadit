# -*- coding: utf-8 -*-
"""Capture the tool actions a scheduled agent performs.

The scheduled run drives the agent through the *exact* audited provider
path the live chat uses (provider ``LLMApiService`` patch → the shared
``daadit_ai_agentic_system`` ``tool_dispatch.run_tool_call``). That path returns only the agent's
final text to the caller — it does not expose the individual tool calls.

To log "which actions were performed" per run we install a thin wrapper
around ``daadit_ai_agentic_system.services.tool_dispatch.run_tool_call``. The
wrapper is a no-op unless a capture buffer is active on the current
thread (set by the schedule runner around the call), so it adds zero
behaviour to normal interactive chat.

Why patch the module attribute: ``llm_api_patch._request_llm_mistral``
calls ``tool_dispatch.run_tool_call(...)`` by attribute lookup at call
time, so reassigning ``tool_dispatch.run_tool_call`` is picked up
without touching the provider module.
"""
import functools
from datetime import datetime
import json
import logging
import threading

from odoo.addons.daadit_ai_agentic_system.services import (
    tool_dispatch as shared_dispatch,
)

_logger = logging.getLogger(__name__)

# Per-thread capture state. ``buffer`` is a list while a scheduled run is
# active, ``None`` otherwise (interactive chat → wrapper is transparent).
# ``test_mode`` + ``readonly_tools`` are set by :func:`start_capture` for
# test runs: while active, any tool whose name is NOT in
# ``readonly_tools`` is *not executed* — the wrapper returns a simulated
# success so the model reasons on as if the write happened, and the run
# log shows exactly what the agent *would* have done.
_state = threading.local()


# The stub fed back to the model in place of a real write result. The
# message doubles as an instruction so the model keeps going instead of
# retrying or reporting a failure.
_SIMULATED_RESULT = {
    "test_run": True,
    "status": "simulated",
    "message": (
        "TEST RUN: this write action was NOT executed — the system was "
        "not modified. Assume it succeeded and continue with the rest "
        "of your task; describe the action in your final log as "
        "'(testrun — niet doorgevoerd)'."
    ),
}


def start_capture(test_mode=False, readonly_tools=None,
                  method_capture=False, readonly_methods=None):
    """Begin recording tool calls on this thread. Returns nothing; pair
    with :func:`stop_capture` in a try/finally.

    :param test_mode: when True, tool calls whose name is not in
        ``readonly_tools`` are intercepted instead of executed (dry
        run). Read/search/navigation tools still run so the agent has
        real data to reason on.
    :param readonly_tools: set of tool names that are safe to execute
        in test mode. Required when ``test_mode`` is True.
    """
    _state.buffer = []
    _state.delegations = []
    _state.delegation_stack = []
    _state.test_mode = bool(test_mode)
    _state.readonly_tools = frozenset(readonly_tools or ())
    _state.method_capture = bool(method_capture)
    _state.readonly_methods = frozenset(readonly_methods or ())


def stop_capture():
    """Stop recording and return the list of captured action dicts
    (possibly empty). Safe to call even if capture was never started."""
    buf = getattr(_state, "buffer", None)
    _state.buffer = None
    _state.test_mode = False
    _state.readonly_tools = frozenset()
    _state.method_capture = False
    _state.readonly_methods = frozenset()
    return buf or []


def is_capturing():
    """Of er op deze thread een run wordt vastgelegd."""
    return getattr(_state, "buffer", None) is not None


def peek():
    """Een kopie van wat er tot nu toe is vastgelegd, zonder te stoppen."""
    return list(getattr(_state, "buffer", None) or [])


def _call_parts(tool_call):
    """``(name, arguments)`` of one tool call, whatever the provider.

    Mistral and loes.ai hand the dispatcher an OpenAI-style call
    (``{"function": {"name", "arguments"}}``); Claude hands it an
    Anthropic ``tool_use`` block (``{"name", "input"}``).
    """
    if not isinstance(tool_call, dict):
        return "", ""
    fn = tool_call.get("function")
    if isinstance(fn, dict):
        return fn.get("name") or "", fn.get("arguments") or ""
    return tool_call.get("name") or "", tool_call.get("input") or ""


def _record(tool_call, result):
    buf = getattr(_state, "buffer", None)
    if buf is None:
        return
    name, arguments = _call_parts(tool_call)
    is_error = isinstance(result, dict) and (
        "error" in result or result.get("_daadit_access_denied")
    )
    buf.append({
        "tool_name": name,
        "arguments": arguments,
        "result": result,
        "is_error": bool(is_error),
    })


# De modellen waarmee een tool-aanroep aan een collega werkt: de
# agentkaart zelf, en het knowledge-artikel dat zijn prompt bevat (de
# promptregistry is daar de bron van waarheid).
_REPAIR_TARGET_MODELS = ("ai.agent", "knowledge.article")


def _repair_scope_refusal(agent, tool_call):
    """Weiger een aanroep die buiten het reparatiebereik valt (taak 727).

    Levert ``None`` wanneer de aanroep gewoon door mag — dat is het
    normale geval: de poort geldt alleen voor een agent waarvoor iemand
    een reparatieregel heeft ingesteld.

    De poort kijkt naar het doel van de aanroep, niet naar de tekst
    eromheen: een schrijfaanroep op ``ai.agent`` of op een
    knowledge-artikel identificeert de collega die wordt aangepast. Kan
    het doel niet worden vastgesteld, dan blokkeert deze poort niets —
    hij is bedoeld om een bekende grens hard te maken, niet om een agent
    zijn gewone werk af te nemen.
    """
    if agent is None or getattr(agent, "env", None) is None:
        return None
    name, args = _call_parts(tool_call)
    if not name:
        return None
    try:
        if not agent.sudo().daadit_repair_scope_ids.filtered("effective"):
            return None
        from ..models.ai_agent_schedule import (
            _READONLY_CLASSIFY_ONLY, _READONLY_TOOL_NAMES,
        )
        if name in _READONLY_TOOL_NAMES or name in _READONLY_CLASSIFY_ONLY:
            # Lezen is geen repareren; Argus moet incidenten kunnen
            # onderzoeken van iedereen.
            return None
        if isinstance(args, str):
            args = json.loads(args or "{}")
        if not isinstance(args, dict):
            return None
        model_name = args.get("model_name") or args.get("model") or ""
        if model_name not in _REPAIR_TARGET_MODELS:
            return None
        record_id = args.get("record_id") or args.get("id") or 0
        if not isinstance(record_id, int) or not record_id:
            return None
        target = agent.env["ai.agent"].browse()
        article_ref = 0
        if model_name == "ai.agent":
            target = agent.env["ai.agent"].sudo().browse(record_id).exists()
        else:
            article_ref = record_id
        allowed, reason = agent.daadit_may_repair(
            target_agent=target or None, article_ref=article_ref,
        )
        if allowed:
            return None
    except Exception:  # noqa: BLE001
        _logger.exception(
            "daadit_ai_agent_schedule.run_capture: kon het "
            "reparatiebereik van agent %s niet vaststellen — aanroep "
            "%r ongemoeid gelaten", getattr(agent, "id", "?"), name,
        )
        return None
    _logger.info(
        "daadit_ai_agent_schedule.run_capture: aanroep %r geweigerd — "
        "buiten het reparatiebereik van agent %s", name, agent.id,
    )
    # Geen ``error``: dit is een weigering, geen mislukte aanroep. De
    # bestaande effectlaag leest ``ok: false`` al als "niets gewijzigd",
    # en de foutteller blijft zo voorbehouden aan echte fouten.
    return {
        "ok": False,
        "blocked_by_repair_scope": True,
        "reason": "repair_scope",
        "message": reason,
    }


# Provider dispatch modules already wrapped (by module __name__), so a
# second run against the same provider doesn't double-wrap.
_WRAPPED_DISPATCHERS = set()


def install(tool_dispatch=None):
    """Idempotently wrap a provider's ``tool_dispatch.run_tool_call``.

    :param tool_dispatch: the provider add-on's dispatch module
        (``daadit_ai_<code>.services.tool_dispatch``). Resolved by
        ``provider_bridge`` at run time, so any provider — Mistral,
        loes.ai, Claude, or one installed next month — gets the same
        dispatcher-level capture. When omitted, the shared dispatcher
        of daadit_ai_agentic_system is wrapped.

    Called lazily from the schedule runner so we never import a
    provider module at registry-build time (ordering safety).
    """
    if tool_dispatch is None:
        tool_dispatch = shared_dispatch

    key = getattr(tool_dispatch, "__name__", str(tool_dispatch))
    if key in _WRAPPED_DISPATCHERS:
        return True

    original = getattr(tool_dispatch, "run_tool_call", None)
    if original is None:
        _logger.warning(
            "daadit_ai_agent_schedule.run_capture: tool_dispatch has no "
            "run_tool_call attribute — cannot install capture wrapper."
        )
        return False
    if getattr(original, "_daadit_schedule_wrapped", False):
        _WRAPPED_DISPATCHERS.add(key)
        return True

    @functools.wraps(original)
    def _wrapper(agent, tool_call):
        # Test-run interception: while a dry run is active on this
        # thread, write tools are answered with a simulated success
        # instead of being dispatched. Only tools in the caller's
        # read-only allowlist actually execute. Buffer-off (interactive
        # chat) or test_mode-off paths are untouched.
        if (
            getattr(_state, "buffer", None) is not None
            and getattr(_state, "test_mode", False)
        ):
            name, arguments = _call_parts(tool_call)
            readonly = getattr(_state, "readonly_tools", frozenset())
            if name and name not in readonly:
                result = dict(_SIMULATED_RESULT)
                _logger.info(
                    "daadit_ai_agent_schedule.run_capture: TEST RUN — "
                    "write tool %r intercepted, not executed "
                    "(args=%s)", name,
                    json.dumps(arguments, default=str)[:200],
                )
                try:
                    _record(tool_call, result)
                except Exception:  # noqa: BLE001
                    _logger.exception(
                        "daadit_ai_agent_schedule.run_capture: failed "
                        "to record an intercepted tool call"
                    )
                return result

        blocked = _repair_scope_refusal(agent, tool_call)
        if blocked is not None:
            _record(tool_call, blocked)
            return blocked

        result = original(agent, tool_call)
        try:
            _record(tool_call, result)
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_agent_schedule.run_capture: failed to record "
                "a tool call — continuing (chat/run unaffected)"
            )
        return result

    _wrapper._daadit_schedule_wrapped = True
    _wrapper._daadit_original = original
    tool_dispatch.run_tool_call = _wrapper
    _WRAPPED_DISPATCHERS.add(key)
    _logger.info(
        "daadit_ai_agent_schedule.run_capture: %s.run_tool_call wrapped "
        "for scheduled-run action capture", key,
    )
    return True


# ---------------------------------------------------------------------------
# Method-level hooks — provider-agnostic capture (v19.0.3.0.0)
# ---------------------------------------------------------------------------
#
# Stock providers (OpenAI / Gemini) execute tools through Odoo's own
# LLMApiService loop, which never touches daadit_ai_mistral's
# ``tool_dispatch.run_tool_call`` — so the wrapper above records nothing
# for those runs. The one chokepoint every provider shares is the
# ``ai.agent._ai_tool_*`` methods themselves: stock's server actions eval
# ``record._ai_tool_search(...)`` and Mistral's dispatcher ends up in the
# same methods.
#
# ``install_method_hooks`` wraps every ``_ai_tool_*`` callable on the
# registry class. The wrapper is transparent unless BOTH a capture buffer
# is active AND ``method_capture`` was requested for this run — Mistral
# runs keep using the run_tool_call wrapper (which also sees hallucinated
# tool names that never reach a method) and set method_capture=False so
# nothing is recorded twice.

_METHOD_PREFIX = "_ai_tool_"
_PUBLIC_PREFIX = "ir_actions_server_"


def _public_tool_name(method_name):
    """``_ai_tool_search`` -> ``ir_actions_server_search`` so run-action
    rows keep one consistent naming scheme (and the is_write
    classification keeps working) regardless of capture level."""
    if method_name.startswith(_METHOD_PREFIX):
        return _PUBLIC_PREFIX + method_name[len(_METHOD_PREFIX):]
    return method_name


def _record_method_call(method_name, args, kwargs, result):
    buf = getattr(_state, "buffer", None)
    if buf is None:
        return
    try:
        arg_repr = json.dumps(
            {"args": list(args), "kwargs": kwargs}, default=str,
        )
    except Exception:  # noqa: BLE001
        arg_repr = str({"args": args, "kwargs": kwargs})
    is_error = isinstance(result, dict) and "error" in result
    buf.append({
        "tool_name": _public_tool_name(method_name),
        "arguments": arg_repr,
        "result": result,
        "is_error": bool(is_error),
    })


def _make_method_wrapper(method_name, original):
    @functools.wraps(original)
    def _wrapper(self, *args, **kwargs):
        active = (
            getattr(_state, "buffer", None) is not None
            and getattr(_state, "method_capture", False)
        )
        if not active:
            return original(self, *args, **kwargs)
        # Test-run dry mode at method level: anything outside the
        # read-only allowlist is answered with the simulated stub.
        if (
            getattr(_state, "test_mode", False)
            and method_name not in getattr(
                _state, "readonly_methods", frozenset()
            )
        ):
            result = dict(_SIMULATED_RESULT)
            _logger.info(
                "daadit_ai_agent_schedule.run_capture: TEST RUN — "
                "write method %r intercepted, not executed", method_name,
            )
            _record_method_call(method_name, args, kwargs, result)
            return result
        result = original(self, *args, **kwargs)
        try:
            _record_method_call(method_name, args, kwargs, result)
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_agent_schedule.run_capture: failed to record "
                "method call %r — continuing", method_name,
            )
        return result
    _wrapper._daadit_schedule_method_wrapped = True
    _wrapper._daadit_original = original
    return _wrapper


def install_method_hooks(agent_cls):
    """Idempotently wrap every ``_ai_tool_*`` method on the merged
    ai.agent registry class. Safe to call once per run: the marker
    attribute short-circuits repeats; a registry rebuild produces a
    fresh class and simply gets wrapped again."""
    wrapped = 0
    for name in dir(agent_cls):
        if not name.startswith(_METHOD_PREFIX):
            continue
        fn = getattr(agent_cls, name, None)
        if not callable(fn):
            continue
        if getattr(fn, "_daadit_schedule_method_wrapped", False):
            continue
        try:
            setattr(agent_cls, name, _make_method_wrapper(name, fn))
            wrapped += 1
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_agent_schedule.run_capture: could not wrap "
                "%s on %s", name, agent_cls,
            )
    if wrapped:
        _logger.info(
            "daadit_ai_agent_schedule.run_capture: wrapped %d _ai_tool_* "
            "method(s) on %s for provider-agnostic capture",
            wrapped, agent_cls,
        )
    return wrapped


# ---------------------------------------------------------------------
# Chat turns
# ---------------------------------------------------------------------
#
# A scheduled run has always left an action log. A chat turn did not —
# and that is exactly where it went wrong on 01-08-2026: a routed answer
# reported five tasks "klaargezet in Odoo" against zero writes, and
# there was no record to check it against. The tool wrapper above
# already sees every call the chat makes; it simply was never armed.
#
# So we arm it around a top-level chat turn and store the result as a
# run with no schedule. Everything built for scheduled runs — the
# factual footer, the list of records actually touched, the
# claims_unverified banner — then applies to chat unchanged.
#
# Only turns that called at least one tool are stored. A plain question
# and answer touches nothing, and a record per chat message would bury
# the runs that matter.

class _ChatTurnObserver:
    """Bracket a top-level chat turn and file it as a run."""

    def begin(self, agent):
        # A scheduled run has already armed the buffer and owns it. Never
        # take a turn we did not open, or stop_capture below would steal
        # the scheduler's actions.
        if getattr(_state, "buffer", None) is not None:
            _state.chat_owned = False
            return
        _state.chat_owned = True
        _state.chat_started_at = datetime.utcnow()
        start_capture(test_mode=False)

    def end(self, agent, answer):
        if not getattr(_state, "chat_owned", False):
            return
        _state.chat_owned = False
        started = getattr(_state, "chat_started_at", None)
        actions = stop_capture()
        if not actions:
            return
        if agent is None or getattr(agent, "env", None) is None:
            return
        try:
            if isinstance(answer, (list, tuple)):
                findings = "\n\n".join(
                    str(part) for part in answer if part is not None
                ).strip()
            else:
                findings = str(answer or "").strip()
            now = datetime.utcnow()
            # Koppel deze vastgelegde beurt aan het live turn-id dat de
            # denkstappen over de bus droegen, zodat de vluchtige stappen
            # achteraf terugleesbaar zijn uit deze run (zelfde bron als de
            # audit). Best-effort: geen id gevonden, geen koppeling.
            turn_id = None
            try:
                from . import agent_steps
                turn_id = agent_steps.current_turn_id()
            except Exception:  # noqa: BLE001
                turn_id = None
            run = agent.env["daadit.ai.agent.schedule.run"].sudo().create({
                "agent_id": agent.id,
                "trigger": "chat",
                "state": "done",
                "start_date": started or now,
                "end_date": now,
                "user_id": agent.env.uid,
                "turn_uuid": turn_id or False,
            })
            run._record_actions(actions)
            run._record_delegations(take_delegations())
            run.write({
                "findings": (findings or "") + run._factual_footer(),
            })
        except Exception:  # noqa: BLE001
            # An audit record is never worth failing someone's chat over.
            _logger.exception(
                "daadit_ai_agent_schedule.run_capture: could not file a "
                "chat turn for agent %s", getattr(agent, "id", "?"),
            )


def install_turn_observer():
    """Point the shared dispatcher's turn hook at us, once."""
    if shared_dispatch.turn_hooks is not None:
        return False
    shared_dispatch.turn_hooks = _ChatTurnObserver()
    _logger.info(
        "daadit_ai_agent_schedule.run_capture: chat turns are now "
        "captured the same way scheduled runs are"
    )
    return True


def _usage_marks(env):
    """Highest usage-row id per provider usage model, as a start mark."""
    marks = {}
    for model_name in (
        "daadit_ai_mistral.usage", "daadit_ai_claude.usage",
        "daadit_ai_loes.usage",
    ):
        if model_name not in env:
            continue
        row = env[model_name].sudo().search([], order="id desc", limit=1)
        marks[model_name] = row.id or 0
    return marks


def _usage_since(env, marks, agent_id):
    """Tokens and cost the delegate itself used since ``marks``."""
    totals = {"prompt_tokens": 0, "completion_tokens": 0,
              "estimated_cost_usd": 0.0, "provider_usage": ""}
    for model_name, mark in (marks or {}).items():
        if model_name not in env:
            continue
        rows = env[model_name].sudo().search([
            ("id", ">", mark), ("agent_id", "=", agent_id),
        ])
        if not rows:
            continue
        totals["provider_usage"] = model_name
        for row in rows:
            totals["prompt_tokens"] += row.prompt_tokens or 0
            totals["completion_tokens"] += row.completion_tokens or 0
            totals["estimated_cost_usd"] += row.estimated_cost_usd or 0.0
    return totals


class _DelegationRecorder:
    """Note every delegated sub-run of the run being captured.

    Entries live on the capture state and are filed by the run with
    :func:`take_delegations`. Nested delegations point at their parent
    entry, so the run keeps the whole chain.
    """

    def begin(self, caller, target, question, depth):
        if getattr(_state, "buffer", None) is None:
            return None
        entries = getattr(_state, "delegations", None)
        if entries is None:
            entries = _state.delegations = []
        stack = list(getattr(_state, "delegation_stack", None) or [])
        try:
            marks = _usage_marks(target.env)
        except Exception:  # noqa: BLE001
            marks = {}
        entries.append({
            "parent_index": stack[-1] if stack else None,
            "depth": depth,
            "caller_agent_id": caller.id,
            "agent_id": target.id,
            "question": question or "",
            "start_date": datetime.utcnow(),
            "_env": target.env,
            "_marks": marks,
        })
        token = len(entries) - 1
        _state.delegation_stack = stack + [token]
        return token

    def end(self, token, result):
        entries = getattr(_state, "delegations", None) or []
        if token is None or token >= len(entries):
            return
        stack = list(getattr(_state, "delegation_stack", None) or [])
        if stack and stack[-1] == token:
            stack.pop()
        _state.delegation_stack = stack
        entry = entries[token]
        result = result if isinstance(result, dict) else {}
        entry["end_date"] = datetime.utcnow()
        entry["state"] = "answered" if result.get("ok") else "failed"
        entry["answer"] = result.get("answer") or ""
        entry["error"] = "" if result.get("ok") else (
            result.get("error") or "Geen antwoord"
        )
        entry["provider"] = result.get("provider") or ""
        entry["tool_calls"] = int(result.get("tool_calls_made") or 0)
        entry["write_calls"] = int(result.get("write_actions_made") or 0)
        try:
            entry.update(_usage_since(
                entry["_env"], entry["_marks"], entry["agent_id"],
            ))
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_agent_schedule.run_capture: could not read the "
                "usage of a delegation to agent %s", entry["agent_id"],
            )


def take_delegations():
    """Return the delegations noted since :func:`start_capture`, once."""
    entries = getattr(_state, "delegations", None) or []
    _state.delegations = []
    _state.delegation_stack = []
    for entry in entries:
        entry.pop("_env", None)
        entry.pop("_marks", None)
    return entries


def install_delegation_observer():
    """Point the shared dispatcher's delegation hook at us, once."""
    if shared_dispatch.delegation_hooks is not None:
        return False
    shared_dispatch.delegation_hooks = _DelegationRecorder()
    return True
