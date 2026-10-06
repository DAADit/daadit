# -*- coding: utf-8 -*-
{
    'name': 'DAADit Teams Call — CRM bridge',
    'version': '19.0.1.1.0',
    'category': 'Sales/CRM',
    'summary': 'Call CRM leads and opportunities from Odoo via Microsoft Teams.',
    'description': """
DAADit Teams Call — CRM bridge
==============================
Auto-install bridge between ``daadit_teams_call`` and Odoo CRM. Adds a
"Call via Teams" button next to the phone field on every lead /
opportunity form, opening the Teams PSTN deeplink in a new tab.

Uses ``phone`` first, falling back to ``mobile`` if the main number is
empty. Honours the same normalisation rules as the core module
(whitespace stripped, leading ``00`` rewritten to ``+``).

Loads only when BOTH ``daadit_teams_call`` and ``crm`` are installed.
""",
    'author': 'DAADit',
    'website': 'https://www.daadit.group',
    'license': 'LGPL-3',
    'depends': [
        'daadit_teams_call',
        'crm',
    ],
    'data': [
        'views/crm_lead_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': True,
}
