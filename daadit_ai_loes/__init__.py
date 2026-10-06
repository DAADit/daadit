# -*- coding: utf-8 -*-
import logging

_logger = logging.getLogger(__name__)
_logger.info("daadit_ai_loes: __init__ loading (v19.0.6.0.0)")

# Services first so diagnostics installs its UserError tap before any model
# code is imported.
from . import services
from . import models

_logger.info("daadit_ai_loes: __init__ loaded")


# ============================================================================
# Lifecycle hooks (registered from __manifest__.py)
# ============================================================================
#
# These exist purely to keep the database from getting stuck when
# ``ai_agent.llm_model`` holds a Loes value while stock ``ai`` module's
# data files are reloaded by Odoo.sh's ``--update=all`` build. Stock's
# reload triggers ``ai_agent.write()`` which calls stock's
# ``_get_provider()``. At that phase our ``_inherit`` extensions are NOT
# yet merged into the ``ai.agent`` class (because we load AFTER ``ai``
# in dependency order), so stock's ``get_provider`` runs unwrapped and
# raises ``UserError("No provider found for the selected model")`` — the
# whole registry init fails.
#
# We can't fix that ordering from inside the module, but we *can* make
# sure the DB doesn't carry a Loes value into a fresh registry build:
#
#   * ``uninstall_hook`` — when the user explicitly uninstalls our
#     module, reset every Loes ``llm_model`` to ``gpt-4o`` and every
#     ``loes-embed`` to ``text-embedding-3-small`` so the next stock
#     reload is clean.
#
#   * ``pre_init_hook`` — runs on a FRESH install of our module. If the
#     DB already has stale Loes values from a previous install/
#     uninstall cycle, scrub them before our module's data files load.
#
# Neither hook helps the user who's already wedged — that case requires
# the SQL fix in the README's "Recovery" section to be run by hand.

_RESET_LLM_SQL = """
UPDATE ai_agent
   SET llm_model = 'gpt-4o'
 WHERE llm_model LIKE 'loes%%'
"""

_RESET_EMBED_SQL = """
UPDATE ai_embedding
   SET embedding_model = 'text-embedding-3-small'
 WHERE embedding_model = 'loes-embed'
"""


def _reset_loes_values(env):
    """Run the SQL fix-ups on whichever tables exist."""
    cr = env.cr
    cr.execute("""
        SELECT table_name FROM information_schema.tables
         WHERE table_name IN ('ai_agent', 'ai_embedding')
    """)
    tables = {row[0] for row in cr.fetchall()}
    if 'ai_agent' in tables:
        cr.execute(_RESET_LLM_SQL)
        _logger.info(
            "daadit_ai_loes: reset %d ai_agent.llm_model values to gpt-4o",
            cr.rowcount,
        )
    if 'ai_embedding' in tables:
        cr.execute(_RESET_EMBED_SQL)
        _logger.info(
            "daadit_ai_loes: reset %d ai_embedding.embedding_model values "
            "to text-embedding-3-small",
            cr.rowcount,
        )


def pre_init_hook(env):
    """Called immediately before our module's data files load on FRESH
    install. Scrubs any stray Loes values left in the DB by an earlier
    install/uninstall cycle."""
    _logger.info(
        "daadit_ai_loes.pre_init_hook: scrubbing stale Loes values"
    )
    _reset_loes_values(env)


def uninstall_hook(env):
    """Called when the module is being uninstalled. Reset every Loes
    ``llm_model`` / ``embedding_model`` so the DB doesn't carry an
    invalid-after-uninstall value into the next ``--update=all`` build.
    """
    _logger.info(
        "daadit_ai_loes.uninstall_hook: resetting Loes values "
        "across ai_agent / ai_embedding"
    )
    _reset_loes_values(env)
