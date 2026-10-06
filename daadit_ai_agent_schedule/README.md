# DAADit AI — Agent Schedules

Schedule Odoo 19 `ai.agent`s to run autonomously on a recurring basis,
and log each run's **findings** and **tool actions**.

## What it adds

| Model | Purpose |
|---|---|
| `daadit.ai.agent.schedule` | Agent + standing instruction + interval + "run as" user |
| `daadit.ai.agent.schedule.run` | One execution: findings, tokens, cost, status |
| `daadit.ai.agent.schedule.run.action` | One tool call within a run (name, args, result, error) |
| `daadit.ai.permission.review` | Kwartaalrapport over de agents met schrijf-/uitvoerrechten |
| `daadit.ai.permission.review.line` | Eén agent in die review, plus de uitkomst van de reviewer |

UI lives under the **AI** app menu:

- **AI → Schedules** — create / edit / (de)activate schedules, "Run now".
- **AI → Schedule Runs** — the log: per run the agent's written answer
  ("Findings") and every tool action it performed ("Actions performed").
- **AI → Rechtenreview** — de kwartaalreview van de uitvoerende agents;
  procedure in [`docs/RECHTENREVIEW.md`](docs/RECHTENREVIEW.md).

Voor de dienst: een klantmelding afhandelen (waar ligt het aan, wat zet
je uit, wat communiceer je) staat in
[`docs/SUPPORT_RUNBOOK.md`](docs/SUPPORT_RUNBOOK.md); een agent die iets
deed wat niet had gemoeten, in
[`docs/AGENT_INCIDENT.md`](docs/AGENT_INCIDENT.md).

Schedules are also listed directly on the **AI Agent form** (a
"Schedules" section), so you can add/edit them from the agent.

## How runs execute

