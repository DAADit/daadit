# Support-runbook OAS: een klant meldt dat de AI iets niet doet

Taak 639. Dit is het document voor de dienst: iemand die niet dagelijks
in het OAS zit moet met dit blad een melding kunnen afhandelen zonder
eerst uit te zoeken hoe het systeem in elkaar zit.

Het gaat over **de melding**, niet over de nasleep. Twee documenten
liggen ernaast en worden hier niet herhaald:

- **De agent heeft iets gedaan wat niet had gemoeten** (terugdraaien,
  back-up, communicatie) → `docs/AGENT_INCIDENT.md` in deze module.
- **De klant kan zijn AI-client niet verbinden / de MCP-gateway zelf**
  (401/403/429, onbereikbare klant-Odoo, OAuth, sleutels) →
  `daadit_mcp_multi_tenant/docs/RUNBOOK.md`.

Eén regel om vast te houden: **de tekst van de agent is geen bewijs.**
Elke geslaagde schrijfaanroep staat als eigen record vast met model,
record en soort wijziging. Wat daar niet in staat, is niet gebeurd — hoe
zeker het rapport ook klinkt.

---

## 0. Vaststellen wat de klant eigenlijk meldt (≤ 5 minuten)

Vraag drie dingen en niets meer:

1. **Welke collega** (Robin, Bram, Nova, Eva, Lux, Sem, Pim, Argus …)?
2. **Wanneer**, en ging het om een **geplande run** of een **chatbeurt**?
3. **Wat zag de klant** — een foutmelding, een leeg antwoord, of een
   antwoord dat zegt dat er iets is gedaan?

Open dan **AI → Schedule Runs**, filter op de agent en het tijdvak. De
kolommen die je direct nodig hebt staan er standaard in:

| kolom / veld | wat het je vertelt |
| --- | --- |
| `state` | `Success`, `Error`, `Running` of **`Budget bereikt`** — de laatste is geen storing, zie §4 |
| `trigger` | `Scheduled` / `Manual` / `Test run` (droog: schrijfacties gesimuleerd) / `Chat` |
| `needs_attention` + `attention_reason` | door code geschreven reden waarom deze run aandacht vraagt, óók als hij op `Success` staat |
| `claims_unverified` | de tekst beweert werk dat geen enkel vastgelegd effect dekt |
| `error_action_count` | mislukte tool-aanroepen binnen de run |
| `write_attempt_count` / `write_action_count` | pogingen versus echt doorgevoerde wijzigingen |
| `duration`, `total_tokens`, `model` | duurde het te lang, en met welk model |

Vind je de run niet, gebruik dan het filter **Vraagt aandacht en nog
niet opgelost** in de zoekbalk van dezelfde lijst; en voor het beeld van
het hele team **AI → Gezondheid** (`runs_failed_24h`,
`failed_actions_24h`, `attention_24h`, `claims_24h`,
`schedules_tripped`, `schedules_overdue`). Elk getal op dat scherm is
doorklikbaar naar de onderliggende records.

Staat er helemaal geen run in het tijdvak, dan heeft de agent niet
gedraaid: ga naar §4 (planning uit, breaker geslagen, budget bereikt of
achterstand).

## 1. Bij wie ligt het: bij ons, bij het model, of bij de klant-Odoo?

Doe dit in deze volgorde. De onderste twee zijn de goedkoopste om uit te
sluiten en veroorzaken de meeste meldingen.

**a. Bij de klant-Odoo** — open de run, tabblad *Actions performed*, en kijk
naar `result` van de mislukte regel. Staat er `Cannot reach …`, een
time-out of een authenticatiefout van de klantkant, dan is het de
gateway-route: `daadit_mcp_multi_tenant/docs/RUNBOOK.md` §2, en bewijs
het met `/my/mcp/<id>/me` → *Test connection*. In de backend zie je
hetzelfde in **MCP Server → Audit log** (`success = False`,
`error_message`, `duration_ms`) gefilterd op die tenant.

**b. Bij een grens die wij zelf hebben gezet** — de tool is aangeroepen
en geweigerd. Herkenbaar aan `result`:
`{"blocked_by_scope_guard": true}`, `{"ok": false, …}` of
`{"ok": true, "skipped": true}`. In alle drie de gevallen is
`is_effective_write` op de actie `False`; dat is precies waarom een
weigering niet meer als gedaan werk meetelt. Zie §3.

