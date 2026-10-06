# -*- coding: utf-8 -*-
"""Provider-agnostic bridge to the installed DAADit LLM providers.

Every DAADit provider add-on follows the same shape:

    daadit_ai_<code>/
        services/tool_dispatch.py    -> run_tool_call(agent, tool_call)
                                        current_agent  (threading.local)
                                        router_state   (threading.local)
        services/llm_api_patch.py    -> patch_llm_api_service()
        services/<name>_client.py    -> SUPPORTED_MODELS, is_<x>_model()
        models/ai_agent.py           -> _get_provider() -> "<code>"
        models/<name>_usage.py       -> _name = "daadit_ai_<code>.usage"

and reads a batch/scheduled marker from the env context under
``daadit_<code>_batch`` (Fase-0 API-key split).

The scheduler used to import ``daadit_ai_mistral`` by name, which meant
Mistral got the full treatment (dispatcher-level action capture, hard
run deadline, exhaustion detection, token/cost logging) while every
other provider silently lost those features — or worse, had the agent
threadlocal set on the *wrong* module.

This bridge resolves whichever add-on serves the agent's provider at
run time, so a new provider (loes.ai, Claude, whatever comes next)
works the moment it is installed — no change here needed as long as it
keeps the family conventions. Providers that have no DAADit add-on at
all (stock OpenAI / Gemini through Odoo's own LLMApiService) get a
NullBridge: the run still executes and is still logged, just via the
method-level capture hooks in ``run_capture``.
"""
import importlib
import logging

_logger = logging.getLogger(__name__)

# Add-ons that live in the daadit_ai_* namespace but are not LLM
# providers — skip them during discovery.
_NON_PROVIDER_MODULES = frozenset({
    "daadit_ai_agent_schedule",
    "daadit_ai_agentic_system",
    "daadit_ai_router",
    "daadit_ai_customer_memory",
})


def _import_service(module_name, service):
    """Import ``odoo.addons.<module_name>.services.<service>`` or None."""
    try:
        return importlib.import_module(
            "odoo.addons.%s.services.%s" % (module_name, service)
        )
    except Exception:  # noqa: BLE001 — ImportError and anything the
        # provider module raises at import time must not break a run.
        return None


def _looks_like_dispatcher(mod):
    return bool(
        mod is not None
        and callable(getattr(mod, "run_tool_call", None))
        and getattr(mod, "current_agent", None) is not None
    )


def _module_handles_model(module_name, model_name):
    """True when this add-on's client advertises ``model_name``.

    Fallback identification for add-ons whose technical name does not
    match the provider code (e.g. a future ``daadit_ai_azure`` serving
    provider code ``azure_openai``). Looks for the family convention
    ``services/*_client.py`` exposing ``SUPPORTED_MODELS`` and/or an
    ``is_*_model()`` predicate.
    """
    if not model_name:
        return False
    suffix = module_name[len("daadit_ai_"):] if module_name.startswith(
        "daadit_ai_"
    ) else module_name
    for candidate in ("%s_client" % suffix, "client"):
        client = _import_service(module_name, candidate)
        if client is None:
            continue
        supported = getattr(client, "SUPPORTED_MODELS", None)
        if supported and model_name in tuple(supported):
            return True
        for attr in dir(client):
            if attr.startswith("is_") and attr.endswith("_model"):
                fn = getattr(client, attr)
                if not callable(fn) or "embedding" in attr:
                    continue
                try:
                    if fn(model_name):
                        return True
                except Exception:  # noqa: BLE001
                    continue
    return False


def _installed_provider_modules(env):
    """Installed ``daadit_ai_*`` add-ons that could be providers."""
    try:
        mods = env["ir.module.module"].sudo().search([
            ("name", "=like", "daadit_ai_%"),
            ("state", "=", "installed"),
        ])
        return [
            m.name for m in mods if m.name not in _NON_PROVIDER_MODULES
        ]
    except Exception:  # noqa: BLE001
        return []


def installed_service(env, module_name, service):
    """``<module_name>.services.<service>`` if that add-on is installed."""
    if module_name not in _installed_provider_modules(env):
        return None
    return _import_service(module_name, service)


def installed_dispatchers(env):
    """Every installed provider's ``tool_dispatch`` module.

    A provider may hand a turn to another one when it is down (Mistral
    → Claude); the run only sees those tool calls when that provider's
    dispatcher is wrapped too.
    """
    found = []
    for module_name in _installed_provider_modules(env):
        dispatch = _import_service(module_name, "tool_dispatch")
        if _looks_like_dispatcher(dispatch):
            found.append(dispatch)
    return found


