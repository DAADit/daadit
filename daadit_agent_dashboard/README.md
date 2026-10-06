# DAADit Agent Dashboard

A live, native per-agent KPI board. Each `daadit.agent.kpi` row stores a
**target** (team-owned, editable in the UI) and computes its
**realisation** on the fly from the operational models — no snapshot, no
cron. Open it under **Dashboards ▸ Agent Dashboard**; panels are grouped
by agent. The classic kanban/list/form for editing targets lives at
**Dashboards ▸ Doelen beheren** (also reachable via the "Doelen beheren"
button on the board itself).

## Two views, one data source

- **Agent Dashboard** (`static/src/js/agent_dashboard_action.js`) — the
  primary, polished OWL board. One RPC (`get_dashboard_payload`) returns
  every KPI plus a few trend charts (sales/leads by month, helpdesk
  workload); the client groups and renders them.
- **Doelen beheren** (`views/agent_kpi_views.xml`) — the classic
  kanban/list/form on `daadit.agent.kpi` itself, for editing targets.

Both live inside the native **Dashboards** app (`spreadsheet_dashboard`),
as siblings of that app's own "Dashboards" and "Configuration" entries —
not as `spreadsheet.dashboard` records, since that format can't render
arbitrary HTML/CSS.

## Agents & metrics

| Agent | KPIs |
|-------|------|
| **Sales** | Omzet YTD vs jaardoel · gewonnen deals · actieve pijplijn · gem. dealwaarde |
| **Marketing** | Nieuwe leads (YTD + maand) · lead→klant-conversie · bron-attributie |
| **Helpdesk** | Niet-toegewezen · open tickets · werklast-spreiding |
| **Project** | Actieve projecten · open taken · urgente taken |
| **Product** | Bevestigde orders + omzet (geïnstalleerde basis) |

## Design notes

- `realised` / `progress` / `status` are **non-stored computed** — every
  load reflects the current DB state.
- Each metric query is **fail-open** (`_safe`): a missing model or renamed
  field yields `0` and a neutral card, never a traceback.
- Status is **pace-aware** where set: a yearly target at mid-year is
  judged against ~50% expected.
- Queries run `sudo()` so any employee who sees the menu sees the
  aggregates, without direct read access to every source record.
- Targets seed once (`noupdate="1"`) and are then owned by the team.

## Adding a metric

1. Add `metric_key → (label, "_m_<name>")` to `_METRICS` in
   `models/agent_kpi.py`.
2. Implement `_m_<name>(self)` returning a float (wrapped by `_safe`).
3. Seed a `daadit.agent.kpi` record in `data/agent_kpi_data.xml`.

## Agent Office grouping

The native **Agents** board (AI app) showed every `ai.agent` in one
undifferentiated pile. `models/ai_agent.py` adds a `daadit_office`
Selection field (Delivery / Technical / Operations / Executive — the
four Offices from Knowledge → *Interne bedrijfsprocessen*), and
`views/ai_agent_views.xml` groups the kanban by it by default (plus
exposes it on the form and as a search filter).

`hooks._seed_agent_offices` seeds the Office for the agents that
existed when this shipped, matched by name. It runs via `post_init_hook`
on a fresh install *and* via `migrations/19.0.2.0.0/post-migrate.py` on
an upgrade of an already-installed database — `post_init_hook` alone
only fires on install, and this module was already live in production,
so the migration is what actually seeds it for the team's upgrade.
Idempotent (never overwrites an edited value). After that,
`daadit_office` is a plain field the team edits on the agent form like
any other — new agents start uncategorized until someone sets it.

## Active-schedule ribbon

Same file. `daadit_has_active_schedule` is a non-stored computed
Boolean, true when the agent has at least one active
`daadit.ai.agent.schedule` (computed via an explicit `active = True`
search, not by relying on `schedule_ids`' implicit active-filtering —
keeps the meaning unambiguous). Backs a green "Active" ribbon,
top-right, right next to the stock "Archived" ribbon on both the
kanban card and the form. Depends on `daadit_ai_agent_schedule` (the
module that adds `schedule_ids` to `ai.agent`).
