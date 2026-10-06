/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import {
    Component,
    onWillStart,
    onMounted,
    onWillUnmount,
    useState,
} from "@odoo/owl";

const BUS_CHANNEL = "daadit_agent_activity";
const POLL_MS = 60000;

const STATE_LABEL = {
    running: "bezig",
    done: "klaar",
    error: "fout",
    "": "rust",
};

export class DaaditAgentActivity extends Component {
    static template = "daadit_agent_activity.Board";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.busService = useService("bus_service");
        this.env.config.setDisplayName("Agent-activiteit");
        this.state = useState({
            loading: true,
            error: false,
            live: false,
            data: null,
            // filters
            dept: "all",
            agent: "all",
            status: "all",
        });
        onWillStart(() => this.load());
        onMounted(() => {
            this.busService.addChannel(BUS_CHANNEL);
            this.busService.subscribe(
                "daadit_agent_activity/update",
                () => this.scheduleReload(),
            );
            this.state.live = true;
            this.pollTimer = setInterval(() => this.load(), POLL_MS);
        });
        onWillUnmount(() => {
            clearInterval(this.pollTimer);
            clearTimeout(this.reloadTimer);
            this.busService.deleteChannel(BUS_CHANNEL);
        });
    }

    /** Debounce bus-bursts (een run schrijft create + meerdere writes). */
    scheduleReload() {
        clearTimeout(this.reloadTimer);
        this.reloadTimer = setTimeout(() => this.load(), 400);
    }

    async load() {
        try {
            this.state.data = await this.orm.call(
                "daadit.agent.activity", "get_payload", [],
            );
            this.state.error = false;
        } catch (error) {
            console.error("daadit_agent_activity: load failed", error);
            this.state.error = true;
        } finally {
            this.state.loading = false;
        }
    }

    // ------------------------------------------------------------------
    // Filters
    // ------------------------------------------------------------------
    get agentIdsInScope() {
        const d = this.state.data;
        if (!d) {
            return new Set();
        }
        return new Set(
            d.agents
                .filter(
                    (a) =>
                        (this.state.dept === "all" ||
                            String(a.dept_id) === this.state.dept) &&
                        (this.state.agent === "all" ||
                            String(a.id) === this.state.agent),
                )
                .map((a) => a.id),
        );
    }

    get agents() {
        const scope = this.agentIdsInScope;
        return (this.state.data?.agents || []).filter((a) => scope.has(a.id));
    }

    get agentChoices() {
        // Agent-dropdown volgt het afdelingsfilter.
        return (this.state.data?.agents || []).filter(
            (a) =>
                this.state.dept === "all" ||
                String(a.dept_id) === this.state.dept,
        );
    }

    get running() {
        const scope = this.agentIdsInScope;
        return (this.state.data?.running || []).filter((r) =>
            scope.has(r.agent_id),
        );
    }

    get today() {
        const scope = this.agentIdsInScope;
        return (this.state.data?.today || []).filter(
            (r) =>
                scope.has(r.agent_id) &&
                (this.state.status === "all" || r.state === this.state.status),
        );
    }

    get upcoming() {
        const scope = this.agentIdsInScope;
        return (this.state.data?.upcoming || []).filter((s) =>
            scope.has(s.agent_id),
        );
    }

    get feed() {
        const scope = this.agentIdsInScope;
        return (this.state.data?.feed || []).filter((e) =>
            scope.has(e.agent_id),
        );
    }

    onDeptChange(ev) {
        this.state.dept = ev.target.value;
        this.state.agent = "all";
    }

    // ------------------------------------------------------------------
    // Weergave-helpers
    // ------------------------------------------------------------------
    stateLabel(s) {
        return STATE_LABEL[s] ?? s;
    }

    fmtTime(iso) {
        if (!iso) {
            return "—";
        }
        return new Date(iso).toLocaleTimeString("nl-NL", {
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    fmtDay(iso) {
        if (!iso) {
            return "—";
        }
        const d = new Date(iso);
        const today = new Date();
        const sameDay = d.toDateString() === today.toDateString();
        if (sameDay) {
            return "vandaag " + this.fmtTime(iso);
        }
        return (
            d.toLocaleDateString("nl-NL", {
                weekday: "short",
                day: "numeric",
                month: "short",
            }) +
            " " +
            this.fmtTime(iso)
        );
    }

    fmtElapsed(seconds) {
        if (seconds < 60) {
            return `${seconds}s`;
        }
        return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    }

    fmtTokens(n) {
        return n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n);
    }

    openRun(runId) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "daadit.ai.agent.schedule.run",
            res_id: runId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openSchedule(scheduleId) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "daadit.ai.agent.schedule",
            res_id: scheduleId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openAgent(agentId) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "ai.agent",
            res_id: agentId,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("daadit_agent_activity", DaaditAgentActivity);