**c. Bij het model** — de tool bestaat niet, het model is verzonnen, of
het antwoord is prietpraat. Signalen: `result` bevat
`There is no model named …` of `Unknown tool` (die tellen bewust niet
als schrijfpoging), `iterations` is 1 terwijl er werk gevraagd was, of
`claims_unverified` staat aan met nul effecten. Provider-kant:
**Instellingen → Technisch → Logging** (`ir.logging`, filter op
`daadit_ai_mistral.tool_dispatch`) en de verbruiksregels
(`daadit_ai_mistral.usage`: `error`, `model`, `iterations`). Liep het
over de router, dan staat de uitkomst in `ai.router.log`: `status` =
`ok` / **`fallback`** (Mistral viel uit, Claude nam over) / `error`,
met `latency_ms` en `error`.

**d. Bij ons** — pas als a, b en c niets opleveren: `state = error` met
een Python-fout in `error`, of een run die door de tijdgrens is
afgekapt (`daadit_ai_agent_schedule.max_run_seconds`, standaard 900 s).
Dan is het een taak voor Jeffrey, met run-id erbij.

---

## 2. Casus: "hij zegt dat het gedaan is, maar er is niets gebeurd"

Deze week meerdere keren gezien; run 523 is het schoolvoorbeeld (drie
schrijfacties in het rapport, nul echte wijzigingen: twee
scope-guard-weigeringen en één actie die `{"ok": true, "skipped": true}`
teruggaf).

1. Open de run. Kijk naar `claim_verification` — die regel is door code
   geschreven en zegt hoeveel beweringen er in de tekst staan
   (`claim_count`), hoeveel er door een vastgelegd effect worden gedekt
   (`claim_covered_count`) en welke niet.
2. Tabblad *Actions performed*, zoek op **Write**. De kolommen `change_kind`,
   `target_model` en `target_ref` zijn de feiten. Lege lijst = niets
   gewijzigd.
3. Antwoord aan de klant: benoem wat er *wel* is gebeurd (gelezen,
   voorbereid) en wat niet, en zet het werk opnieuw in de wacht. Beloof
   geen resultaat dat je niet in die lijst ziet staan.
4. Uitzetten hoeft hier niet: er is niets veranderd. Wat wél moet, is de
   oorzaak uit §3 of §1c wegnemen — anders komt dezelfde melding morgen
   terug.

## 3. Casus: een agent loopt tegen zijn scope-guard

Wat de klant merkt: de agent zegt dat hij "geen toegang" heeft, of levert
half werk. Wat er gebeurt: de aanroep is tegengehouden. Dat is het
systeem dat werkt, niet het systeem dat stuk is — en de fout zit dus
bijna altijd in de opdracht of in de configuratie, niet in de guard.

Waar je het antwoord vindt:

| grens | waar hij staat | wat je ziet |
| --- | --- | --- |
| Model niet toegestaan | `ai.agent` → toegestane/geblokkeerde modellen | `ir.logging`-regel `ACCESS_DENIED fn=… model=…` |
| Leesscope | `daadit.ai.agent.read.scope` (per agent, per model, met domein) | de agent ziet records niet die er wel zijn; een onparseerbaar domein sluit bewust álles af |
| PII-blocklist | veldenlijst op de agent | veld ontbreekt in het antwoord |
| Activiteitenscope | `daadit.ai.agent.activity.scope` | activiteit bij de verkeerde persoon geweigerd |
| Tenant is productie | `mcp.instance` → veld **Environment** (standaard `production`) | serverlog `TENANT-GATE refused …`; agents mogen daar alleen lezen |
| Reparatiebereik | `daadit.ai.agent.repair.scope` | Argus dient geen fix in voor een collega die niet in zijn lijst staat, en een regel telt alleen mee met een mens in `approved_by_id` |

Verruimen is een besluit, geen supportactie. Loopt een agent structureel
tegen dezelfde grens, leg dat dan vast op een taak en laat het langs de
periodieke rechtenreview (**AI → Rechtenreview**,
`daadit.ai.permission.review`) — die vergelijkt per agent wat hij mag
met wat hij erover meldt. Wijzig nooit alleen een prompt in de database:
de knowledge-registry is de bron en de uurlijkse sync draait je
wijziging terug.

