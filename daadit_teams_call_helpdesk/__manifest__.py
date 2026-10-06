# -*- coding: utf-8 -*-
{
    'name': 'DAADit Teams Call — Helpdesk bridge',
    'version': '19.0.1.1.0',
    'category': 'Productivity/Helpdesk',
    'summary': 'Call helpdesk-ticket customers from Odoo via Microsoft Teams.',
    'description': """
DAADit Teams Call — Helpdesk bridge
===================================
Auto-install bridge between ``daadit_teams_call`` and Odoo Helpdesk.
Adds a "Call via Teams" button next to the customer phone on every
helpdesk ticket, opening the Teams PSTN deeplink in a new tab.

Uses ``partner_phone`` on the ticket first (agent may have edited it),
falling back to ``partner_id.phone`` or ``partner_id.mobile``.

Loads only when BOTH ``daadit_teams_call`` and ``helpdesk`` are
installed.
""",
    'author': 'DAADit',
    'website': 'https://www.daadit.group',
    'license': 'LGPL-3',
    'depends': [
        'daadit_teams_call',
        'helpdesk',
    ],
    'data': [
        'views/helpdesk_ticket_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': True,
}
