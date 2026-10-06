# -*- coding: utf-8 -*-
"""Het meldkanaal opzoeken, niets aanzetten en niets afsluiten (taak 1070).

De opruimer moet weten op welk record de verslagen staan. Dat id staat
bewust niet in code — 182 is het reparatiekanaal van *deze* database en
betekent in een andere niets. Deze migratie leidt het af uit de
werkelijkheid: het record met de meeste verslag-activiteiten.

Wat deze migratie **niet** doet, en dat is opzet:

* de opruimer aanzetten. Die stapel is iemands werkvoorraad; aanzetten is
  een besluit van die persoon (parameter
  ``daadit_ai_agent_schedule.assurance_cleanup_enabled``);
* een activiteit afsluiten of verwijderen;
* de serveractie van de applier herschrijven. Dat is een aparte,
  handmatige stap met de tekst uit ``docs/ASSURANCE_MELDPAD.md``, omdat
  een migratie die 10 kB databasecode blind stringvervangt erger is dan
  de situatie die ze repareert.
"""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.daadit_ai_agent_schedule.models.ai_assurance_report import (
    CLEANUP_MODEL_PARAM,
    CLEANUP_RES_ID_PARAM,
    REPORT_SUMMARY_TOKENS,
)

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    icp = env["ir.config_parameter"].sudo()
    if (icp.get_param(CLEANUP_RES_ID_PARAM, "") or "").strip():
        return
    domain = ["|"] * (len(REPORT_SUMMARY_TOKENS) - 1)
    domain += [("summary", "=like", token + "%")
               for token in REPORT_SUMMARY_TOKENS]
    activities = env["mail.activity"].sudo().search(domain)
    if not activities:
        _logger.info(
            "assurance-meldpad: geen verslag-activiteiten gevonden; "
            "%s blijft leeg en de opruimer doet niets", CLEANUP_RES_ID_PARAM,
        )
        return
    tally = {}
    for activity in activities:
        key = (activity.res_model, activity.res_id)
        tally[key] = tally.get(key, 0) + 1
    (model, res_id), count = max(tally.items(), key=lambda item: item[1])
    icp.set_param(CLEANUP_MODEL_PARAM, model)
    icp.set_param(CLEANUP_RES_ID_PARAM, str(res_id))
    _logger.warning(
        "assurance-meldpad: meldkanaal vastgesteld op %s #%s (%s verslagen). "
        "De opruimer staat uit; een droogloop laat zien wat hij zou sluiten.",
        model, res_id, count,
    )
