# -*- coding: utf-8 -*-
import logging

from markupsafe import Markup, escape

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Standen die een gat aanduiden tussen hoe Odoo het doet en hoe de klant werkt.
# 'akkoord' en 'open' leveren dus geen taak op.
GAP_STATES = ("aandacht", "afkeur")

TASK_TITLE = {
    "afkeur": "Anders gewenst: %s",
    "aandacht": "Aandachtspunt: %s",
}


class DaaditProcessStep(models.Model):
    """Een handeling is op het canvas één knooppunt met een in- en uitgang."""
    _inherit = "daadit.process.step"

    canvas_x = fields.Integer(string="Positie X", default=0)
    canvas_y = fields.Integer(string="Positie Y", default=0)

    # De bestaande velden 'input' en 'output' blijven staan: die beschrijven
    # WAT er in en uit gaat. De verbindingen hieronder beschrijven WAARHEEN.
    outgoing_link_ids = fields.One2many(
        "daadit.process.link", "source_step_id", string="Gaat naar")
    incoming_link_ids = fields.One2many(
        "daadit.process.link", "target_step_id", string="Komt van")

    origin = fields.Selection(
        [("base", "Basisinrichting"), ("customer", "Toegevoegd door klant")],
        string="Herkomst", required=True, index=True,
        # Wie via de portal werkt is een 'share'-gebruiker. Zo klopt de herkomst
        # ook als een handeling ergens anders vandaan wordt aangemaakt dan via
        # het canvas -- een import, een script, de backend.
        default=lambda self: "customer" if self.env.user.share else "base",
        help="Een handeling die de klant zelf toevoegt zit per definitie niet "
             "in de basisinrichting, en levert dus meteen een taak op.")
    task_id = fields.Many2one(
        "project.task", string="Taak", readonly=True, copy=False,
        ondelete="set null")

    # ------------------------------------------------------------------
    # Aanjagers. Bewust op het model en niet in de portal-controller, zodat
    # dit net zo goed werkt vanuit de backend of een import.
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        steps = super().create(vals_list)
        for step in steps:
            if step.origin == "customer" or step.validation_state in GAP_STATES:
                step._daadit_sync_task()
        return steps

    def write(self, vals):
        # Vóór het schrijven vastleggen wie er al een gat was, anders kun je na
        # afloop niet meer zien of dit een overgang is of een herhaling.
        was_gap = {s.id: s.validation_state in GAP_STATES for s in self}
        res = super().write(vals)
        if not ({"validation_state", "validation_note", "name"} & set(vals)):
            return res
        for step in self:
            is_gap = step.validation_state in GAP_STATES
            if is_gap:
                step._daadit_sync_task()
            elif was_gap.get(step.id) and step.task_id:
                step._daadit_note_reverted()
        return res

    # ------------------------------------------------------------------
    def _daadit_sync_task(self):
        """Maak de taak, of werk hem bij als hij er al is.

        Idempotent: één taak per handeling. Twee keer klikken hoort geen twee
        taken op te leveren.
        """
        self.ensure_one()
        # Vanaf hier met sudo: de klant werkt als portaalgebruiker en heeft geen
        # rechten op project.project of project.task. Hij mag een taak
        # veroorzaken, niet er zelf een aanmaken -- en zonder dit loopt precies
        # deze stroom stuk zodra een echte klant hem gebruikt.
        step = self.sudo()
        project = step.process_id._canvas_project()
        if not project:
            _logger.warning(
                "Procescanvas: handeling %s (proces %s) is een afwijking, maar "
                "op het proces staat geen project. Er is geen taak aangemaakt.",
                step.name, step.process_id.display_name)
            return False
        values = step._daadit_task_values(project)
        if step.task_id:
            step.task_id.write({
                "name": values["name"],
                "description": values["description"],
            })
            return step.task_id
        task = self.env["project.task"].sudo().create(values)
        step.task_id = task
        return task

    def _daadit_task_values(self, project):
        self.ensure_one()
        if self.origin == "customer":
            name = _("Nieuwe handeling: %s", self.name)
        else:
            name = _(TASK_TITLE.get(self.validation_state, "%s"), self.name)
        values = {
            "name": name,
            "project_id": project.id,
            "partner_id": self.process_id.partner_id.id or False,
            "description": self._daadit_task_description(),
            "daadit_process_step_id": self.id,
        }
        if self.validation_state == "afkeur":
            # project.task kent vier standen (0 laag t/m 3 urgent); een
            # expliciete afkeuring van de klant is 'High', niet 'Medium'.
            values["priority"] = "2"
        stage = self._daadit_intake_stage(project)
        if stage:
            values["stage_id"] = stage.id
        return values

    def _daadit_intake_stage(self, project):
        """De fase waarin bevindingen binnenkomen.

        Eerst de instelling op het project uit daadit_project_framework; valt
        die weg, dan de eerste fase met de rol 'intake'. Zonder allebei laat we
        de fase leeg en pakt Odoo zijn eigen eerste fase.
        """
        stage = project.daadit_intake_target_stage_id
        if stage:
            return stage
        return self.env["project.task.type"].search(
            [("stage_role", "=", "intake"),
             ("project_ids", "in", project.id)], limit=1)

    def _daadit_task_description(self):
        self.ensure_one()
        rows = [
            (_("Fase"), self.phase_id.name),
            (_("Odoo-app"), self.phase_id.app_label or self.system),
            (_("Wie doet dit"), self.role),
            (_("Wat gaat erin"), self.input),
            (_("Wat komt eruit"), self.output),
        ]
        items = Markup("").join(
            Markup("<li><strong>%s:</strong> %s</li>") % (label, value)
            for label, value in rows if value)
        body = Markup("<p>%s</p><ul>%s</ul>") % (
            _("Deze taak komt uit het procescanvas van %s.",
              self.process_id.display_name),
            items)
        if self.validation_state in GAP_STATES and self.validation_note:
            body += Markup("<p><strong>%s</strong></p><blockquote>%s</blockquote>") % (
                _("Wat de klant erover zegt:"),
                Markup("<br/>").join(
                    escape(line) for line in self.validation_note.splitlines()))
        elif self.origin == "customer":
            body += Markup("<p>%s</p>") % _(
                "De klant heeft deze handeling zelf toegevoegd; ze zit dus niet "
                "in de basisinrichting.")
        return body

    def _daadit_note_reverted(self):
        """De klant draait zijn oordeel terug.

        De taak blijft staan: er kan al aan gewerkt zijn, en stilletjes werk
        weggooien is erger dan een taak die te veel op de lijst staat.
        """
        self.ensure_one()
        # Ook hier sudo: het is de klant die terugdraait, en die mag niet op een
        # projecttaak schrijven.
        self.sudo().task_id.message_post(body=Markup("<p>%s</p>") % _(
            "De klant heeft '%(step)s' in het procescanvas teruggezet op "
            "'%(state)s'. Beoordeel of deze taak nog nodig is.",
            step=self.name,
            state=dict(self._fields["validation_state"].selection).get(
                self.validation_state, self.validation_state)))
