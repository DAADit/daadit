# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class DaaditProcessLink(models.Model):
    """Een expliciete verbinding tussen twee handelingen.

    Hiervoor werd de samenhang afgeleid uit de vrije tekst in ``input`` en
    ``output``. Dat leest prettig, maar je kunt er geen betrouwbare lijn uit
    tekenen: 'Concept-offerte' en 'concept offerte' zijn voor een mens
    hetzelfde en voor een tekening niet.

    Verbindingen die een fasegrens oversteken vormen tegelijk de lijnen tussen
    de apps op het overzichtsniveau. Die worden dus niet apart vastgelegd.
    """
    _name = "daadit.process.link"
    _description = "Verbinding tussen handelingen"
    _order = "id"

    source_step_id = fields.Many2one(
        "daadit.process.step", string="Van", required=True,
        ondelete="cascade", index=True)
    target_step_id = fields.Many2one(
        "daadit.process.step", string="Naar", required=True,
        ondelete="cascade", index=True)
    process_id = fields.Many2one(
        "daadit.process", string="Proces", related="source_step_id.process_id",
        store=True, index=True)
    label = fields.Char(
        string="Wat gaat er over", help="Het artefact dat van de ene handeling "
        "naar de andere gaat. Verschijnt als label op de lijn.")

    # Odoo 19 negeert _sql_constraints stilzwijgend, dus deze controles staan
    # bewust in Python.
    @api.constrains("source_step_id", "target_step_id")
    def _check_link(self):
        for link in self:
            if link.source_step_id == link.target_step_id:
                raise ValidationError(_(
                    "Een handeling kan niet naar zichzelf verwijzen."))
            source = link.source_step_id.process_id
            target = link.target_step_id.process_id
            if source == target:
                continue
            # Een verbinding mag een procesgrens oversteken -- dat is juist wat
            # de lijnen tussen de processen op domeinniveau oplevert -- maar
            # niet een domeingrens. Twee processen zonder domein horen daarom
            # ook niet aan elkaar geknoopt te worden.
            if not source.domain_id or source.domain_id != target.domain_id:
                raise ValidationError(_(
                    "'%(source)s' en '%(target)s' horen niet bij hetzelfde "
                    "domein. Een verbinding mag van het ene proces naar het "
                    "andere lopen, maar blijft binnen één domein.",
                    source=link.source_step_id.name,
                    target=link.target_step_id.name))

    # name_get() bestaat niet meer sinds Odoo 17.
    @api.depends("source_step_id.name", "target_step_id.name")
    def _compute_display_name(self):
        for link in self:
            link.display_name = "%s → %s" % (
                link.source_step_id.name or "?", link.target_step_id.name or "?")
