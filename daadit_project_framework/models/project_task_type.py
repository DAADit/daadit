from odoo import fields, models

# Canonieke rollen binnen het DAADit-framework. Gedrag (zoals de AI-build-knop
# in daadit_ai_builder) hangt aan deze rol, NOOIT aan de naam van de fase, zodat
# elk project zijn fasen vrij mag benoemen.
STAGE_ROLES = [
    ("intake", "Requirements / Intake"),
    ("todo", "Te doen"),
    ("development", "Ontwikkeling"),
    ("test", "Test"),
    ("documentation", "Documentatie"),
    ("client_review", "Overleg klant"),
]


class ProjectTaskType(models.Model):
    _inherit = "project.task.type"

    stage_role = fields.Selection(
        selection=STAGE_ROLES,
        string="DAADit-rol",
        index=True,
        help="Functionele rol van deze fase binnen het DAADit-framework. "
        "Let op: fasen worden in Odoo gedeeld tussen projecten. Gebruik voor "
        "framework-projecten een eigen (niet-gedeelde) fasenset zodat een rol "
        "niet ongewild bij andere projecten terechtkomt.",
    )
