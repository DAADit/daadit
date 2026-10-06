# -*- coding: utf-8 -*-
from . import services

# Instellingen die de providers elk onder hun eigen naam hadden. De
# eerste provider in deze volgorde met een waarde wint.
_LEGACY_PARAMS = {
    "daadit_ai_agentic_system.max_tool_result_chars": (
        "daadit_ai_mistral.max_tool_result_chars",
        "daadit_ai_loes.max_tool_result_chars",
    ),
    "daadit_ai_agentic_system.log_tool_results": (
        "daadit_ai_mistral.log_tool_results",
        "daadit_ai_claude.log_tool_results",
        "daadit_ai_loes.log_tool_results",
    ),
}


def post_init_hook(env):
    """Neem de bestaande providerinstellingen over."""
    icp = env["ir.config_parameter"].sudo()
    for key, legacy_keys in _LEGACY_PARAMS.items():
        if icp.get_param(key):
            continue
        for legacy in legacy_keys:
            value = icp.get_param(legacy)
            if value:
                icp.set_param(key, value)
                break
