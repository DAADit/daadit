from odoo import fields, models


class ProjectProject(models.Model):
    _name = "project.project"
    _inherit = ["project.project", "daadit.build.config.mixin"]

    # ------------------------------------------------------------------
    # Intake / requirements (project-specifiek, niet cascaderend)
    # ------------------------------------------------------------------
    daadit_intake_method = fields.Selection(
        selection=[
            ("interview", "Interview"),
            ("process_model", "Procesmodel"),
            ("both", "Beide"),
        ],
        string="Intake-methode",
        help="Hoe halen we het proces bij de klant op tijdens de requirements-fase.",
    )
    daadit_intake_target_stage_id = fields.Many2one(
        "project.task.type",
        string="Doelfase intake-taken",
        domain="[('project_ids', 'in', id)]",
        help="Fase waarin taken uit de intake / gap-analyse worden geplaatst.",
    )

    # ------------------------------------------------------------------
    # Build-schakelaars (project-niveau; booleans cascaderen slecht)
    # ------------------------------------------------------------------
    daadit_run_tests = fields.Boolean(string="Tests draaien", default=True)
    daadit_require_review = fields.Boolean(string="Review verplicht", default=True)
    daadit_auto_build = fields.Boolean(
        string="Automatisch AI-build starten",
        help="Indien aan: taken die naar een fase met rol 'Ontwikkeling' worden "
        "gesleept starten automatisch een AI-build. Standaard uit.",
    )
    daadit_auto_test = fields.Boolean(
        string="Automatisch AI-review starten",
        help="Indien aan: taken die in een fase met rol 'Test' belanden worden "
        "automatisch door de review-agent beoordeeld. Vereist een geslaagde "
        "build; anders gebeurt er niets.",
    )
    daadit_auto_docs = fields.Boolean(
        string="Automatisch documentatie schrijven",
        help="Indien aan: taken die in een fase met rol 'Documentatie' belanden "
        "krijgen automatisch een uitleg van de wijziging, waarna ze doorschuiven "
        "naar 'Overleg klant' met een activiteit voor de projectmanager.",
    )
