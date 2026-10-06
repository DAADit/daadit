# -*- coding: utf-8 -*-
"""Read-side aggregator for the Agent-activiteit board.

One abstract model, one RPC entry point (:meth:`get_payload`). All data
comes from records other modules already write — schedules, runs,
mentions, usage rows. Every sub-query is fail-open: a missing optional
model (mentions, usage) or a broken metric yields an empty list or 0,
never a traceback, so one source can never take the board down.
"""
import logging
from datetime import timedelta

import pytz

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Feed/list caps — the board is a monitor, not an archive.
FEED_LIMIT = 40
TODAY_LIMIT = 80
UPCOMING_DAYS = 7


class DaaditAgentActivity(models.AbstractModel):
    _name = "daadit.agent.activity"
    _description = "Agent-activiteit dashboard (aggregatie)"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @api.model
    def _user_day_start_utc(self):
        """Start of 'today' in the user's timezone, as naive UTC."""
        tz_name = self.env.user.tz or "UTC"
        try:
            tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            tz = pytz.utc
        now_local = fields.Datetime.now().replace(tzinfo=pytz.utc).astimezone(tz)
        day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        return day_start_local.astimezone(pytz.utc).replace(tzinfo=None)

    # DAADit rekent in euro's; de AI-providers factureren in dollars en
    # ``estimated_cost_usd`` bewaart daarom de ruwe USD-waarde als bron.
    # Omrekenen gaat NIET via res.currency: de USD-koers in deze database
    # staat op 1,00 (er is nooit een koersbron ingesteld), dus die route
    # zou een dollar gelijkstellen aan een euro. Daarom een expliciete,
    # instelbare koers — één parameter om bij te werken, en zichtbaar in
    # het dashboard zodat niemand hoeft te raden welke koers is gebruikt.
    _FX_PARAM = "daadit_ai_mistral.usd_eur_rate"
    _FX_DEFAULT = 0.92

    def _fx_rate(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(
            self._FX_PARAM
        )
        try:
            rate = float(raw)
        except (TypeError, ValueError):
            return self._FX_DEFAULT
        # Een koers van 0 of negatief is een invoerfout, geen instelling.
        return rate if rate > 0 else self._FX_DEFAULT

    def _eur(self, usd_amount):
        return (usd_amount or 0.0) * self._fx_rate()

    @api.model
    def _dt(self, value):
        """Naive UTC datetime → ISO string with explicit Z suffix."""
        return value.isoformat() + "Z" if value else False

    @api.model
    def _agent_meta(self):
        """id → {name, dept, dept_id} for every active agent.

        'Afdeling' is de directe manager uit het organogram
        (``x_manager_agent_id``, een handmatig veld dat alleen op prod
        bestaat). Zonder dat veld valt alles onder één afdeling.
        """
        Agent = self.env["ai.agent"].sudo()
        has_manager = "x_manager_agent_id" in Agent._fields
        meta = {}
        for agent in Agent.search([]):
            manager = agent.x_manager_agent_id if has_manager else False
            meta[agent.id] = {
                "id": agent.id,
                "name": agent.name,
                "dept_id": manager.id if manager else 0,
                "dept": ("Team %s" % manager.name) if manager else "Directie",
            }
        return meta

    # ------------------------------------------------------------------
    # Optional sources (models from modules that may not be installed)
    # ------------------------------------------------------------------
    @api.model
    def _mention_stats(self):
        """(pending per agent, feed rows) from the chatter-mention queue."""
        pending, feed = {}, []
        Mention = self.env.get("daadit.agent.mention")
        if Mention is None:
            return pending, feed
        try:
            Mention = Mention.sudo()
            for row in Mention.read_group(
                [("state", "=", "pending")], ["id:count"], ["agent_id"],
            ):
                agent = row.get("agent_id")
                if agent:
                    pending[agent[0]] = row.get("agent_id_count", 0)
            day_start = self._user_day_start_utc()
            for m in Mention.search(
                [("write_date", ">=", day_start), ("state", "!=", "pending")],
                order="write_date desc", limit=15,
            ):
                feed.append({
                    "ts": self._dt(m.write_date),
                    "agent_id": m.agent_id.id,
                    "agent": m.agent_id.name,
                    "kind": "mention",
                    "level": "success" if m.state == "done" else (
                        "error" if m.state == "failed" else "info"
                    ),
                    "text": "@-vermelding %s (%s #%s)" % (
                        {"done": "beantwoord", "failed": "mislukt",
                         "skipped": "overgeslagen"}.get(m.state, m.state),
                        m.res_model or "?", m.res_id or "?",
                    ),
                })
        except Exception:  # noqa: BLE001
            _logger.exception("agent_activity: mention stats failed")
        return pending, feed

    @api.model
    def _chat_calls_today(self):
        """agent_id → # chat-calls vandaag, over alle provider-usagemodellen."""
        counts = {}
        day_start = self._user_day_start_utc()
        for model_name in (
            "daadit_ai_mistral.usage",
            "daadit_ai_claude.usage",
            "daadit_ai_loes.usage",
        ):
            Usage = self.env.get(model_name)
            if Usage is None:
                continue
            try:
                Usage = Usage.sudo()
                if "agent_id" not in Usage._fields:
                    continue
                domain = [("create_date", ">=", day_start)]
                if "kind" in Usage._fields:
                    domain.append(("kind", "=", "chat"))
                for row in Usage.read_group(domain, ["id:count"], ["agent_id"]):
                    agent = row.get("agent_id")
                    if agent:
                        counts[agent[0]] = (
                            counts.get(agent[0], 0)
                            + row.get("agent_id_count", 0)
                        )
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "agent_activity: usage stats failed for %s", model_name,
                )
        return counts

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    @api.model
    def get_payload(self):
        meta = self._agent_meta()
        day_start = self._user_day_start_utc()
        now = fields.Datetime.now()
        Run = self.env["daadit.ai.agent.schedule.run"].sudo()
        Schedule = self.env["daadit.ai.agent.schedule"].sudo()

        # --- Nu bezig -------------------------------------------------
        running = [{
            "run_id": r.id,
            "agent_id": r.agent_id.id,
            "agent": r.agent_id.name,
            "schedule": r.schedule_id.name or "(handmatig)",
            "trigger": r.trigger,
            "started": self._dt(r.start_date),
            "elapsed_s": int((now - r.start_date).total_seconds())
            if r.start_date else 0,
        } for r in Run.search([("state", "=", "running")], order="start_date")]

        # --- Vandaag gedaan ------------------------------------------
        today, feed = [], []
        for r in Run.search(
            [("start_date", ">=", day_start), ("state", "!=", "running")],
            order="start_date desc", limit=TODAY_LIMIT,
        ):
            today.append({
                "run_id": r.id,
                "agent_id": r.agent_id.id,
                "agent": r.agent_id.name,
                "schedule": r.schedule_id.name or "(handmatig)",
                "state": r.state,
                "started": self._dt(r.start_date),
                "duration": round(r.duration or 0.0, 1),
                "tokens": r.total_tokens or 0,
                "cost": round(self._eur(r.estimated_cost_usd), 4),
                "actions": r.action_count or 0,
                "write_actions": r.write_action_count or 0,
                "error_short": (r.error or "")[:140],
            })
            feed.append({
                "ts": self._dt(r.end_date or r.start_date),
                "agent_id": r.agent_id.id,
                "agent": r.agent_id.name,
                "kind": "run",
                "level": "success" if r.state == "done" else "error",
                "text": "%s — %s" % (
                    r.schedule_id.name or "handmatige run",
                    "afgerond" if r.state == "done" else "mislukt",
                ),
                "run_id": r.id,
            })
        for r in running:
            feed.append({
                "ts": r["started"],
                "agent_id": r["agent_id"],
                "agent": r["agent"],
                "kind": "run",
                "level": "info",
                "text": "%s — gestart" % r["schedule"],
                "run_id": r["run_id"],
            })

        # --- Gepland (komende week) ----------------------------------
        horizon = now + timedelta(days=UPCOMING_DAYS)
        upcoming = [{
            "schedule_id": s.id,
            "agent_id": s.agent_id.id,
            "agent": s.agent_id.name,
            "name": s.name,
            "nextcall": self._dt(s.nextcall),
            "interval": "%s %s" % (s.interval_number, s.interval_type),
            "overdue": bool(s.nextcall and s.nextcall < now),
        } for s in Schedule.search(
            [("active", "=", True), ("nextcall", "!=", False),
             ("nextcall", "<=", horizon)],
            order="nextcall",
        )]

        # --- Per-agent kaarten ---------------------------------------
        mention_pending, mention_feed = self._mention_stats()
        feed.extend(mention_feed)
        chat_calls = self._chat_calls_today()
        last_by_agent = {}
        for r in today:
            last_by_agent.setdefault(r["agent_id"], r)  # newest first
        running_agents = {r["agent_id"] for r in running}

        agents = []
        for agent_id, m in sorted(meta.items(), key=lambda kv: kv[1]["name"]):
            runs_today = [r for r in today if r["agent_id"] == agent_id]
            last = last_by_agent.get(agent_id)
            agents.append(dict(m, **{
                "running": agent_id in running_agents,
                "last_state": "running" if agent_id in running_agents
                else (last and last["state"]) or "",
                "runs_today": len(runs_today),
                "errors_today": sum(
                    1 for r in runs_today if r["state"] == "error"
                ),
                "chat_calls_today": chat_calls.get(agent_id, 0),
                "mentions_pending": mention_pending.get(agent_id, 0),
            }))

        feed.sort(key=lambda e: e["ts"] or "", reverse=True)
        depts = sorted(
            {(m["dept_id"], m["dept"]) for m in meta.values()},
            key=lambda d: d[1],
        )
        return {
            "now": self._dt(now),
            "agents": agents,
            "depts": [{"id": d[0], "name": d[1]} for d in depts],
            "running": running,
            "today": today,
            "upcoming": upcoming,
            "feed": feed[:FEED_LIMIT],
            "stats": {
                "runs_today": len(today),
                "ok_today": sum(1 for r in today if r["state"] == "done"),
                "error_today": sum(1 for r in today if r["state"] == "error"),
                "tokens_today": sum(r["tokens"] for r in today),
                "cost_today": round(sum(r["cost"] for r in today), 2),
                "mentions_pending": sum(mention_pending.values()),
                "chat_calls_today": sum(chat_calls.values()),
                "currency": "EUR",
                "fx_rate": round(self._fx_rate(), 4),
            },
        }