## 4. Casus: de agent doet helemaal niets

Loop in deze volgorde langs **AI → Schedules** en de laatste run:

1. **`active` uit** — iemand heeft hem gearchiveerd (misschien wij, bij
   een eerder incident). Zet aan en laat één testrun lopen.
2. **Circuit breaker geslagen** — na `circuit_breaker_threshold`
   opeenvolgende mislukte runs (standaard 3) zet de planning zichzelf
   uit; `consecutive_failures` laat zien hoe hij daar kwam. Los eerst de
   oorzaak van die runs op, dan aanzetten.
3. **Budget bereikt** — laatste run staat op `Budget bereikt`, niet op
   `Error`. Dag- of maandbudget van de planning, van de agent of van de
   vestiging. Dit is een gesprek over de bundel, geen storing: kijk op
   **AI → Gezondheid** naar `units_ratio` en `overage_eur` voordat je
   iets verhoogt.
4. **Achterstand** — `schedules_overdue` op het gezondheidsscherm telt
   planningen die meer dan twee uur over hun tijd zijn. Staat de cron
   **DAADit AI: Run due agent schedules** nog aan (Instellingen →
   Technisch → Geplande acties)? Die wordt bij incidenten uitgezet en
   blijft dan uit.

## 5. Casus: de Odoo van de klant is onbereikbaar

Symptoom bij ons: runs met `error_action_count > 0` en `Cannot reach …`
in `result`, of een cluster `success = False` in de MCP-audit log van
één tenant. Wij worden hier **niet** automatisch op gealarmeerd (bekende
lacune, zie de RUNBOOK van de gateway), dus dit komt meestal via de
klant binnen.

1. Bewijs de kant: `/my/mcp/<id>/me` → *Test connection*. Faalt die, dan
   is het de klantkant en volg je `daadit_mcp_multi_tenant/docs/RUNBOOK.md`
   §2 (Odoo down, of de API-sleutel van het lid verlopen).
2. Zet in de tussentijd de planningen uit die op die tenant werken. Elke
   run kost anders tokens en levert niets op, en de mislukte runs tikken
   de circuit breaker aan.
3. Wil je de tenant volledig afsluiten (bijvoorbeeld bij verdenking van
   misbruik): `/my/mcp/<id>` → Archiveren. Dat trekt bearer- en
   OAuth-toegang direct in.

## 6. Wat zet je uit — in oplopende zwaarte

Kies de kleinste maatregel die de klacht stopt, en leg de keuze vast op
de taak.

| maatregel | waar | gevolg |
| --- | --- | --- |
| Eén planning archiveren | AI → Schedules → *Active* uit | alleen dit werk stopt |
| Tool uit het onderwerp halen | onderwerp van de agent | de agent kán het niet meer; het enige middel dat niet op discipline van een taalmodel leunt |
| Concept-only | de agent maakt concepten, een mens verstuurt | klant houdt de output, wij het risico niet |
| Alle geplande werk stoppen | cron *DAADit AI: Run due agent schedules* uit | chat blijft werken |
| Tenant archiveren | `/my/mcp/<id>` → Archiveren | alle toegang van die klant weg |

## 7. Wat communiceer je

Kort, feitelijk, en alleen wat je in de records hebt gezien.

- **Aan de klant**: wat er is misgegaan in gewone taal, of er iets in
  zijn systeem is gewijzigd (uit de lijst met effecten, niet uit de
  tekst van de agent), wat wij nu doen, en wanneer hij iets hoort.
  Beloof geen tijdstip dat van de klant zelf afhangt (§5).
- **Niet doen**: doorsturen van modeltekst als verklaring, en
  toezeggen dat "het niets heeft geraakt" voordat je de
  schrijfacties hebt gezien.
- **Intern**: een taak met run-id, welke van de vijf casussen het was,
  welke maatregel uit §6 is genomen en of die nog aanstaat. Dat laatste
  is de regel die het vaakst wordt vergeten: een planning die na een
  melding uit blijft staan, is de melding van volgende week.
- **Escaleren**: data van meerdere klanten of twijfel daarover, of iets
  wat naar buiten is gegaan → direct Nick (en behandel het als
  datalek-verdenking, `daadit_mcp_multi_tenant/docs/RUNBOOK.md` §6).
  Code en deploys → Jeffrey.
