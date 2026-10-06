# -*- coding: utf-8 -*-
"""Per-agent KPI board: stored targets, live-computed realisation.

Each ``daadit.agent.kpi`` row pins one metric to one AI-agent domain
(Sales / Marketing / Helpdesk / Project / Product). The ``target`` is a
plain stored field the team edits in the UI; ``realised`` and everything
derived from it (``progress``, ``status``, the display strings and the
bar width) are **non-stored computed** fields, so every dashboard load
reflects the current database state — no cron, no snapshot to refresh.

Robustness
----------
Every metric query runs through :meth:`_safe`, which swallows any error
(missing model, renamed field, access issue) and returns ``0.0``. A
broken metric therefore shows as an empty/neutral card instead of taking
the whole board down — the same fail-open philosophy the cost-cap uses.

All source queries run with ``sudo()``: the board shows company-level
aggregates to any employee who can see the menu, without granting them
direct read access to every CRM/Sales record.
"""
import logging
from datetime import date

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Confirmed-order states used for "realised revenue".
_SALE_DONE_STATES = ("sale", "done")


class DaaditAgentKpi(models.Model):
    _name = "daadit.agent.kpi"
    _description = "DAADit Agent KPI (target vs realisation)"
    _order = "agent_key, sequence, id"

    # ------------------------------------------------------------------
    # Definition (stored, team-owned)
    # ------------------------------------------------------------------
    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)

    agent_key = fields.Selection(
        selection=[
            ("sales", "Sales"),
            ("marketing", "Marketing"),
            ("helpdesk", "Helpdesk"),
            ("project", "Project"),
            ("product", "Product"),
        ],
        string="Agent",
        required=True,
        index=True,
    )

    # Which live query backs this KPI. Keep in sync with _METRICS below.
    metric_key = fields.Selection(
        selection="_selection_metric_key",
        string="Metric",
        required=True,
    )

    target = fields.Float(
        string="Target",
        help="The goal for this metric. Editable — the team owns its "
             "targets; no code deploy needed to change them.",
    )
    unit = fields.Selection(
        selection=[("eur", "€"), ("count", "#"), ("pct", "%")],
        default="count",
        required=True,
    )

    higher_is_better = fields.Boolean(
        default=True,
        help="On for growth metrics (revenue, leads). Off for metrics you "
             "want to keep low (unassigned tickets, workload skew).",
    )
    pace_aware = fields.Boolean(
        string="Pace-aware",
        default=False,
        help="Judge against the fraction of the year elapsed. A €600k "
             "yearly target at mid-year is measured against ~€300k, not "
             "€600k.",
    )
    informational = fields.Boolean(
        default=False,
        help="Context figure without a real target: shown as a plain "
             "number with a neutral status and no progress bar.",
    )
    signal = fields.Char(
        translate=True,
        help="Short guidance shown on the card — the action this KPI "
             "points to.",
    )

    # ------------------------------------------------------------------
    # Live (non-stored computed)
    # ------------------------------------------------------------------
    realised = fields.Float(compute="_compute_live", store=False)
    progress = fields.Float(
        compute="_compute_live", store=False,
        help="Realised / target, 0..1+ (1 = target met).",
    )
    pace = fields.Float(compute="_compute_live", store=False)
    status = fields.Selection(
        selection=[
            ("good", "Op koers"),
            ("warn", "Aandacht"),
            ("crit", "Achter"),
            ("na", "—"),
        ],
        compute="_compute_live", search="_search_status", store=False,
    )
    realised_display = fields.Char(compute="_compute_live", store=False)
    target_display = fields.Char(compute="_compute_live", store=False)
    progress_label = fields.Char(compute="_compute_live", store=False)
    bar_width = fields.Integer(
        compute="_compute_live", store=False,
        help="Progress bar width in %, capped at 100.",
    )

    # ------------------------------------------------------------------
    # Metric registry — metric_key -> (label, realised-callable name)
    # ------------------------------------------------------------------
    @property
    def _METRICS(self):
        return {
            # --- Sales ---
            "sales_revenue_ytd": ("Omzet dit jaar", "_m_sales_revenue_ytd"),
            "sales_won_count_ytd": ("Gewonnen deals (YTD)", "_m_sales_won_count_ytd"),
            "sales_pipeline_eur": ("Actieve pijplijn", "_m_sales_pipeline_eur"),
            "sales_avg_deal_ytd": ("Gem. dealwaarde (YTD)", "_m_sales_avg_deal_ytd"),
            # --- Marketing ---
            "mkt_leads_ytd": ("Nieuwe leads (YTD)", "_m_mkt_leads_ytd"),
            "mkt_leads_month": ("Leads deze maand", "_m_mkt_leads_month"),
            "mkt_conversion_pct": ("Conversie lead → klant", "_m_mkt_conversion_pct"),
            "mkt_source_pct": ("Leads met bron", "_m_mkt_source_pct"),
            # --- Helpdesk ---
            "hd_unassigned": ("Niet-toegewezen tickets", "_m_hd_unassigned"),
            "hd_open": ("Open tickets", "_m_hd_open"),
            "hd_max_share_pct": ("Grootste werklast (1 persoon)", "_m_hd_max_share_pct"),
            # --- Project ---
            "proj_active": ("Actieve projecten", "_m_proj_active"),
            "proj_open_tasks": ("Open taken", "_m_proj_open_tasks"),
            "proj_urgent": ("Urgente open taken", "_m_proj_urgent"),
            # --- Product ---
            "prod_total_orders": ("Bevestigde orders (totaal)", "_m_prod_total_orders"),
            "prod_total_revenue": ("Omzet boeken (totaal)", "_m_prod_total_revenue"),
        }

    @api.model
    def _selection_metric_key(self):
        return [(k, v[0]) for k, v in self._METRICS.items()]

    # ------------------------------------------------------------------
    # Date + safety helpers
    # ------------------------------------------------------------------
    def _year_bounds(self):
        today = fields.Date.context_today(self)
        return date(today.year, 1, 1), date(today.year, 12, 31), today

    def _month_start(self):
        today = fields.Date.context_today(self)
        return date(today.year, today.month, 1)

    def _pace(self):
        y0, y1, today = self._year_bounds()
        span = (y1 - y0).days or 1
        return max(0.0, min(1.0, (today - y0).days / span))

    @staticmethod
    def _safe(fn):
        """Run a realised-callable; never raise (fail-open to 0.0)."""
        try:
            return float(fn() or 0.0)
        except Exception:  # noqa: BLE001
            _logger.exception(
                "daadit_agent_dashboard: metric query failed; "
                "reporting 0 (fail-open)."
            )
            return 0.0

    def _sum(self, model, domain, field):
        groups = self.env[model].sudo().read_group(domain, [f"{field}:sum"], [])
        return (groups[0].get(field) or 0.0) if groups else 0.0

    def _count(self, model, domain):
        return float(self.env[model].sudo().search_count(domain))

    # ------------------------------------------------------------------
    # Metric implementations (each returns a float; wrapped by _safe)
    # ------------------------------------------------------------------
    # Sales -------------------------------------------------------------
    def _m_sales_revenue_ytd(self):
        y0, _, _ = self._year_bounds()
        return self._sum(
            "sale.order",
            [("state", "in", list(_SALE_DONE_STATES)),
             ("date_order", ">=", y0.strftime("%Y-%m-%d"))],
            "amount_total",
        )

    def _m_sales_won_count_ytd(self):
        y0, _, _ = self._year_bounds()
        return self._count(
            "crm.lead",
            [("type", "=", "opportunity"), ("stage_id.is_won", "=", True),
             ("date_closed", ">=", y0.strftime("%Y-%m-%d 00:00:00"))],
        )

    def _m_sales_pipeline_eur(self):
        return self._sum(
            "crm.lead",
            [("type", "=", "opportunity"), ("active", "=", True),
             ("stage_id.is_won", "=", False)],
            "expected_revenue",
        )

    def _m_sales_avg_deal_ytd(self):
        y0, _, _ = self._year_bounds()
        domain = [("type", "=", "opportunity"), ("stage_id.is_won", "=", True),
                  ("date_closed", ">=", y0.strftime("%Y-%m-%d 00:00:00"))]
        total = self._sum("crm.lead", domain, "expected_revenue")
        count = self._count("crm.lead", domain)
        return (total / count) if count else 0.0

    # Marketing ---------------------------------------------------------
    def _m_mkt_leads_ytd(self):
        y0, _, _ = self._year_bounds()
        return self._count(
            "crm.lead",
            [("create_date", ">=", y0.strftime("%Y-%m-%d 00:00:00"))],
        )

    def _m_mkt_leads_month(self):
        m0 = self._month_start()
        return self._count(
            "crm.lead",
            [("create_date", ">=", m0.strftime("%Y-%m-%d 00:00:00"))],
        )

    def _m_mkt_conversion_pct(self):
        y0, _, _ = self._year_bounds()
        leads = self._count(
            "crm.lead",
            [("create_date", ">=", y0.strftime("%Y-%m-%d 00:00:00"))],
        )
        won = self._count(
            "crm.lead",
            [("type", "=", "opportunity"), ("stage_id.is_won", "=", True),
             ("date_closed", ">=", y0.strftime("%Y-%m-%d 00:00:00"))],
        )
        return (won / leads * 100.0) if leads else 0.0

    def _m_mkt_source_pct(self):
        total = self._count("crm.lead", [])
        with_source = self._count("crm.lead", [("source_id", "!=", False)])
        return (with_source / total * 100.0) if total else 0.0

    # Helpdesk ----------------------------------------------------------
    def _m_hd_unassigned(self):
        return self._count(
            "helpdesk.ticket",
            [("close_date", "=", False), ("stage_id.fold", "=", False),
             ("user_id", "=", False)],
        )

    def _m_hd_open(self):
        return self._count(
            "helpdesk.ticket",
            [("close_date", "=", False), ("stage_id.fold", "=", False)],
        )

    def _m_hd_max_share_pct(self):
        groups = self.env["helpdesk.ticket"].sudo().read_group(
            [("close_date", "=", False), ("stage_id.fold", "=", False),
             ("user_id", "!=", False)],
            ["__count"], ["user_id"], lazy=False,
        )
        counts = [g.get("__count") or 0 for g in groups]
        total = sum(counts)
        return (max(counts) / total * 100.0) if total and counts else 0.0

    # Project -----------------------------------------------------------
    def _m_proj_active(self):
        return self._count("project.project", [("active", "=", True)])

    def _m_proj_open_tasks(self):
        return self._count("project.task", [("is_closed", "=", False)])

    def _m_proj_urgent(self):
        return self._count(
            "project.task",
            [("is_closed", "=", False), ("priority", "=", "3")],
        )

    # Product -----------------------------------------------------------
    def _m_prod_total_orders(self):
        return self._count(
            "sale.order", [("state", "in", list(_SALE_DONE_STATES))],
        )

    def _m_prod_total_revenue(self):
        return self._sum(
            "sale.order",
            [("state", "in", list(_SALE_DONE_STATES))],
            "amount_total",
        )

    # ------------------------------------------------------------------
    # Display formatting
    # ------------------------------------------------------------------
    def _fmt(self, value, unit):
        if unit == "eur":
            whole = int(round(value))
            return "€" + format(whole, ",d").replace(",", ".")
        if unit == "pct":
            return ("%.1f%%" % value).replace(".", ",")
        return format(int(round(value)), ",d").replace(",", ".")

    # ------------------------------------------------------------------
    # The one compute that fills every live field
    # ------------------------------------------------------------------
    def _compute_live(self):
        metrics = self._METRICS
        pace = self._pace()
        for rec in self:
            spec = metrics.get(rec.metric_key)
            realised = self._safe(getattr(rec, spec[1])) if spec else 0.0
            target = rec.target or 0.0

            rec.realised = realised
            rec.pace = pace
            rec.progress = (realised / target) if target else 0.0
            rec.realised_display = rec._fmt(realised, rec.unit)

            # Informational rows: plain number, neutral status, no bar.
            if rec.informational:
                rec.status = "na"
                rec.target_display = ""
                rec.progress_label = ""
                rec.bar_width = 0
                continue

            rec.target_display = rec._fmt(target, rec.unit)

            if rec.higher_is_better:
                expected = target * (pace if rec.pace_aware else 1.0)
                perf = (realised / expected) if expected > 0 else 0.0
                if expected <= 0:
                    rec.status = "na"
                elif perf >= 0.9:
                    rec.status = "good"
                elif perf >= 0.6:
                    rec.status = "warn"
                else:
                    rec.status = "crit"
                rec.progress_label = "%d%%" % round(rec.progress * 100)
                rec.bar_width = max(0, min(100, int(round(rec.progress * 100))))
            else:
                # Lower-is-better: stay at or under target.
                if realised <= target:
                    rec.status = "good"
                elif (target > 0 and realised <= target * 1.25) or \
                     (target == 0 and realised <= 2):
                    rec.status = "warn"
                else:
                    rec.status = "crit"
                rec.progress_label = ""
                if target > 0:
                    rec.bar_width = max(0, min(100, int(round(realised / target * 100))))
                else:
                    rec.bar_width = 100 if realised > 0 else 0

    def _search_status(self, operator, value):
        """Make the computed ``status`` filterable in the search view.

        ``status`` derives from live aggregates, so it has no column to
        query. With only a handful of KPI rows we can compute it for all
        of them and translate the filter into an ``id in [...]`` domain.
        """
        if operator not in ("=", "!=", "in", "not in"):
            return []
        wanted = set(value if isinstance(value, (list, tuple)) else [value])
        all_recs = self.search([])
        matched = [r.id for r in all_recs if r.status in wanted]
        if operator in ("!=", "not in"):
            matched = [r.id for r in all_recs if r.id not in matched]
        return [("id", "in", matched)]

    # ------------------------------------------------------------------
    # Keep name/unit sensible when a metric is picked in the UI
    # ------------------------------------------------------------------
    @api.onchange("metric_key")
    def _onchange_metric_key(self):
        spec = self._METRICS.get(self.metric_key)
        if spec and not self.name:
            self.name = spec[0]

    # ------------------------------------------------------------------
    # Trend charts for the OWL board (Dashboards app). Same fail-open
    # philosophy as the KPI metrics: a broken query returns [] rather
    # than breaking the whole payload.
    # ------------------------------------------------------------------
    def _chart_sales_monthly(self):
        try:
            y0, _, _ = self._year_bounds()
            groups = self.env["sale.order"].sudo().read_group(
                [("state", "in", list(_SALE_DONE_STATES)),
                 ("date_order", ">=", y0.strftime("%Y-%m-%d"))],
                ["amount_total:sum"], ["date_order:month"],
            )
            return [
                {"label": g["date_order:month"], "value": g.get("amount_total") or 0.0}
                for g in groups
            ]
        except Exception:  # noqa: BLE001
            _logger.exception("daadit_agent_dashboard: sales chart query failed")
            return []

    def _chart_leads_monthly(self):
        try:
            y0, _, _ = self._year_bounds()
            groups = self.env["crm.lead"].sudo().read_group(
                [("create_date", ">=", y0.strftime("%Y-%m-%d 00:00:00"))],
                ["__count"], ["create_date:month"], lazy=False,
            )
            return [
                {"label": g["create_date:month"], "value": g.get("__count") or 0}
                for g in groups
            ]
        except Exception:  # noqa: BLE001
            _logger.exception("daadit_agent_dashboard: leads chart query failed")
            return []

    def _chart_helpdesk_workload(self):
        try:
            groups = self.env["helpdesk.ticket"].sudo().read_group(
                [("close_date", "=", False), ("stage_id.fold", "=", False),
                 ("user_id", "!=", False)],
                ["__count"], ["user_id"], lazy=False,
            )
            rows = [
                {"label": g["user_id"][1] if g.get("user_id") else "—",
                 "value": g.get("__count") or 0}
                for g in groups
            ]
            rows.sort(key=lambda r: r["value"], reverse=True)
            return rows[:8]
        except Exception:  # noqa: BLE001
            _logger.exception("daadit_agent_dashboard: helpdesk chart query failed")
            return []

    @api.model
    def get_dashboard_payload(self):
        """Single RPC for the OWL board (static/src/js/agent_dashboard_action.js):
        every KPI plus a handful of light trend charts, computed live, in
        one round-trip."""
        kpis = self.search([])
        kpi_fields = [
            "agent_key", "name", "sequence", "unit", "informational",
            "realised", "realised_display", "target_display",
            "progress_label", "bar_width", "status", "signal",
        ]
        return {
            "kpis": kpis.read(kpi_fields),
            "sales_monthly": self._chart_sales_monthly(),
            "leads_monthly": self._chart_leads_monthly(),
            "helpdesk_workload": self._chart_helpdesk_workload(),
            "pace_pct": round(self._pace() * 100),
            "year": self._year_bounds()[0].year,
        }
