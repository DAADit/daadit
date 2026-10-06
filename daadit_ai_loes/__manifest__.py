# -*- coding: utf-8 -*-
{
    "name": "DAADit AI — Loes.ai Provider",
    "summary": "Add Loes.ai as an LLM provider for Odoo's built-in AI features",
    "description": """
DAADit AI — Loes.ai Provider
============================
Extends Odoo 19 Enterprise's ``ai`` module to support Loes.ai as an
additional LLM provider alongside OpenAI (ChatGPT), Google (Gemini) and
the DAADit Mistral / Claude providers, with feature parity across chat,
embeddings and tool calling. Built on the same blueprint (and with the
same restrictions) as ``daadit_ai_mistral``.

Features
--------
* **Chat completions** — adds Loes.ai models to ``ai.agent.llm_model``
  (``hyai/loes-large`` — World variant on Qwen3.5-27B — and
  ``hyai/loes-large-eu-eurollm-22b-2512``, the fully-EU alternative)
  and routes them to the OpenAI-compatible chat-completions endpoint of
  the HostYourAI EU-router (Loes is HostYourAI's EU-trained model). The
  model list is refreshed from ``GET /models`` by a daily cron and a
  manual "Refresh models" button; only Loes models (``hyai/loes*``)
  are imported from the router's catalogue.
* **Tool calling** — passes ``tools`` and ``tool_choice`` through to Loes.ai
  when an agent has ``topic_ids`` configured; auto-builds OpenAI-compatible
  tool defs from ``ai.topic.tool_ids`` (``ir.actions.server``).
* **Embeddings** — adds ``loes-embed`` to ``ai.embedding.embedding_model``
  and routes embedding generation to ``POST /v1/embeddings`` so Sources / RAG
  fully run on Loes.ai.
* **Settings UI** — adds a Loes.ai provider block to General Settings → AI
  next to the existing ChatGPT / Gemini blocks.

Restrictions (same hard gates as the Mistral / Claude providers)
----------------------------------------------------------------
* Base-URL allowlist (https-only, no IP literals, no userinfo) so a
  tampered URL cannot exfiltrate the API key.
* Per-agent allowed/blocked model lists + field-level PII blocklist,
  enforced in the tool-dispatch layer before Odoo RBAC.
* Hard per-agent read scopes (record domains AND-ed into every search).
* Usage logging with cost estimates and daily/monthly cost caps.

Notes
-----
The write-side AI tools (Assign User, Schedule Activity, Search
Knowledge, Ask Agent) are shipped by ``daadit_ai_mistral`` and shared
across the DAADit provider modules; this module's dispatcher resolves
them by action-name slug, so no duplicate server actions are created.
""",
    "version": "19.0.1.14.0",
    "category": "Productivity/Discuss",
    "author": "DAADit",
    "website": "https://daadit.group",
    "support": "https://daadit.group",
    "images": ["static/description/banner.png"],
    "license": "LGPL-3",
    "depends": [
        "base",
        "ai",
        "ai_app",
        # mail: deze module breidt mail.activity uit. Zonder de
        # afhankelijkheid hangt het ervan af of mail eerder in de
        # laadvolgorde staat, en die volgorde verschuift zodra er een
        # module bijkomt.
        "mail",
        "daadit_ai_agentic_system",
    ],
    "external_dependencies": {
        "python": ["requests"],
    },
    "data": [
        "security/ir.model.access.csv",
        "security/loes_usage_security.xml",
        "data/cost_cap_params.xml",
        "data/loes_models_seed.xml",
        "data/model_sync_cron.xml",
        "views/res_config_settings_views.xml",
        "views/loes_usage_views.xml",
        "views/ai_agent_views.xml",
        "views/loes_model_views.xml",
        "views/ai_agent_read_scope_views.xml",
    ],
    "pre_init_hook": "pre_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}
