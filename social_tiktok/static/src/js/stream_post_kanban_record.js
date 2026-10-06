/** @odoo-module **/

import { StreamPostKanbanRecord } from "@social/js/stream_post_kanban_record";
import { patch } from "@web/core/utils/patch";
import { useEffect } from "@odoo/owl";

/**
 * Extend the base kanban record to add TikTok-specific interactions.
 *
 * TikTok's standard Content API does not provide comment management endpoints,
 * so clicking the comments button opens the video directly on TikTok.
 */
patch(StreamPostKanbanRecord.prototype, {

    setup() {
        super.setup(...arguments);

        // Attach the comments-click handler for TikTok posts
        useEffect((commentEl) => {
            if (commentEl) {
                const handler = this._onTikTokCommentsClick.bind(this);
                commentEl.addEventListener("click", handler);
                return () => commentEl.removeEventListener("click", handler);
            }
        }, () => [this.rootRef.el?.querySelector(".o_social_tiktok_comments")]);
    },

    /**
     * Open the TikTok video page in a new tab when the comments icon is clicked.
     * Full comment management is only available on the TikTok platform itself.
     */
    _onTikTokCommentsClick(ev) {
        ev.stopPropagation();
        const postLink = this.record.post_link?.raw_value;
        if (postLink) {
            window.open(postLink, "_blank", "noopener,noreferrer");
        }
    },
});
