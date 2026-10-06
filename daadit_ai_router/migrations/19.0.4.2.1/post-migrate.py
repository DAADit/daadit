# -*- coding: utf-8 -*-
"""Normaliseer providercode 'claude' naar 'anthropic'.

``ai.router.provider.code`` is een Selection met ``anthropic``; er staat
in productie een record met de waarde ``claude``. Die waarde komt in
geen enkele afleiding voor: ``_DEFAULT_URLS``, ``_MODELS_URLS`` en
``_CONFIG_PARAMS`` zijn allemaal op ``anthropic`` gesleuteld, en de
Messages-API-tak toetst op ``self.code == 'anthropic'``.

Gevolg: zodra de router die provider gebruikt en er geen base-URL is
ingevuld, valt ``_endpoint()`` terug op ``self._DEFAULT_URLS[self.code]``
— en dat is een **KeyError** op 'claude'. Hetzelfde gold voor 'loes'
tot 19.0.4.2.0; daar is de sleutel toegevoegd, hier hoort de dátawaarde
naar de bestaande code toe.

``_REGISTRY_SOURCES`` accepteert allebei (``('claude', 'anthropic')``),
dus de modellenspiegeling blijft na deze wijziging gewoon werken.

Alleen de code wordt gelijkgetrokken; er wordt niets verwijderd.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    # Rechtstreeks in SQL: de ORM weigert 'claude' te lezen/schrijven
    # omdat het geen geldige Selection-waarde is.
    cr.execute(
        "UPDATE ai_router_provider SET code = 'anthropic' "
        "WHERE code = 'claude'"
    )
    if cr.rowcount:
        _logger.info(
            "Router-migratie: %s provider(s) van code 'claude' naar "
            "'anthropic' gezet — 'claude' bestaat niet in de Selection "
            "en liet _endpoint() met een KeyError klappen.",
            cr.rowcount,
        )