class ProviderBridge:
    """Thin, defensive wrapper around one provider add-on's services."""

    def __init__(self, code, module_name=None, tool_dispatch=None,
                 llm_api_patch=None):
        self.code = code or ""
        self.module_name = module_name or ""
        self.tool_dispatch = tool_dispatch
        self.llm_api_patch = llm_api_patch

    # --- capabilities -------------------------------------------------
    @property
    def has_dispatcher(self):
        """True when the provider runs tools through its own dispatcher.

        Dispatcher-level capture is richer than method-level capture: it
        also sees hallucinated tool names and admin-policy denials,
        which never reach an ``_ai_tool_*`` method.
        """
        return _looks_like_dispatcher(self.tool_dispatch)

    @property
    def usage_model(self):
        """``daadit_ai_<code>.usage`` model name (existence unchecked)."""
        if not self.module_name:
            return ""
        return "%s.usage" % self.module_name

    def batch_context(self):
        """Context flags marking this run as scheduled/batch traffic.

        Sets the provider-specific key (``daadit_<code>_batch``) plus a
        generic ``daadit_ai_batch`` that future providers can read.
        Extra keys are harmless: each provider only reads its own.
        """
        ctx = {"daadit_ai_batch": True}
        if self.code:
            ctx["daadit_%s_batch" % self.code] = True
        return ctx

    # --- lifecycle ----------------------------------------------------
    def patch_llm_service(self):
        """Ask the provider to (re)install its LLMApiService patch."""
        fn = getattr(self.llm_api_patch, "patch_llm_api_service", None)
        if callable(fn):
            try:
                fn()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "daadit_ai_agent_schedule: %s.patch_llm_api_service "
                    "raised — continuing", self.module_name,
                )

    def annotate_tools(self, names):
        """Ask the provider to turn tool-name strings into full tool
        defs (rich JSON-Schema parameters). Returns [] when the provider
        cannot do it, so the caller can fall back to passing names."""
        fn = getattr(self.tool_dispatch, "annotate_tools", None)
        if not callable(fn) or not names:
            return []
        try:
            return list(fn(list(names)) or [])
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_ai_agent_schedule: %s.annotate_tools raised — "
                "falling back to plain tool names", self.module_name,
            )
            return []

    def set_current_agent(self, agent):
        if self.tool_dispatch is None:
            return
        try:
            self.tool_dispatch.current_agent.record = agent
        except Exception:  # noqa: BLE001
            pass

    def clear_current_agent(self):
        self.set_current_agent(None)

    def _router_state(self):
        return getattr(self.tool_dispatch, "router_state", None)

    def set_deadline(self, monotonic_deadline):
        state = self._router_state()
        if state is None:
            return False
        try:
            state.run_deadline_monotonic = monotonic_deadline
            return True
        except Exception:  # noqa: BLE001
            return False

    def clear_deadline(self):
        state = self._router_state()
        if state is None:
            return
        try:
            state.run_deadline_monotonic = None
        except Exception:  # noqa: BLE001
            pass

    def reset_exhaustion(self):
        state = self._router_state()
        if state is None:
            return
        try:
            state.top_level_exhausted = False
            state.exhaustion_reason = None
        except Exception:  # noqa: BLE001
            pass

    def read_exhaustion(self):
        """Return ``(exhausted, reason)`` — ``(False, None)`` when the
        provider does not report exhaustion."""
        state = self._router_state()
        if state is None:
            return False, None
        try:
            return (
                bool(getattr(state, "top_level_exhausted", False)),
                getattr(state, "exhaustion_reason", None),
            )
        except Exception:  # noqa: BLE001
            return False, None

    def reset_fallback(self):
        state = self._router_state()
        if state is None:
            return
        try:
            state.fallback_provider = None
            state.fallback_model = None
            state.fallback_reason = None
        except Exception:  # noqa: BLE001
            pass

    def read_fallback(self):
        """Return ``(provider, model, reason)`` when a turn of this run
        was answered by another provider, else ``(None, None, None)``."""
        state = self._router_state()
        if state is None:
            return None, None, None
        return (
            getattr(state, "fallback_provider", None),
            getattr(state, "fallback_model", None),
            getattr(state, "fallback_reason", None),
        )


def resolve(env, provider_code, model_name=None):
    """Return a :class:`ProviderBridge` for ``provider_code``.

    Resolution order:
      1. Name convention ``daadit_ai_<provider_code>``.
      2. Any other installed ``daadit_ai_*`` add-on whose client
         advertises ``model_name`` (covers add-ons whose technical name
         differs from the provider code).
      3. NullBridge — no DAADit provider add-on serves this provider
         (stock OpenAI / Gemini). The run still executes through
         Odoo's own LLMApiService and is logged via method-level hooks.
    """
    code = (provider_code or "").strip()
    tried = []

    if code:
        convention = "daadit_ai_%s" % code
        tried.append(convention)
        dispatch = _import_service(convention, "tool_dispatch")
        if _looks_like_dispatcher(dispatch):
            return ProviderBridge(
                code, convention, dispatch,
                _import_service(convention, "llm_api_patch"),
            )

    for module_name in _installed_provider_modules(env):
        if module_name in tried:
            continue
        if not _module_handles_model(module_name, model_name):
            continue
        dispatch = _import_service(module_name, "tool_dispatch")
        if _looks_like_dispatcher(dispatch):
            _logger.info(
                "daadit_ai_agent_schedule: provider %r resolved to "
                "add-on %s via model %r (name convention did not "
                "match)", code, module_name, model_name,
            )
            return ProviderBridge(
                code, module_name, dispatch,
                _import_service(module_name, "llm_api_patch"),
            )

    _logger.info(
        "daadit_ai_agent_schedule: no DAADit provider add-on found for "
        "provider %r (model %r) — running through stock LLMApiService "
        "with method-level action capture", code, model_name,
    )
    return ProviderBridge(code)
