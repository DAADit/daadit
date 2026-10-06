# Copyright 2026 DAADit
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0.html).
{
    "name": "DAADit AI Customer Memory",
    "version": "19.0.1.0.0",
    "category": "Helpdesk",
    "summary": "Compact customer memories for AI context",
    "author": "DAADit",
    "website": "https://www.daadit.group",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
        "contacts",
        "helpdesk",
        "project",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ai_customer_memory_settings_data.xml",
        "views/ai_customer_memory_views.xml",
        "views/ai_customer_memory_log_views.xml",
        "views/settings_views.xml",
        "views/res_partner_views.xml",
        "views/helpdesk_ticket_views.xml",
        "views/project_task_views.xml",
        "wizard/ai_customer_memory_backfill_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
}
