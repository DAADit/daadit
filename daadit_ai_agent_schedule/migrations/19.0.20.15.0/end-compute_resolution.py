# -*- coding: utf-8 -*-
"""Reken "inmiddels opgelost" opnieuw met de strengere bewijsregel (taak 803).

Een opvolgrun die zelf schrijfwerk verloor, of niets vastlegde terwijl de
oorspronkelijke run schrijfwerk verloor, telt niet meer als herstel. De
oplossing kijkt naar andere records, dus expliciet over het laatste
kwartaal, net als bij 19.0.12.0.0.
"""
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields

_WINDOW_DAYS = 90
_CHUNK = 200


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Run = env["daadit.ai.agent.schedule.run"]
    since = fields.Datetime.now() - timedelta(days=_WINDOW_DAYS)
    runs = Run.search(
        [("start_date", ">=", since)], order="start_date desc, id desc",
    )
    for index in range(0, len(runs), _CHUNK):
        chunk = runs[index:index + _CHUNK]
        chunk._compute_resolution()
        env.flush_all()
        env.invalidate_all()
