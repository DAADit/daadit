# -*- coding: utf-8 -*-
{
    "name": "DAADit Claude Design",
    "version": "19.0.3.0.3",
    "summary": "AI-tool: haal live design system uit Claude Design.",
    "description": """
DAADit Claude Design
====================
Laat marketingagents het live design system uit Claude Design ophalen
vóór website- en social drafts, en laat Penny en Mark daarmee een
social-visual in de huisstijl renderen (tool: maak_social_visual).

Na installatie: Instellingen → AI → Claude Design (huisstijl).
Technische naam: daadit_claude_design
""",
    "author": "DAADit",
    "website": "https://www.daadit.group",
    "license": "OEEL-1",
    "category": "Marketing",
    # Bewust GEEN afhankelijkheid van 'social': de visual staat op
    # zichzelf en wordt alleen aan een post gehangen als die app er is.
    # Zo blijft de module installeerbaar in klantomgevingen zonder
    # Social Marketing.
    "depends": [
        "ai",
        "ai_app",
    ],
    "data": [
        "data/ir_config_parameter.xml",
        "data/ai_tools.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    # Visible under Apps without clearing the "Apps" filter.
    "application": True,
    "auto_install": False,
}
