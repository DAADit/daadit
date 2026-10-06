# -*- coding: utf-8 -*-
"""Post-install / post-upgrade hook: seed ``ai.agent.daadit_office``.

Runs on both a fresh install (via ``post_init_hook``) and an upgrade of
an already-installed database (via
``migrations/19.0.2.0.0/post-migrate.py``) — ``post_init_hook`` alone
only fires on genuinely new installs, and this module was already
installed in production before the Office field existed, so relying on
``post_init_hook`` alone would silently skip the seeding for everyone
who upgrades rather than freshly installs. See
``daadit_teams_meeting/hooks.py`` for the same pattern used earlier in
this codebase.

Matched by name rather than xmlid: these ``ai.agent`` records were
created interactively (chat / RPC), not via this module's XML data, so
they have no stable external id to seed against. Idempotent — only sets
``daadit_office`` where it's still empty, so re-running (e.g. on a
later upgrade) never overwrites a value the team has since edited.
"""

_OFFICE_BY_NAME = {
    # Delivery Office — klantgerichte levering: implementatie,
    # helpdesk & support, service & doorontwikkeling.
    "Helpdesk Agent": "delivery",
    "Helpdesk SLA Agent": "delivery",  # tolerate the older name too
    "Project Agent": "delivery",
    "Product Agent": "delivery",
    "AI: Helpdesk AI context generator": "delivery",
    # Technical Office — technische ruggengraat (generic/system agent).
    "Odoo Agent": "technical",
    # Executive Office — strategie, commercie en zichtbaarheid.
    "Ask AI": "executive",
    "Livechat AI Agent": "executive",
    "Sales Agent": "executive",
    "Marketing Agent": "executive",
}


def _seed_agent_offices(env):
    agents = env["ai.agent"].sudo().search([("name", "in", list(_OFFICE_BY_NAME.keys()))])
    for agent in agents:
        office = _OFFICE_BY_NAME.get(agent.name)
        if office and not agent.daadit_office:
            agent.daadit_office = office
