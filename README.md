# DAADit-modules voor Odoo

De Odoo-modules die DAADit aan klanten aanbiedt, voor **Odoo 19**
(Enterprise). Deze repository is een automatische spiegel van de interne
monorepo van DAADit: elke push naar `main` daar zet de publieke modules
hier opnieuw neer. Pull requests horen daarom niet hier thuis; meld
problemen via [daadit.group](https://www.daadit.group) of je klantportaal.

## Installeren

- **Odoo.sh**: zet de gewenste module-mappen (en de modules waar ze van
  afhangen, zie het manifest) in je eigen Odoo.sh-repository, of laat het
  DAADit-klantportaal dat voor je doen met *Push naar Odoo.sh*.
- **Eigen hosting**: zet de mappen in je addons-path, werk de modulelijst
  bij en installeer via Apps.
- **Odoo Online** staat geen eigen Python-modules toe.

De AI-providers `daadit_ai_claude` en `daadit_ai_mistral` zijn submodules
naar hun eigen repository; clone met `--recurse-submodules`.

## Modules

| Map | Naam | Functie |
|---|---|---|
| `daadit_agent_activity` | DAADit AI — Agent-activiteit | Live dashboard: wat elke AI-agent nu doet, vandaag deed en later nog gaat doen |
| `daadit_agent_automation` | DAADit AI — Collega reageert op records | Een automatiseringsregel schakelt een AI-collega in wanneer een record wordt aangemaakt of gewijzigd |
| `daadit_agent_dashboard` | DAADit Agent Dashboard — targets vs realisation | A per-agent KPI board that stores the team's targets and computes realisation live from CRM, Sales, Helpdesk and Project. |
| `daadit_agent_mention` | DAADit AI — Agents reageren op vermeldingen | Tag een AI-collega in de chatter en zij pakt het op |
| `daadit_agent_voice` | DAADit AI — Praten met je agents | Spreek je AI-agents aan met je stem en laat ze hardop antwoorden |
| `daadit_ai_agent_schedule` | DAADit AI — Agent Schedules | Schedule AI agents to run on a recurring basis and log their findings and tool actions per run |
| `daadit_ai_agentic_system` | DAADit AI — Agentic System | De agentlaag van DAADit: één tool-afhandeling voor elke provider |
| `daadit_ai_customer_memory` | DAADit AI Customer Memory | Compact customer memories for AI context |
| `daadit_ai_loes` | DAADit AI — Loes.ai Provider | Add Loes.ai as an LLM provider for Odoo's built-in AI features |
| `daadit_ai_router` | DAADit — AI Router | Centrale routering van al het AI-verkeer: één punt dat elke AI-aanroep naar het juiste model stuurt, met fallback en logging. |
| `daadit_claude_design` | DAADit Claude Design | AI-tool: haal live design system uit Claude Design. |
| `daadit_process_canvas` | DAADit — Procescanvas | Visueel procescanvas: Odoo-apps als knooppunten, inzoomen op de handelingen erbinnen, input en output als lijnen. |
| `daadit_process_tooling` | DAADit — Processtooling (YoMoRo) | Native processen per klant (fasen/stappen, valideren in de portal), per-proces configuratie, template-bibliotheek en optionele externe HTML-tool. |
| `daadit_project_dashboard` | DAADit Project Dashboard — portal-safe data layer | Generic JSON endpoint that exposes project-dashboard data to internal and portal users with strict per-role field whitelisting. |
| `daadit_project_framework` | DAADit Project Framework | Rol-gebaseerde projectfasen en framework-configuratie voor DAADit |
| `daadit_social_jpeg` | DAADit Social — afbeeldingen automatisch naar JPEG | Zet PNG's automatisch om naar JPEG zodra een post naar Instagram gaat, in plaats van de publicatie te blokkeren |
| `daadit_teams_call` | DAADit Teams Call | Click-to-call via Microsoft Teams from any Odoo contact. |
| `daadit_teams_call_crm` | DAADit Teams Call — CRM bridge | Call CRM leads and opportunities from Odoo via Microsoft Teams. |
| `daadit_teams_call_helpdesk` | DAADit Teams Call — Helpdesk bridge | Call helpdesk-ticket customers from Odoo via Microsoft Teams. |
| `daadit_teams_meeting` | DAADit Teams Meeting | Microsoft Teams online meetings as default videoconferencing in Odoo Calendar. |
| `daadit_teams_meeting_appointment` | DAADit Teams Meeting — Appointment bridge | Offer Microsoft Teams as a videoconferencing option on Online Appointments. |
| `daadit_tenant_blueprint` | DAADit — Vestigingsblueprint | Exporteer en importeer de agentconfiguratie van een vestiging, zonder sleutels of tokens |
| `social_tiktok` | Social TikTok | Manage your TikTok accounts and schedule video posts |
| `daadit_ai_claude` | DAADit AI — Claude (Anthropic) Provider | Submodule: [DAADit/daadit_ai_claude](https://github.com/DAADit/daadit_ai_claude) |
| `daadit_ai_mistral` | DAADit AI — Mistral Provider | Submodule: [DAADit/daadit_ai_mistral](https://github.com/DAADit/daadit_ai_mistral) |

## Licentie

LGPL-3, zie [LICENSE](LICENSE).
