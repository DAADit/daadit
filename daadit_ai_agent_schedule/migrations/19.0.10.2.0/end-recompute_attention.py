# -*- coding: utf-8 -*-
"""Herbereken de aandacht-velden na een wijziging in de claimlogica.

Waarom dit nodig is. ``claims_unverified``, ``needs_attention`` en
``attention_reason`` zijn opgeslagen berekende velden, en
``needs_attention`` hangt van ``claims_unverified`` af. Toen de
claimlogica in 19.0.10.0.0 strenger werd, kreeg run 570 live wél de
nieuwe waarde ``claims_unverified = False``, maar bleef
``attention_reason`` staan op "rapport claimt werk zonder schrijfactie".
De watchdog las daarna een reden die niet meer waar was.

Een upgrade herberekent het gewijzigde veld, maar de afhankelijke velden
volgen niet betrouwbaar mee. Hier gebeurt dat expliciet, voor de runs
waar iemand nog naar kijkt.
"""
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields

# De restlijst en de rapportages kijken niet verder terug dan een
# kwartaal; ouder herberekenen kost tijd zonder dat iemand het leest.
_WINDOW_DAYS = 90
_CHUNK = 200


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Run = env["daadit.ai.agent.schedule.run"]
    since = fields.Datetime.now() - timedelta(days=_WINDOW_DAYS)
    runs = Run.search([("start_date", ">=", since)])
    for index in range(0, len(runs), _CHUNK):
        chunk = runs[index:index + _CHUNK]
        chunk._compute_claims_unverified()
        chunk._compute_needs_attention()
        env.flush_all()
        env.invalidate_all()
