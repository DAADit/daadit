# -*- coding: utf-8 -*-
"""Wie mag welke collega autonoom repareren (taak 727, OAS).

Argus analyseert incidenten van andere agents en dient fixes in via
AUTO-APPLY. In de praktijk lukte dat maar bij twee collega's, en niemand
kon aanwijzen waardoor: de grens zat in de configuratie van de tenant
(de leesscope op ``knowledge.article``, de tools van zijn onderwerp en de
artikelen waar hij mocht schrijven), niet in een regel die je kunt
opzoeken of aanpassen. Een impliciete grens is niet te controleren en al
helemaal niet bewust te verruimen: je merkt hem alleen doordat een fix
uitblijft.

Dit model maakt die grens expliciet en per agent instelbaar. Eén regel
zegt: *deze* agent mag *die* agent repareren, aangezet door *die* mens.

Twee eigenschappen zijn opzettelijk zo gekozen:

* **Standaard verandert er niets.** Een agent zonder regels valt buiten
  deze poort — precies de stand van vandaag. De poort gaat pas gelden
  voor een agent zodra iemand er de eerste regel bij zet, en vanaf dat
  moment is die lijst uitputtend.
* **Verruimen is een menselijke handeling.** Een regel telt alleen mee
  als er een gebruiker in ``approved_by_id`` staat. Een agent kan zijn
  eigen bereik dus niet vergroten door een record aan te maken; het veld
  moet door een mens gevuld zijn.
"""
import logging

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class AiAgentRepairScope(models.Model):
    _name = "daadit.ai.agent.repair.scope"
    _description = "AI Agent — Reparatiebereik"
    _order = "agent_id, target_agent_id"

    agent_id = fields.Many2one(
        "ai.agent", string="Reparerende agent", required=True,
        ondelete="cascade", index=True,
        help="De agent die fixes indient (bij DAADit: Argus).",
    )
    target_agent_id = fields.Many2one(
        "ai.agent", string="Mag repareren", required=True,
        ondelete="cascade", index=True,
        help="De collega waarvan deze agent de prompt of planning mag "
             "aanpassen.",
    )
    article_ref = fields.Integer(
        string="Promptartikel (id)",
        help="Het knowledge-artikel dat de prompt van de doelagent "
             "bevat. Bewust een id en geen relatie: de promptregistry "
             "is de bron van waarheid en die leeft in de tenant, niet "
             "in deze module. Leeg = alleen op agent controleren.",
    )
    approved_by_id = fields.Many2one(
        "res.users", string="Aangezet door",
        help="De mens die dit bereik heeft toegestaan. Zonder deze "
             "gebruiker telt de regel niet mee — een agent kan zijn "
             "eigen bereik niet vergroten.",
    )
    approved_on = fields.Datetime(string="Aangezet op", readonly=True)
    note = fields.Char(
        string="Toelichting",
        help="Waarom dit bereik is toegestaan.",
    )
    active = fields.Boolean(default=True)
    effective = fields.Boolean(
        string="Werkt", compute="_compute_effective", store=True,
        help="De regel telt mee: hij staat aan en is door een mens "
             "aangezet.",
    )

    _agent_target_uniq = models.Constraint(
        "UNIQUE(agent_id, target_agent_id)",
        "Er is al een reparatieregel voor deze combinatie van agents — "
        "pas die regel aan in plaats van een tweede toe te voegen.",
    )

    @api.depends("active", "approved_by_id")
    def _compute_effective(self):
        for rec in self:
            rec.effective = bool(rec.active and rec.approved_by_id)

    @api.constrains("agent_id", "target_agent_id")
    def _check_not_self(self):
        for rec in self:
            if rec.agent_id and rec.agent_id == rec.target_agent_id:
                raise ValidationError(_(
                    "Een agent mag zijn eigen prompt niet autonoom "
                    "repareren: dan controleert niemand de wijziging."
                ))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("approved_by_id") and not vals.get("approved_on"):
                vals["approved_on"] = fields.Datetime.now()
        records = super().create(vals_list)
        for rec in records:
            _logger.info(
                "reparatiebereik: %s mag %s repareren (aangezet door %s)",
                rec.agent_id.display_name, rec.target_agent_id.display_name,
                rec.approved_by_id.display_name or _("nog niemand"),
            )
        return records

    def write(self, vals):
        if vals.get("approved_by_id") and "approved_on" not in vals:
            vals = dict(vals, approved_on=fields.Datetime.now())
        return super().write(vals)

    @api.depends("agent_id", "target_agent_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "%s → %s" % (
                rec.agent_id.display_name or "?",
                rec.target_agent_id.display_name or "?",
            )
