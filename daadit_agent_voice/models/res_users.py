from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    daadit_voice_ptt_hotkey = fields.Char(
        default="alt+shift",
        string="Praten-toets (push-to-talk)",
        help=(
            "Toetscombinatie die je ingedrukt houdt om te praten in een "
            "agentgesprek. Bijv. 'alt+shift', 'ctrl+space', of "
            "'alt+shift+space'. Dubbeltik dezelfde combinatie voor een "
            "doorlopend gesprek."
        ),
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ["daadit_voice_ptt_hotkey"]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ["daadit_voice_ptt_hotkey"]
