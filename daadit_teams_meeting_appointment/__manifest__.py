# -*- coding: utf-8 -*-
{
    'name': 'DAADit Teams Meeting — Appointment bridge',
    'version': '19.0.1.0.1',
    'category': 'Productivity/Calendar',
    'summary': 'Offer Microsoft Teams as a videoconferencing option on Online Appointments.',
    'description': """
DAADit Teams Meeting — Appointment bridge
=========================================
Auto-install bridge between ``daadit_teams_meeting`` and Odoo's Online
Appointments. Adds ``Microsoft Teams`` to the ``Link videoconferentie``
dropdown on every Appointment Type; bookings made under such a type get
a Teams join URL attached automatically through the parent module's
existing create-hook.

Loaded only when BOTH ``daadit_teams_meeting`` and ``appointment`` are
installed — never causes a load failure on standalone deployments.
""",
    'author': 'DAADit',
    'website': 'https://www.daadit.group',
    'license': 'LGPL-3',
    'depends': [
        'daadit_teams_meeting',
        'appointment',
    ],
    'data': [],
    'installable': True,
    'application': False,
    'auto_install': True,
}
