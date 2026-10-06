# -*- coding: utf-8 -*-
"""Niveau 1: het domein.

De drie niveaus waar dit canvas over gaat:

  1. Domein      -- 'Order to Cash'            (dit model)
  2. Proces      -- 'Lead tot offerte'         (daadit.process)
  3. Applicatie  -- Sales, Invoicing, ...      (daadit.process.phase)
                    met de handelingen erin    (daadit.process.step)

De hele bedoeling is de vertaling zichtbaar maken: een bedrijf kent zijn eigen
processen, en dit laat zien welke Odoo-applicatie elk daarvan draagt.
"""
from odoo import _, fields, models

from .process import STATE_CLASS


class DaaditProcessDomain(models.Model):
    _name = "daadit.process.domain"
    _description = "Procesdomein"
    _order = "sequence, name, id"

    name = fields.Char(string="Domein", required=True,
                       help="Bijvoorbeeld 'Order to Cash' of 'Procure to Pay'.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    partner_id = fields.Many2one("res.partner", string="Klant", index=True)
    description = fields.Html(string="Omschrijving")
    portal_visible = fields.Boolean(
        string="Zichtbaar op portal", default=True,
        help="Uit betekent dat de klant dit domein niet in zijn portal ziet.")
    allow_validation = fields.Boolean(
        string="Klant mag meedenken", default=True,
        help="Aan betekent dat de klant handelingen mag beoordelen en "
             "toevoegen. Dat is wat de gap-analyse voedt.")
    project_id = fields.Many2one(
        "project.project", string="Project",
        help="Het project waarin bevindingen uit dit domein als taak landen. "
             "Een proces zonder eigen project valt hierop terug.")

    process_ids = fields.One2many(
        "daadit.process", "domain_id", string="Processen")
    process_count = fields.Integer(compute="_compute_counts")
    app_count = fields.Integer(
        string="Betrokken apps", compute="_compute_counts",
        help="Het aantal verschillende Odoo-applicaties dat dit domein raakt.")

    def _compute_counts(self):
        for record in self:
            phases = record.process_ids.phase_ids
            record.process_count = len(record.process_ids)
            record.app_count = len(set(
                phases.filtered("app_technical_name").mapped("app_technical_name")))

    # ------------------------------------------------------------------
    def _canvas_graph(self):
        """De hele tekening voor alle drie de niveaus in één keer.

        Bewust één payload: een domein is klein genoeg om in één keer op te
        halen, en dan is inzoomen naar een proces of een app direct in plaats
        van een wachtmoment per klik.
        """
        self.ensure_one()
        processes = self.process_ids.sorted(lambda p: (p.sequence, p.id))

        nodes, graphs = [], {}
        for index, process in enumerate(processes):
            graphs[process.id] = process._canvas_graph()
            phases = process.phase_ids.sorted(lambda p: (p.sequence, p.id))
            steps = process.step_ids
            # De apps die dit proces raakt, ontdubbeld maar in volgorde: dit is
            # de vertaling waar het de klant om gaat.
            apps, seen = [], set()
            for phase in phases:
                key = phase.app_technical_name
                if not key or key in seen:
                    continue
                seen.add(key)
                apps.append({"label": phase.app_label or phase.name,
                             "icon": phase.app_icon or "",
                             "module": key,
                             "phase": phase.id})
            nodes.append({
                "id": process.id,
                "name": process.name,
                "x": process.canvas_x or (60 + index * 330),
                "y": process.canvas_y or 150,
                "apps": apps,
                "steps": len(steps),
                "states": [STATE_CLASS.get(s.validation_state, "open")
                           for s in steps.sorted(lambda s: (s.sequence, s.id))],
            })

        # Verbindingen die een procesgrens oversteken vormen de lijnen tussen de
        # processen. Net als een niveau lager, waar fasegrens-overstekende
        # verbindingen de lijnen tussen de apps vormen. Eén bron voor alles.
        links = self.env["daadit.process.link"].search(
            [("process_id", "in", processes.ids)])
        flows, seen_flow = [], set()
        for link in links:
            source = link.source_step_id.process_id
            target = link.target_step_id.process_id
            if source == target or target not in processes:
                continue
            key = (source.id, target.id)
            if key in seen_flow:
                continue
            seen_flow.add(key)
            flows.append({"from": key[0], "to": key[1],
                          "label": link.label or link.source_step_id.output or ""})

        return {
            "domain": {
                "id": self.id,
                "name": self.name,
                "client": self.partner_id.display_name or "",
                "can_edit": bool(self.allow_validation),
            },
            "processes": nodes,
            "process_flows": flows,
            "graphs": graphs,
        }

    def action_view_domain_canvas(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": "/my/domeinen/%s/canvas" % self.id,
            "target": "new",
        }

    def action_view_domain_tasks(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Taken uit dit domein"),
            "res_model": "project.task",
            "domain": [("daadit_process_id", "in", self.process_ids.ids)],
            "view_mode": "list,form",
        }
