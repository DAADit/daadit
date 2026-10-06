# -*- coding: utf-8 -*-
"""Een agentrun hangt aan een werkobject, niet aan een gesprek (taak 736).

Een run leefde tot nu toe alleen in zijn eigen runlog. Daardoor bestond
er geen uitchecken (twee runs konden hetzelfde werk oppakken), geen
statusovergang, geen "waarom" en geen resultaat dat later terug te
vinden was. Odoo heeft dat werkobject al: ``project.task``, met stadia,
een chatter en een projectketen. Dit bestand maakt de taak claimbaar,
geeft de agent de doelketen mee en hangt het resultaat aan de taak.
"""
from markupsafe import Markup

from odoo import api, fields, models, _


class ProjectTask(models.Model):
    _inherit = "project.task"

    ai_run_ids = fields.One2many(
        "daadit.ai.agent.schedule.run", "task_id",
        string="Agentruns", readonly=True,
    )
    ai_run_count = fields.Integer(
        string="# Agentruns", compute="_compute_ai_run_count",
    )
    ai_claim_run_id = fields.Many2one(
        "daadit.ai.agent.schedule.run", string="Uitgecheckt door",
        readonly=True, copy=False, index=True,
        help="De run die deze taak nu onderhanden heeft. Zolang dit "
             "gevuld is, pakt geen tweede run hem op.",
    )
    ai_claim_date = fields.Datetime(
        string="Uitgecheckt op", readonly=True, copy=False,
    )
    ai_work_state = fields.Selection(
        [("running", "Agent bezig"),
         ("done", "Agent klaar"),
         ("failed", "Agent gestopt")],
        string="Agentstatus", readonly=True, copy=False,
    )

    @api.depends("ai_run_ids")
    def _compute_ai_run_count(self):
        for rec in self:
            rec.ai_run_count = len(rec.ai_run_ids)

    # ------------------------------------------------------------------
    def _ai_try_claim(self, run, stale_after_seconds):
        """Check deze taak atomair uit voor ``run``.

        Eén UPDATE met de voorwaarde in de WHERE, dus de database
        beslist wie hem krijgt — twee workers die op hetzelfde moment
        dezelfde taak willen, kunnen niet beide slagen. Een claim van
        een run die is omgevallen zonder op te ruimen verjaart na
        ``stale_after_seconds``, anders zou één dode worker de taak voor
        altijd blokkeren.

        Returns True wanneer deze run de taak heeft.
        """
        self.ensure_one()
        self.flush_recordset(["ai_claim_run_id", "ai_claim_date"])
        self.env.cr.execute(
            """
            UPDATE project_task
               SET ai_claim_run_id = %(run)s,
                   ai_claim_date = NOW() AT TIME ZONE 'UTC',
                   ai_work_state = 'running'
             WHERE id = %(task)s
               AND (
                     ai_claim_run_id IS NULL
                     OR ai_claim_date IS NULL
                     OR ai_claim_date <
                        (NOW() AT TIME ZONE 'UTC')
                        - (%(stale)s * INTERVAL '1 second')
                   )
            RETURNING id
            """,
            {
                "run": run.id,
                "task": self.id,
                "stale": stale_after_seconds,
            },
        )
        claimed = bool(self.env.cr.fetchone())
        if claimed:
            self.invalidate_recordset(
                ["ai_claim_run_id", "ai_claim_date", "ai_work_state"]
            )
        return claimed

    def _ai_release(self, run, state):
        """Geef de taak vrij en leg het resultaat erop vast.

        Het rapport komt op de chatter van de taak te staan, niet alleen
        in de runlog: wie de taak opent, ziet wat de agent deed en welke
        records hij daarbij raakte.
        """
        self.ensure_one()
        vals = {"ai_claim_run_id": False, "ai_claim_date": False}
        if state in ("done", "failed"):
            vals["ai_work_state"] = state
        self.sudo().write(vals)
        body = run._task_report_html()
        if body:
            self.sudo().message_post(body=body, subtype_xmlid="mail.mt_note")

    def _ai_goal_context(self):
        """De doelketen van deze taak als tekst voor de agent.

        Project → bovenliggende taken → deze taak. Zonder die keten
        vraagt de agent naar het "waarom" of verzint hij er een; met de
        keten staat het in zijn opdracht.
        """
        self.ensure_one()
        lines = []
        if self.project_id:
            lines.append(_("Project: %s") % self.project_id.display_name)
        chain = []
        parent = self.parent_id
        # Harde grens: een cyclische of extreem diepe keten mag de
        # opdracht niet laten ontsporen.
        while parent and len(chain) < 5:
            chain.append(parent.display_name)
            parent = parent.parent_id
        for name in reversed(chain):
            lines.append(_("Bovenliggend doel: %s") % name)
        lines.append(_("Taak: %s (#%s)") % (self.display_name, self.id))
        if self.date_deadline:
            lines.append(_("Deadline: %s") % self.date_deadline)
        if self.user_ids:
            lines.append(_("Toegewezen aan: %s") % ", ".join(
                self.user_ids.mapped("name")
            ))
        detail = self._ai_task_detail()
        if detail:
            lines.append(_("Omschrijving van de taak: %s") % detail)
        return "\n".join(lines)

    def _ai_task_detail(self):
        """De omschrijving van de taak als platte tekst, afgekapt."""
        self.ensure_one()
        html = self.description or ""
        if not html:
            return ""
        text = " ".join(
            Markup(html).striptags().replace("\xa0", " ").split()
        )
        return text[:2000]
