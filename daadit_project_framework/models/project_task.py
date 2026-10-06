from odoo import fields, models

# Uiteindelijke fallbacks als geen enkel niveau (taak/project/klant) een waarde geeft.
RESOLVER_DEFAULTS = {
    "daadit_base_branch": "main",
    "daadit_branch_prefix": "feature/",
    "daadit_commit_author_name": "DAADit AI Builder",
    "daadit_commit_author_email": "ai-builder@daadit.nl",
    "daadit_ai_provider": "claude",
    # Twee, niet drie: elke poging is een volledige sessie die zichzelf vanaf
    # nul opbouwt. Bij taak #673 leidden vier builds tot het leeuwendeel van de
    # rekening van 1 augustus. Komt de bouwer er in twee rondes niet uit, dan
    # zit het probleem meestal in de opdracht en niet in de poging.
    "daadit_max_iterations": 2,
}


class ProjectTask(models.Model):
    _name = "project.task"
    _inherit = ["project.task", "daadit.build.config.mixin"]

    daadit_stage_role = fields.Selection(
        related="stage_id.stage_role", string="DAADit-fase-rol", readonly=True)

    def _daadit_resolve_build_config(self):
        """Kies per cascade-veld de meest specifieke waarde: taak -> project -> klant.

        Retourneert een dict met effectieve waarden (met uiteindelijke fallbacks),
        plus de project-niveau schakelaars.
        """
        self.ensure_one()
        project = self.project_id
        partner = self.partner_id or project.partner_id
        layers = [rec for rec in (self, project, partner) if rec]

        cfg = {}
        for fname in self._daadit_config_fields():
            value = False
            for rec in layers:
                if fname in rec._fields and rec[fname]:
                    value = rec[fname]
                    break
            if not value and fname in RESOLVER_DEFAULTS:
                value = RESOLVER_DEFAULTS[fname]
            cfg[fname] = value

        # Project-niveau schakelaars (niet cascaderend).
        cfg["daadit_run_tests"] = project.daadit_run_tests
        cfg["daadit_require_review"] = project.daadit_require_review
        cfg["daadit_auto_build"] = project.daadit_auto_build
        return cfg
