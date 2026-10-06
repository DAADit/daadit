/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { Component, onWillStart, useState } from "@odoo/owl";

const AGENT_META = {
    sales: { icon: "\u{1F4BC}", label: "Sales" },
    marketing: { icon: "\u{1F4E3}", label: "Marketing" },
    helpdesk: { icon: "\u{1F3AB}", label: "Helpdesk" },
    project: { icon: "\u{1F4CA}", label: "Project" },
    product: { icon: "\u{1F4E6}", label: "Product" },
};
const AGENT_ORDER = ["sales", "marketing", "helpdesk", "project", "product"];

function formatChartRow(row) {
    const word = (row.label || "").split(" ")[0] || "";
    return { value: Math.round(row.value || 0), month: word.slice(0, 3).toLowerCase() };
}

export class DaaditAgentDashboard extends Component {
    static template = "daadit_agent_dashboard.Board";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.env.config.setDisplayName("Agent Dashboard");
        this.state = useState({
            loading: true,
            error: false,
            groups: [],
            salesMonthly: [],
            leadsMonthly: [],
            helpdeskWorkload: [],
            pacePct: 0,
            year: null,
        });
        onWillStart(() => this.load());
    }

    async load() {
        try {
            const data = await this.orm.call("daadit.agent.kpi", "get_dashboard_payload", []);
            const byAgent = {};
            for (const kpi of data.kpis || []) {
                if (!byAgent[kpi.agent_key]) {
                    byAgent[kpi.agent_key] = [];
                }
                byAgent[kpi.agent_key].push(kpi);
            }
            this.state.groups = AGENT_ORDER.filter((key) => byAgent[key]).map((key) => ({
                key,
                icon: AGENT_META[key].icon,
                label: AGENT_META[key].label,
                kpis: byAgent[key],
            }));
            this.state.salesMonthly = (data.sales_monthly || []).map(formatChartRow);
            this.state.leadsMonthly = (data.leads_monthly || []).map(formatChartRow);
            this.state.helpdeskWorkload = data.helpdesk_workload || [];
            this.state.pacePct = data.pace_pct || 0;
            this.state.year = data.year;
        } catch (error) {
            console.error("daadit_agent_dashboard: failed to load payload", error);
            this.state.error = true;
        } finally {
            this.state.loading = false;
        }
    }

    maxValue(rows) {
        return Math.max(1, ...rows.map((r) => r.value));
    }

    barHeight(value, rows) {
        return Math.round((value / this.maxValue(rows)) * 100);
    }

    openTargets() {
        this.actionService.doAction("daadit_agent_dashboard.action_daadit_agent_kpi");
    }
}

registry.category("actions").add("daadit_agent_dashboard", DaaditAgentDashboard);
