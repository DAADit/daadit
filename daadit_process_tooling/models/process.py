# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError


class DaaditProcess(models.Model):
    _name = "daadit.process"
    _description = "Klantproces (procestooling)"
    _order = "sequence, name, id"

    name = fields.Char(string="Proces", required=True)
    code = fields.Char(string="Code")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    is_template = fields.Boolean(
        string="Template",
        help="Een klant-onafhankelijk sjabloon dat als startpunt dient voor "
             "klanten waar nog geen proces is ingericht.",
    )
    template_id = fields.Many2one(
        "daadit.process", string="Startpunt (template)",
        domain="[('is_template', '=', True)]", ondelete="set null",
    )
    partner_id = fields.Many2one(
        "res.partner", string="Klant",
        domain="[('is_company', '=', True)]", ondelete="cascade", index=True,
    )
    partner_tooling_active = fields.Boolean(
        string="Klant-tooling actief",
        related="partner_id.process_tooling_active", store=False,
    )

    description = fields.Html(string="Omschrijving")

    # --- per-process configuration ---
    portal_visible = fields.Boolean(
        string="Zichtbaar in portal", default=True,
        help="Toon dit proces aan de portalgebruikers van de klant.",
    )
    allow_validation = fields.Boolean(
        string="Valideren toegestaan", default=True,
        help="Laat portalgebruikers de stappen valideren (akkoord / aandacht / afkeur).",
    )

    # --- native process content (fasen -> stappen) ---
    phase_ids = fields.One2many("daadit.process.phase", "process_id", string="Fasen")
    step_ids = fields.One2many("daadit.process.step", "process_id", string="Stappen")
    step_count = fields.Integer(compute="_compute_progress", string="Aantal stappen")
    validated_count = fields.Integer(compute="_compute_progress", string="Gevalideerd")
    progress = fields.Float(compute="_compute_progress", string="Voortgang (%)")

    # --- optional external HTML tool (fallback, e.g. YoMoRo-generated) ---
    tool_html = fields.Binary(string="Externe HTML-tool", attachment=True)
    tool_filename = fields.Char(string="Bestandsnaam")
    version = fields.Char(string="Versie")
    uploaded_on = fields.Datetime(string="Tool bijgewerkt op", readonly=True)

    @api.depends("step_ids.validation_state")
    def _compute_progress(self):
        for proc in self:
            steps = proc.step_ids
            done = len(steps.filtered(lambda s: s.validation_state == "akkoord"))
            proc.step_count = len(steps)
            proc.validated_count = done
            proc.progress = (done / len(steps) * 100.0) if steps else 0.0

    @api.onchange("tool_html")
    def _onchange_tool_html(self):
        for proc in self:
            if proc.tool_html:
                proc.uploaded_on = fields.Datetime.now()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("tool_html"):
                vals.setdefault("uploaded_on", fields.Datetime.now())
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("tool_html"):
            vals.setdefault("uploaded_on", fields.Datetime.now())
        return super().write(vals)

    def action_copy_from_template(self):
        """Copy the chosen template's fasen + stappen into this process."""
        self.ensure_one()
        tpl = self.template_id
        if not tpl:
            raise UserError("Kies eerst een template bij 'Startpunt (template)'.")
        if self.phase_ids:
            raise UserError(
                "Dit proces heeft al fasen. Verwijder die eerst als je opnieuw "
                "vanaf de template wilt beginnen."
            )
        Phase = self.env["daadit.process.phase"]
        Step = self.env["daadit.process.step"]
        for ph in tpl.phase_ids:
            new_phase = Phase.create({
                "process_id": self.id, "name": ph.name, "sequence": ph.sequence,
            })
            for st in ph.step_ids:
                Step.create({
                    "phase_id": new_phase.id, "name": st.name, "sequence": st.sequence,
                    "role": st.role, "system": st.system, "input": st.input,
                    "output": st.output, "description": st.description,
                })
        return True

    def action_open_tool_preview(self):
        """Internal preview of the external HTML tool (fallback)."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": "/process-tooling/process/%s/tool" % self.id,
            "target": "new",
        }

    def action_open_portal_preview(self):
        """Internal preview of the native portal rendering (internal-only route)."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": "/process-tooling/process/%s/preview" % self.id,
            "target": "new",
        }