Runs go through the **same audited Mistral path** as live chat
(`daadit_ai_mistral` `LLMApiService` patch → `tool_dispatch`). No
`discuss.channel` is created — fully server-side. Every security gate
still applies: per-agent model allow/block lists, the field-level PII
blocklist, the domain-validation gate, and per-tool RBAC (evaluated as
the schedule's **Run as** user).

A single `ir.cron` ("DAADit AI: Run due agent schedules", default every
15 min) claims due schedules (advancing `Next Run` *before* running so a
slow run is never double-picked) and runs each in isolation.

### Tool capture

`services/run_capture.py` installs a transparent wrapper around the
shared `daadit_ai_agentic_system` `tool_dispatch.run_tool_call`. It only
records while a scheduled run is active on the thread — interactive chat
is unaffected. Token / iteration counts and estimated cost are linked
from the provider usage row the run creates (`usage_model` +
`usage_row_id`).

Read-only classification (`is_write`) is resolved by `readonly_tool_names(env)`:
the stock English slugs, plus the slug of every read-only
`ir.actions.server` in each installed language (a Dutch agent calls
`AI: Get Fields` as `ir_actions_server_velden_oproepen`), plus the
`daadit_ai_agent_schedule.readonly_tools` parameter.

### Naverificatie van beweringen (taak 707)

Na elke run vergelijkt de code de rapporttekst met de vastgelegde
effecten op `daadit.ai.agent.schedule.run.action`. Er komt geen tweede
LLM-aanroep aan te pas: per regel wordt een werkwoord gezocht dat op een
doorgevoerde wijziging duidt ("aangemaakt", "bijgewerkt", "toegewezen",
"gepubliceerd", "verstuurd", "ingepland") plus het aantal dat daar
direct bij hoort ("drie taken bijgewerkt"). Ontkenningen ("niets
aangemaakt"), voornemens ("wordt morgen gepubliceerd"),
recordverwijzingen ("taken 5, 6 en 7") en tabelregels vallen af.

Het resultaat staat in structurele velden op de run: `claim_count`,
`claim_covered_count`, `claim_verification` (de door code geschreven
verificatieregel, met model en record-id van het bewijs) en het
bestaande `claims_unverified` / `needs_attention`. Op **AI → Schedule
Runs** staat er een kolom en een filter *Bewering niet verifieerbaar*.

Bewust conservatief: een gemiste bewering is beter dan een valse
beschuldiging. Wat deze aanpak per definitie niet ziet: beweringen in
andere woorden dan de lijst, in het Engels, over de aard van een
wijziging (het juiste record, de juiste waarde) en werk dat buiten de
tool-dispatch is gedaan.

### Reparatiebereik per agent (taak 727)

Wie incidenten van collega's analyseert en fixes indient (bij DAADit:
Argus) kon dat in de praktijk maar bij twee collega's, en die grens zat
in de tenantconfiguratie: de leesscope op `knowledge.article`, de tools
van zijn onderwerp en de artikelen waar hij mocht schrijven. Zo'n grens
is niet op te zoeken en niet bewust te verruimen.

`daadit.ai.agent.repair.scope` maakt hem expliciet: één regel per
combinatie *reparerende agent → te repareren collega*, met optioneel het
id van het promptartikel en verplicht een mens in **Aangezet door**.
Zonder die gebruiker telt de regel niet mee, dus een agent kan zijn eigen
bereik niet vergroten. De lijst staat op de agentkaart en onder
**AI → Reparatiebereik**.

De poort zit in `services/run_capture.py` (`_repair_scope_refusal`), vóór
de dispatch: een schrijfaanroep op `ai.agent` of op een
knowledge-artikel identificeert de collega die wordt aangepast. Twee
bewuste eigenschappen:

- **Standaard verandert er niets.** Een agent zonder regels valt buiten
  de poort — de stand van vandaag. Vanaf de eerste regel is de lijst
  uitputtend.
- De poort weigert alleen wat hij kan *identificeren*: lezen blijft vrij
  (incidenten onderzoeken hoort bij het werk) en een aanroep waarvan het
  doel niet vast te stellen is, gaat door. Een weigering komt terug als
  `{"ok": false, "blocked_by_repair_scope": true}` en telt daardoor in de
  bestaande effectlaag als "niets gewijzigd", niet als fout.

### "Inmiddels opgelost" als feit (taak 727)

De restlijst beoordeelde zelf of een fout al was opgelost, en run 571
voerde daarvoor vier runs op die alle vier *vóór* de foutieve run
liepen. `resolved_by_run_id` / `resolved_on` / `is_resolved` stellen dat
nu in code vast: de eerste **latere** geslaagde run van dezelfde planning
die zelf geen aandacht vraagt. Stond `claims_unverified` op de run, dan
blijft het veld leeg — en een oplosser met `claims_unverified` telt ook
niet mee. Filters in de runlijst: *Inmiddels opgelost*, *Nog niet
opgelost* en *Vraagt aandacht en nog niet opgelost* (dat laatste is de
restlijst van Argus), zodat een agent het kan opvragen in plaats van
concluderen.

Sinds `19.0.12.0.0` zijn de velden **opgeslagen**, zodat je erop kunt
filteren en groeperen zonder elke rij te berekenen. Dat vraagt één ding
extra: een run lost een *ander* record op, en die afhankelijkheid leidt
Odoo niet uit `@api.depends` af. `create()` en `write()` roepen daarom
`_recompute_resolution_of_earlier_runs()` aan voor de eerdere runs van
dezelfde planning — een verouderd opgeslagen feit is erger dan geen
feit. De migratie `migrations/19.0.12.0.0/end-compute_resolution.py`
vult de bestaande runs van het laatste kwartaal eenmalig.

### Drift op de promptregistry (taak 707)

De knowledge-registry is de bron van waarheid voor prompts en een
uurlijkse sync in de tenant schrijft die terug naar agent en planning.
Wat niemand kon zien: of de tekst die op dit moment *draait* daar nog
gelijk aan is. Tussen twee synchronisaties, na een sync die op een fout
afbrak of na een handmatige wijziging in de database is dat niet zo — en
dan is de gepubliceerde grens niet de gebruikte grens. Op 3-8-2026 moest
dat twee keer met de hand worden rechtgezet (artikel 343 ↔ planning 42,
artikel 344 ↔ planning 44).

Het gezondheidsscherm (**AI → Gezondheid**) heeft daarvoor
*Registry-koppelingen*, *Planningen met drift*, *Welke drift* (met per
planning waar de teksten uit elkaar lopen) en *Koppelingen zonder doel*,
plus een knop naar de lijst met afwijkende planningen. Drift geeft *let
op*, geen alarm: er staat niets stil en het kost geen geld, maar een
bewering over governance is niet meer aantoonbaar.

`services/prompt_registry.py` doet het werk en leest twee dingen bewust
uit de bestaande sync in plaats van ze zelf te bedenken: de koppeling
staat in de systeemparameter `daadit_prompt_registry.map`
(`artikel:agent:planning`, gescheiden door `;`) en de blokken worden op
dezelfde manier uit de body gehaald (`<pre>` in volgorde, `<br>` wordt
een regeleinde, `&lt;`/`&gt;` worden gewone tekens). Verschillen die
alleen uit witruimte bestaan zijn geen drift; een **leeg** blok 2 is met
opzet niet gesynchroniseerd en dus ook geen drift.

De controle schrijft nooit: herstellen blijft het werk van de sync, of
van een mens die het artikel aanpast. Wat hij niet ziet: drift in blok 1
(de system-prompt van de agent staat in de meeste artikelen met opzet
leeg) en of de gepubliceerde tekst zelf *goed* is.

### Dekking van de promptregistry (taak 748)

Drift meten zegt pas iets als je erbij weet hoeveel er is gemeten. Een
planning die niet in `daadit_prompt_registry.map` staat, kan per
definitie geen drift hebben; "nul drift" leest dan als gerustheid die
niemand heeft verdiend. Twee stille varianten daarvan zijn nu een eigen
uitkomst in plaats van *gelijk*:

- een koppeling naar een planning die **niet meer bestaat**
  (*Koppelingen zonder doel*);
- een koppeling naar een planning die **uit staat** (*Koppelingen naar
  uitgezette planning*) — het artikel is gepubliceerd, maar er draait
  niets om mee te vergelijken.

Het gezondheidsscherm toont daarnaast *Planningen met registry-artikel*
en *Planningen zonder registry-artikel* (met een knop naar die lijst) en
*Artikelen zonder koppeling*: artikelen in de registry-map waar geen
enkele entry naar wijst, dus wel gepubliceerd maar niet in gebruik. Waar
die map staat, leidt de code af uit het gedeelde bovenliggende artikel
van de gekoppelde artikelen — verhuist de registry, dan verhuist de
controle mee, zonder tweede instelling die kan verlopen.

De knop **Dekkingsrapport (dry-run)** geeft per ontbrekend artikel een
voorstel: agent, doel (planning en interval), bestemming (titel onder de
registry-map plus de entry die in de systeemparameter hoort), budget per
dag en per maand, en de tekst die op dit moment draait. Bewust alleen
tekst: welke instructie gepubliceerd wordt is een besluit van een mens.
Automatisch artikelen genereren zou van de registry een afgeleide van de
database maken, terwijl het net andersom hoort.

### Kwaliteitsmeting per rol (taken 1072/739)

Een promptreview vindt niet wat een modelwissel met het gedrag van een
agent doet. **AI → Kwaliteitsmetingen** stelt elke collega een vaste
reeks realistische vragen uit `eval_cases/<rol>.json` en beoordeelt het
antwoord deterministisch: taal, terugvragen wat de agent zelf kon
opzoeken, werk teruggeven aan een mens, verplichte en verboden inhoud,
verplichte en verboden tools, schrijven buiten de eigen grens, cijfers
die niet uit een tooluitkomst komen, ongedekte beweringen, iteraties en
kosten. De regels staan in `services/eval_scoring.py`.

Drie ontwerpkeuzes die de meting bruikbaar houden:

- **De beurt draait via de gewone runtime**, niet via een tweede route:
  de meting zet de prompt op een eigen, uitgezette planning per agent en
  roept `_execute(trigger="test")` aan. De uitkomst is dus een echte
  `daadit.ai.agent.schedule.run` met toolaanroepen, argumenten en
  resultaten — dat is het bewijs onder elke case, en het staat onder
  elke uitkomst gelinkt. Die planning draait met de gebruiker van het
  échte werk van die agent: meten onder ruimere rechten meet een agent
  die niet bestaat.
- **Testmodus.** Leestools werken echt, schrijftools worden onderschept.
  Een meting kan dus niets in de database wijzigen. Wat daardoor niet
  vast te stellen is (landde een verwachte wijziging echt?) wordt *niet
  te beoordelen* en nooit stil *geslaagd*. Eén criterium is juist
  strenger: wie niets mag schrijven, zakt op de **poging** — dat de
  vangrail het tegenhield maakt de poging geen detail.
- **De poort valt op verslechtering**, niet op een case die al rood was.
  Een poort die weken rood staat wordt genegeerd, en dan meet je niets
  meer. Een case die niet in de vorige meting stond kan niet
  verslechteren, zodat een nieuwe case toevoegen veilig is.

Het rapport komt als rollende `[Eval]`-taak op het agentbord (dezelfde
instelling als de deploy-wachter gebruikt), een verslechtering als eigen
`[Eval-poort]`-taak. Sluiten doet de meting nooit zelf: of een
kwaliteitsdaling acceptabel is, is een menselijk besluit — de les van
taak 773. Onderaan elk rapport staat wat de meting **niet** ziet,
inclusief de belangrijkste beperking: ze draait in dezelfde database als
het werk dat ze beoordeelt. Het losse harnas in `daadit_odoo/evals`
gebruikt dezelfde casebestanden en dezelfde scoringregels en is de
onafhankelijke tegenhanger; wijzigt een regel of een case, dan horen
beide kanten mee te bewegen.

De cron *"DAADit AI: Kwaliteitsmeting per rol"* staat **standaard uit**:
elke case is een echte agentbeurt en kost geld, dus aanzetten is een
besluit. Aan gezet meet hij per ronde de rol die het langst niet gemeten
is; `daadit_ai_agent_schedule.eval_roles` beperkt welke rollen meedoen en
`.eval_roles_per_run` hoeveel rollen één ronde meet (standaard één).

## Requirements / scope

- Depends on `daadit_ai_mistral`. The scheduled agent **must** use a
  Mistral LLM model; non-Mistral agents produce a clear error run.
- Only the standard `ai.agent._ai_tool_*` tools (Search, Read group,
  Get Fields, Open Menu *, …) are dispatchable via the Mistral path, so
  those are the actions a scheduled run can perform and log. Custom
  write server actions on other models are not dispatched by
  `daadit_ai_mistral` and are skipped (logged for visibility) — enabling
  them is a provider/topic concern, not part of this module.

## Odoo.sh memory (tests)

Heavy suites (`test_eval`, and the mirrored drills in MCP / blueprint)
are tagged `-standard` / `daadit_heavy`. Odoo.sh default builds skip
them; run explicitly with `--test-tags=daadit_heavy` on a larger
worker if you need the full suite. Claim telemetry only fetches the
count columns of a day's runs (not findings/error blobs), and the
deploy watchdog only compares `daadit_*` module versions.

## Deploy (Odoo.sh)

1. Commit this module to the Odoo.sh branch addons path.
2. Update the apps list / install **DAADit AI — Agent Schedules**
   (`daadit_ai_agent_schedule`).
3. Grant the **AI Agent Schedules: Manager** group (implied automatically
   for Settings/admin users).

## Verify after deploy

- **AI → Schedules → New**: pick a Mistral agent, set instruction +
  interval, save, **Run now**. Inspect the resulting run: Findings tab
  + Actions performed tab.
- **AI → Schedule Runs**: confirm the run row, token/cost link, and
  per-action arguments/result.
- Cron: *Settings → Technical → Scheduled Actions →* "DAADit AI: Run due
  agent schedules" — tune interval or disable without a module upgrade
  (record is `noupdate`).
