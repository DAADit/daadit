/**
 * Open a specialist chat when Robin (or another orchestrator) hands off.
 *
 * The server tool ``AI: Open Agent Chat`` creates/reuses a discuss
 * channel of type ``ai_chat`` and pushes ``daadit_open_agent_chat`` over
 * the bus with ``channel_id`` / ``agent_id`` / optional ``action``. This
 * service listens and navigates the user there so they can continue with
 * that specialist.
 *
 * Best-effort: several Odoo builds expose different ways to open a
 * Discuss thread. We try, in order: the stock action from
 * ``open_agent_chat``, the mail store's thread.open, then the Discuss
 * client action with the channel as active_id.
 */
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

export const openAgentChatService = {
    dependencies: ["bus_service", "action", "notification"],
    start(env, { bus_service, action, notification }) {
        async function openChannel(payload) {
            if (!payload || !payload.channel_id) {
                return;
            }
            const channelId = payload.channel_id;
            const agentName = payload.agent_name || _t("de specialist");

            // 1) Stock action returned by ai.agent.open_agent_chat().
            if (payload.action && payload.action.type) {
                try {
                    await action.doAction(payload.action);
                    return;
                } catch {
                    // Fall through to the mail-store path.
                }
            }

            // 2) mail.store Thread API (Odoo 17–19 Discuss).
            try {
                const store = env.services["mail.store"];
                if (store?.Thread?.getOrFetch) {
                    const thread = await store.Thread.getOrFetch({
                        model: "discuss.channel",
                        id: channelId,
                    });
                    if (thread?.open) {
                        thread.open({ focus: true });
                        return;
                    }
                    if (thread && store.ChatWindow?.insert) {
                        store.ChatWindow.insert({ thread });
                        return;
                    }
                }
            } catch {
                // Fall through.
            }

            // 3) Discuss client action with the channel as active_id.
            try {
                await action.doAction("mail.action_discuss", {
                    additionalContext: {
                        active_id: channelId,
                        default_active_id: `discuss.channel_${channelId}`,
                    },
                });
                return;
            } catch {
                // Last resort: tell the user the chat exists.
            }

            notification.add(
                _t(
                    "Chat met %s is klaar. Open Discuss om verder te praten.",
                    agentName
                ),
                { type: "info" }
            );
        }

        bus_service.subscribe("daadit_open_agent_chat", (payload) => {
            // Fire-and-forget: a failed handoff UI must never break chat.
            Promise.resolve(openChannel(payload)).catch(() => {});
        });

        return { openChannel };
    },
};

registry.category("services").add("daadit.open_agent_chat", openAgentChatService);
