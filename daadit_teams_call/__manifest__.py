# -*- coding: utf-8 -*-
{
    'name': 'DAADit Teams Call',
    'version': '19.0.1.1.0',
    'category': 'Productivity/Phone',
    'summary': 'Click-to-call via Microsoft Teams from any Odoo contact.',
    'description': """
DAADit Teams Call
=================
Standalone click-to-call functionality using Microsoft Teams deeplinks.

* Adds a "Call via Teams" button next to the phone field on every
  ``res.partner`` form
* Opens ``https://teams.microsoft.com/l/call/0/0?users=4:<phone>`` in a
  new browser tab — Teams desktop app handles the call
* Works with Teams Phone (PSTN calling) for actual dial-out; without it,
  the link still opens Teams for manual handling
* Normalises phone input (whitespace / parens stripped, ``00`` → ``+``)

Independent of ``daadit_teams_meeting`` and Odoo's ``voip`` module —
customers who don't need calendar integration or telephony can install
this on its own. Bridge modules (e.g. helpdesk) live in separate addons.
""",
    'author': 'DAADit',
    'website': 'https://www.daadit.group',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'contacts',
    ],
    'data': [
        'views/res_partner_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
