# -*- coding: utf-8 -*-
{
    'name': 'DAADit Teams Meeting',
    'version': '19.0.3.0.2',
    'category': 'Productivity/Calendar',
    'summary': 'Microsoft Teams online meetings as default videoconferencing in Odoo Calendar.',
    'description': """
DAADit Teams Meeting
====================
Replaces (or augments) Odoo's built-in videoconferencing on calendar events
with Microsoft Teams online meetings, created through Microsoft Graph using
delegated permissions (per organiser).

Features
--------
* Per-user OAuth2 connection to Microsoft Graph
* Automatic creation / update / deletion of a Teams meeting when an Odoo
  calendar event is created / modified / unlinked
* Join URL stored as ``videocall_location`` so it shows up in invitation
  emails and calendar reminders
* Per-user default: "Use Microsoft Teams for new meetings"
* Tenant-wide Azure AD app credentials configured in System Parameters
""",
    'author': 'DAADit',
    'website': 'https://www.daadit.group',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        'calendar',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/res_config_settings_views.xml',
        'views/res_users_views.xml',
        'views/calendar_event_views.xml',
    ],
    'external_dependencies': {
        'python': ['requests'],
    },
    'assets': {
        'web.assets_backend': [
            'daadit_teams_meeting/static/src/scss/calendar_event.scss',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'post_init_hook': '_patch_calendar_mail_templates',
}
