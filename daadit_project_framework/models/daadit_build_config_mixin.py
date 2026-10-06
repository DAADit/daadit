from odoo import fields, models

# Velden die overerven via de cascade klant -> project -> taak. Bewust GEEN
# defaults hier: een lege waarde betekent "erf van het niveau erboven". De
# uiteindelijke fallbacks (bv. base_branch 'main') zet de resolver op de taak.
DAADIT_CONFIG_FIELDS = [
    "daadit_repo_owner",
    "daadit_repo_name",
    "daadit_base_branch",
    "daadit_branch_prefix",
    "daadit_commit_convention",
    "daadit_commit_author_name",
    "daadit_commit_author_email",
    "daadit_target_env",
    "daadit_path_whitelist",
    "daadit_ai_provider",
    "daadit_ai_model_id",
    "daadit_max_iterations",
]


class DaaditBuildConfigMixin(models.AbstractModel):
    _name = "daadit.build.config.mixin"
    _description = "DAADit build-configuratie (herbruikbaar op klant/project/taak)"

    daadit_repo_owner = fields.Char(string="GitHub owner/org")
    daadit_repo_name = fields.Char(string="GitHub repository")
    daadit_base_branch = fields.Char(
        string="Basisbranch",
        help="Branch waarvandaan de AI-agent vertrekt en waar de PR naartoe gaat. "
        "Leeg = erven; uiteindelijke standaard is 'main'.",
    )
    daadit_branch_prefix = fields.Char(
        string="Branch-prefix",
        help="Verplichte prefix voor branches van de agent. Leeg = erven; "
        "uiteindelijke standaard is 'feature/'.",
    )
    daadit_commit_convention = fields.Text(string="Commit-conventie")
    daadit_commit_author_name = fields.Char(string="Commit-auteur (naam)")
    daadit_commit_author_email = fields.Char(string="Commit-auteur (e-mail)")
    daadit_target_env = fields.Selection(
        selection=[("development", "Development"), ("staging", "Staging")],
        string="Doelomgeving",
    )
    daadit_path_whitelist = fields.Char(
        string="Pad-whitelist",
        help="Optioneel. Komma-gescheiden padprefixen waarbinnen de agent mag "
        "schrijven (bv. 'addons/,custom/'). Leeg = hele repo.",
    )
    daadit_ai_provider = fields.Selection(
        selection=[("claude", "Claude (Anthropic)"), ("codex", "Codex (OpenAI)")],
        string="AI-provider",
    )
    daadit_ai_model_id = fields.Many2one(
        "daadit.ai.claude.model",
        string="AI-model",
        domain="[('active', '=', True)]",
        help="Gesynchroniseerd Claude-model (gedeeld met daadit_ai_claude).",
    )
    daadit_max_iterations = fields.Integer(
        string="Max. automatische pogingen",
        help="Hoe vaak een taak automatisch opnieuw gebouwd mag worden nadat de "
        "review hem afkeurde. Voorkomt dat build en review elkaar eindeloos "
        "blijven aanroepen. 0/leeg = erven; standaard 2.",
    )

    def _daadit_config_fields(self):
        return list(DAADIT_CONFIG_FIELDS)
