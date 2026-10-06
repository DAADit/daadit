import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { AddSocialStreamDialog } from "@social/js/add_stream_modal";
import { StreamPostDashboard } from "@social/js/stream_post_kanban_dashboard";

/*
 * The social app blindly does `document.location = action.url` after
 * 'action_add_account', which breaks when the server returns a regular
 * window action (our TikTok credentials wizard): the browser then navigates
 * to "undefined". Route non-URL actions through the action service instead.
 */
function executeAddAccountAction(env, action) {
    if (action && action.type && action.type !== "ir.actions.act_url") {
        env.services.action.doAction(action);
    } else if (action && action.url) {
        document.location = action.url;
    }
}

patch(AddSocialStreamDialog.prototype, {
    _onClickSocialMedia(event) {
        const mediaId = parseInt(event.currentTarget.dataset.mediaId);
        const selectCompany = this.modalRef.el.querySelector('select[name="company_id"]');
        const companyId = selectCompany ? parseInt(selectCompany.value) || 0 : undefined;

        this.orm.call("social.media", "action_add_account", [mediaId], {
            company_id: companyId,
        }).then((action) => {
            if (action && action.type && action.type !== "ir.actions.act_url") {
                this.props.close();
            }
            executeAddAccountAction(this.env, action);
        });
    },
});

patch(StreamPostDashboard.prototype, {
    _onRelinkAccount(event) {
        const mediaId = parseInt(event.currentTarget.dataset.mediaId);
        if (this.props.isSocialManager) {
            this.orm.call("social.media", "action_add_account", [mediaId]).then((action) => {
                executeAddAccountAction(this.env, action);
            });
        } else {
            this.notification.add(
                _t("Sorry, you're not allowed to re-link this account, please contact your administrator."),
                { type: "danger" }
            );
        }
    },
});
