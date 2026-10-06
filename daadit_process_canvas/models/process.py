# -*- coding: utf-8 -*-
from odoo import _, fields, models

# Van de validatiestand van een handeling naar de kleurcode op het canvas.
STATE_CLASS = {
    "open": "open",
    "akkoord": "ok",
    "aandacht": "warn",
    "afkeur": "dev",
}


class DaaditProcess(models.Model):
    _inherit = "daadit.process"

    domain_id = fields.Many2one(
        "daadit.process.domain", string="Domein", index=True,
        ondelete="set null",
        help="Het bedrijfsdomein waar dit proces onder valt, bijvoorbeeld "
             "'Order to Cash'. Dat is niveau 1 van het canvas.")
    canvas_x = fields.Integer(string="Positie X", default=0)
    canvas_y = fields.Integer(string="Positie Y", default=0)

    project_id = fields.Many2one(
        "project.project", string="Project",
        help="Het project waarin de bevindingen van de klant als taak landen. "
             "Leeg laten mag: dan valt het terug op het project van het domein. "
             "Zonder allebei blijft het canvas werken, maar levert een afwijking "
             "geen taak op.")

    def _canvas_project(self):
        """Waar bevindingen uit dit proces landen.

        Eerst het proces zelf, anders het domein. Zo hoef je het project maar
        op één plek in te stellen voor een heel domein, maar kun je er per
        proces van afwijken.
        """
        self.ensure_one()
        return self.project_id or self.domain_id.project_id
    task_count = fields.Integer(
        string="Taken uit dit proces", compute="_compute_task_count")

    def _compute_task_count(self):
        # sudo: dit veld wordt ook gelezen als een klant zijn proces opent, en
        # die heeft geen leesrecht op project.task.
        grouped = dict(self.env["project.task"].sudo()._read_group(
            [("daadit_process_id", "in", self.ids)],
            groupby=["daadit_process_id"], aggregates=["__count"])) if self.ids else {}
        for process in self:
            process.task_count = grouped.get(process, 0)

    def action_view_process_tasks(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Taken uit dit proces"),
            "res_model": "project.task",
            "domain": [("daadit_process_id", "=", self.id)],
            "context": {"default_project_id": self.project_id.id,
                        "default_partner_id": self.partner_id.id},
            "view_mode": "list,form",
        }

    # ------------------------------------------------------------------
    # Het canvas
    # ------------------------------------------------------------------
    def _canvas_graph(self):
        """De hele tekening als gewone Python-structuren.

        Bewust hier en niet in de controller: dezelfde grafiek voedt straks ook
        een weergave in de backend, en zo is er één plek waar de vorm bepaald
        wordt.
        """
        self.ensure_one()
        phases = self.phase_ids.sorted(lambda p: (p.sequence, p.id))
        steps_by_phase = {p.id: p.step_ids.sorted(lambda s: (s.sequence, s.id))
                          for p in phases}
        # Alleen verbindingen die binnen dit proces blijven. Een verbinding die
        # naar een ander proces vertrekt hoort op domeinniveau thuis, niet hier.
        links = self.env["daadit.process.link"].search(
            [("process_id", "=", self.id)]).filtered(
            lambda link: link.target_step_id.process_id == self)

        apps = []
        for index, phase in enumerate(phases):
            steps = steps_by_phase[phase.id]
            apps.append({
                "id": phase.id,
                "label": phase.app_label or phase.name,
                "subtitle": phase.app_technical_name or "",
                "icon": phase.app_icon or "",
                # Nog nooit versleept? Dan een nette rij in leesvolgorde, zodat
                # een vers proces niet als een hoop op elkaar begint.
                "x": phase.canvas_x or (60 + index * 300),
                "y": phase.canvas_y or 150,
                "states": [STATE_CLASS.get(s.validation_state, "open")
                           for s in steps],
            })

        flows, external = [], {}
        seen_flow = set()
        for link in links:
            src, tgt = link.source_step_id, link.target_step_id
            if src.phase_id == tgt.phase_id:
                continue
            # Verbindingen die een fasegrens oversteken vormen de lijnen tussen
            # de apps. Ze worden dus niet apart vastgelegd.
            label = link.label or src.output or ""
            key = (src.phase_id.id, tgt.phase_id.id)
            if key not in seen_flow:
                seen_flow.add(key)
                flows.append({"from": key[0], "to": key[1], "label": label})
            external.setdefault(src.phase_id.id, []).append({
                "dir": "out", "phase": tgt.phase_id.id, "step": src.id,
                "label": label})
            external.setdefault(tgt.phase_id.id, []).append({
                "dir": "in", "phase": src.phase_id.id, "step": tgt.id,
                "label": label})

        steps, inner = {}, {}
        for phase in phases:
            rows = []
            for index, step in enumerate(steps_by_phase[phase.id]):
                rows.append({
                    "id": step.id,
                    "name": step.name,
                    "role": step.role or "",
                    "input": step.input or "",
                    "output": step.output or "",
                    "state": step.validation_state or "open",
                    "cls": STATE_CLASS.get(step.validation_state, "open"),
                    "note": step.validation_note or "",
                    "origin": step.origin,
                    "x": step.canvas_x or (120 + (index % 3) * 310),
                    "y": step.canvas_y or (60 + (index // 3) * 190),
                })
            steps[phase.id] = rows
            inner[phase.id] = [
                {"from": link.source_step_id.id,
                 "to": link.target_step_id.id,
                 "label": link.label or ""}
                for link in links
                if link.source_step_id.phase_id == phase
                and link.target_step_id.phase_id == phase
            ]

        return {
            "process": {
                "id": self.id,
                "name": self.name,
                "client": self.partner_id.display_name or "",
                "can_edit": bool(self.allow_validation),
                "has_project": bool(self.project_id),
            },
            "apps": apps,
            "flows": flows,
            "steps": steps,
            "links": inner,
            "external": external,
        }
