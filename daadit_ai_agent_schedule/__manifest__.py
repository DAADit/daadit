# -*- coding: utf-8 -*-
{
    "name": "DAADit AI — Agent Schedules",
    "summary": "Schedule AI agents to run on a recurring basis and log "
               "their findings and tool actions per run",
    "description": """
DAADit AI — Agent Schedules
===========================
Adds a *schedule* concept on top of Odoo 19's ``ai.agent``, for every
provider that serves the agent (Mistral, Claude, loes.ai or Odoo's own).

Features
--------
* **Schedules** — pick an AI agent, give it a standing instruction
  (prompt), choose an interval (every N minutes/hours/days/...), and the
  agent runs autonomously on that cadence. Create / edit / (de)activate
  from a dedicated list+form, or straight from the AI Agent form via a
  "Schedules" smart button.
* **Run log** — every execution produces one ``Schedule Run`` record
  containing the agent's *findings* (its written answer) plus the exact
  list of *tool actions* it performed (tool name, arguments, result,
  error flag), token usage and a link to the provider's usage row.
* **Run-as user** — each schedule carries a "Run as" user (defaults to
  its creator). Tool calls execute with that user's access rights, so
  Odoo RBAC / record rules / multi-company isolation are enforced for
  everything the scheduled agent reads.
* **Manual trigger** — "Run now" button for ad-hoc execution and
  testing.

How it runs
-----------
Schedules execute headless through the same audited provider path the
live chat uses — any model the agent form accepts (Mistral, ChatGPT/
OpenAI, Google Gemini) can be scheduled. Runs through a DAADit provider
additionally get (provider ``LLMApiService`` patch + the shared
``daadit_ai_agentic_system`` ``tool_dispatch``):
per-agent model allow/block lists, the field-level
PII blocklist, the domain-validation gate and per-tool RBAC all still
apply. No ``discuss.channel`` is created — the run is fully server-side.

A single ``ir.cron`` (default: every 15 min) scans for due schedules and
runs each in isolation so one failing schedule never blocks the others.

Rechtenreview (kwartaal)
------------------------
Een tweede cron stelt elk kwartaal een **rechtenreview** samen van de
agents met schrijf- of uitvoerrechten: rechtenniveau, schrijvende en
publicerende tools, leesscope, PII-blocklist, wat er sinds de vorige
review in die configuratie wijzigde (vergeleken met de bestaande
configuratie-momentopnamen) en hoe betrouwbaar de agent over zijn eigen
werk rapporteerde. Het rapport komt als To-Do met deadline bij de
verantwoordelijke gebruiker te liggen; het wijzigt zelf geen enkel
recht. Procedure: ``docs/RECHTENREVIEW.md``.

Drift op de promptregistry
--------------------------
De knowledge-registry is de bron van waarheid voor agent- en
planningsprompts, maar niets toonde of de *draaiende* instructie daar nog
gelijk aan is. Het gezondheidsscherm vergelijkt nu per registry-koppeling
"Blok 2 — Schedule-instructie" uit het artikel met de ``prompt`` van de
bijbehorende planning en toont hoeveel planningen afwijken, met een knop
naar die lijst. Alleen lezen: herstellen blijft het werk van de bestaande
uurlijkse sync. Een leeg blok is met opzet niet gesynchroniseerd en is
dus geen drift.

Dekking van de promptregistry (taak 748)
----------------------------------------
Drift meten zegt pas iets naast de vraag hoeveel er is gemeten. Het
gezondheidsscherm toont nu per planning of er een registry-artikel is,
per artikel of het gekoppeld is, en telt koppelingen naar een verdwenen
of uitgezette planning apart — die golden eerder als "geen drift",
terwijl er niets was vergeleken. De knop "Dekkingsrapport (dry-run)"
stelt per ontbrekend artikel voor wat erin hoort (agent, doel,
bestemming, budget) zonder iets aan te maken: publiceren blijft een
besluit van een mens.

Deploy-wachter (taak 1069)
--------------------------
Een stille storing bestaat niet meer. Elk uur vergelijkt een wachter de
versie in het manifest op schijf met de versie in de database, zoekt
modules die in een tussenstand zijn blijven hangen en crons die hun eigen
ritme missen. Elke bevinding wordt een taak op het agentbord — bewust
geen activiteit, want die valt onder de dagcap en kan stil geweigerd
worden. De wachter leest en meldt; herstellen blijft mensenwerk.

Claim tegenover effect per dag (taak 1071)
------------------------------------------
Per run werd al gemeten of een bewering door een geregistreerd effect
gedekt is. Een nachtelijke telling brengt dat naar het niveau erboven:
per dag, per collega en per model het aantal runs met een onverifieerbare
claim, het aandeel daarvan, verloren schrijfpogingen en de kosten van die
dag. De gebruikte runs staan onder elke regel, zodat elk cijfer
narekenbaar is in plaats van te vertrouwen — en zodat een goedkoper model
een afweging wordt en geen gevoel.

Kwaliteitsmeting per rol (taken 1072/739)
-----------------------------------------
Elke moduleversie en elke modelwissel verandert het gedrag van een agent
op een manier die een promptreview niet vindt. De meting stelt elke
collega een vaste reeks realistische vragen uit ``eval_cases/`` en
beoordeelt het antwoord deterministisch: taal, terugvragen wat de agent
zelf kon opzoeken, werk teruggeven aan een mens, schrijven buiten de
eigen grens, cijfers die niet uit de tooluitkomst komen, en de kosten van
de beurt. De beurten draaien in **testmodus**, dus een meting kan niets
in de database wijzigen; wat daardoor niet vast te stellen is, wordt
``niet te beoordelen`` en nooit stil ``geslaagd``.

De poort valt alleen op **verslechtering** tegenover de vorige meting:
een poort die weken rood staat wordt genegeerd, en dan meet je niets
meer. Het rapport komt als rollende taak op het agentbord, een
verslechtering als eigen taak, en sluiten doet de meting nooit zelf. De
cron staat standaard uit: beurten kosten geld, dus aanzetten is een
besluit.

Het meldpad van de assurance-keten (taak 1070)
----------------------------------------------
Het meldkanaal liep vol met verslagen: 28 open activiteiten op één
artikel, vrijwel allemaal achteraf-informatie over al afgehandeld werk.
Samen vulden ze de dagcap van dat record, waardoor een échte melding stil
geweigerd werd. Een verslag is daarom geen activiteit meer maar een regel
onder één rollende taak op het agentbord; een voorstel dat nog een
besluit vraagt blijft juist een eigen taak, ontdubbeld op zijn inhoud en
niet op het onderwerp-voorvoegsel — dat wordt onderweg herschreven.
Niets sluit zichzelf af.

De bestaande stapel kan opgeruimd worden, maar niet ongevraagd: die
opruimer staat standaard uit, kan eerst drooglopen, vat elk verslag samen
op de rollende taak en sluit het daarna af met ``action_feedback`` zodat
de tekst in de chatter blijft staan. Verwijderen doet hij nooit — dit is
historie. Alles wat géén verslag is laat hij staan: dat is werkvoorraad
van een mens. Procedure en de handmatige omzetting van de serveractie:
``docs/ASSURANCE_MELDPAD.md``.
""",
    "version": "19.0.20.17.0",
    "category": "Productivity/Discuss",
    "author": "DAADit",
    "website": "https://daadit.group",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
        "ai",
        "ai_app",
        "project",
        "daadit_ai_agentic_system",
    ],
    "data": [
        "security/ai_agent_schedule_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "data/budget_params.xml",
        "views/ai_agent_schedule_views.xml",
        "views/ai_agent_repair_scope_views.xml",
        "views/ai_agent_views.xml",
        "views/ai_health_views.xml",
        "views/ai_permission_review_views.xml",
        "views/ai_claim_telemetry_views.xml",
        "views/ai_eval_views.xml",
        "views/ai_index_run_views.xml",
        "views/ai_werkafspraak_views.xml",
        "data/ai_tools_werkafspraak.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
