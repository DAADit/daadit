# -*- coding: utf-8 -*-
{
    "name": "DAADit Agent Dashboard — targets vs realisation",
    "summary": "A per-agent KPI board that stores the team's targets and "
               "computes realisation live from CRM, Sales, Helpdesk and "
               "Project.",
    "description": """
DAADit Agent Dashboard
======================
A live, native dashboard that mirrors the DAADit AI agents (Sales,
Marketing, Helpdesk, Project, Product). Each KPI record holds a **target**
(editable in the UI, so the team owns its goals) and computes its
**realisation** on the fly from the operational models:

* **Sales** — omzet YTD vs jaardoel, gewonnen deals, actieve pijplijn,
  gemiddelde dealwaarde.
* **Marketing** — nieuwe leads (YTD + deze maand), lead→klant-conversie,
  bron-attributie.
* **Helpdesk** — niet-toegewezen tickets, open tickets, werklast-spreiding.
* **Project** — actieve projecten, open taken, urgente taken.
* **Product** — geïnstalleerde basis (orders + omzet) als context.

Design
------
* ``realised`` / ``progress`` / ``status`` are **non-stored computed**
  fields — every dashboard load reflects the current database state.
* Each metric query is **fail-open**: a broken or missing source model
  yields 0 and a neutral status, never a traceback, so one metric can
  never take the board down.
* Targets are seeded once (``noupdate="1"``) and then owned by the team:
  any internal user edits them straight from the board (read+write, no
  create/delete) → geen code-deploy nodig.
* Status is **pace-aware** where it matters: a yearly revenue target at
  half-year is judged against ~50% expected, not 100%.

The primary view is a custom OWL board (``static/src/js/
agent_dashboard_action.js``) styled to match the team's preferred
mock-up, living as a menu entry inside the native **Dashboards** app
(``spreadsheet_dashboard``) — a sibling of that app's own "Dashboards"
and "Configuration" entries, since a spreadsheet-format dashboard can't
render arbitrary HTML/CSS. Target editing stays available via the
classic kanban/list/form under "Doelen beheren" in the same app.

Agent Office grouping
----------------------
Adds a ``daadit_office`` field to ``ai.agent`` and groups the native
Agents kanban (AI app) by it, so agents no longer show as one
undifferentiated pile. The four Offices mirror the org structure
documented in Knowledge ("Interne bedrijfsprocessen"): Delivery,
Technical, Operations, Executive. The seed in ``hooks.py`` categorizes
the agents that existed when this shipped (matched by name), run via
``post_init_hook`` on a fresh install *and* via
``migrations/19.0.2.0.0/post-migrate.py`` on an upgrade of an
already-installed database (this module was already live in
production, so relying on ``post_init_hook`` alone would have skipped
the seeding entirely). Idempotent — never overwrites a value the team
has since edited. A plain, team-owned field afterwards, editable on the
agent form.

Active-schedule ribbon
-----------------------
A green "Active" ribbon (top-right, same spot as the stock "Archived"
ribbon) on the kanban card and form, shown whenever the agent has at
least one active ``daadit.ai.agent.schedule``.
""",
    "version": "19.0.2.2.0",
    "category": "Productivity/Discuss",
    "author": "DAADit",
    "website": "https://daadit.group",
    "license": "LGPL-3",
    "depends": [
        "base",
        "sale",
        "crm",
        "project",
        "ai",
        "ai_app",
        "spreadsheet_dashboard",
        "daadit_ai_agent_schedule",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/agent_kpi_views.xml",
        "views/ai_agent_views.xml",
        "views/agent_dashboard_client_action.xml",
        "data/agent_kpi_data.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "daadit_agent_dashboard/static/src/scss/kpi_board.scss",
            "daadit_agent_dashboard/static/src/scss/agent_dashboard_action.scss",
            "daadit_agent_dashboard/static/src/js/agent_dashboard_action.js",
            "daadit_agent_dashboard/static/src/xml/agent_dashboard_action.xml",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}
