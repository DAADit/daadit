> Tests: `test_vestiging_package` is tagged `-standard`/`daadit_heavy` (Odoo.sh memory).

# Runbook — een vestiging opnieuw opzetten

Doel: kunnen aantonen dat een tweede klantomgeving dezelfde
configuratie **en dezelfde grenzen** krijgt, zonder dat er een sleutel of
een klantgegeven meereist.

## 1. Exporteren

Instellingen → *Vestigingsblueprint* → **Exporteren**. In het veld
`Blueprint` staat de JSON; de samenvatting noemt hoeveel collega's,
onderwerpen, planningen, klantomgevingen en instellingen erin zitten.

Wil je er later naar terug kunnen, klik dan **Bewaren als pakket**. Dat
legt dezelfde JSON vast als *configuratiepakket* met een oplopend nummer
per vestiging, een tijdstip en de maker (Instellingen →
*Configuratiepakketten*). Is de configuratie gelijk aan het laatste
pakket, dan komt er geen tweede bij: dat pakket ís de stand van nu. De
nachtelijke cron doet hetzelfde, automatisch.

Wat er in zit:

| Sectie | Inhoud |
| --- | --- |
| `topics` | naam, beschrijving, instructies, toolnamen |
| `agents` | naam, model, subtitel, kostenplafonds, PII-blocklist, onderwerpen |
| `agents[].read_scopes` | per model de leesscope met domein |
| `agents[].activity_scopes` | per model het activiteitenbereik |
| `agents[].repair_scopes` | reparatiebereik per doelcollega, zonder goedkeuring |
| `agents[].read_scope` / `blocked_models` | model-allowlist en -blocklist |
| `schedules` | planning per collega met interval en budgetten |
| `tenants` | per `slug`: omgeving (dev/staging/productie), plan, endpointmodus, limieten, capabilities, model-allowlist en -blocklist, veld-blocklist |
| `parameters` | plan-, fair-use- en kostenparameters op een allowlist |

Wat er **niet** in zit, en waarom: url, database, gebruikersnaam, API-key,
bearer-token en webhook-secret horen bij de omgeving, niet bij de
configuratie. Eigenaar, leden, erkenning, verwerkersovereenkomst,
auditregels en tellers zijn klant- of bewijsgegevens. Ids ontbreken:
die betekenen niets in een andere database. De export breekt af zodra
een sleutelnaam op een geheim wijst — een vergeten instelling is een
ongemak, een gelekte sleutel een incident.

## 2. Voorbereiden van de tweede omgeving

Maak daar eerst zelf aan wat een sleutel of endpoint nodig heeft:

1. de tenant (`mcp.instance`) met url, database, gebruiker en API-key;
2. de modules die de blueprint veronderstelt (zonder de app bestaat het
   model, en dus de scope, daar niet).

De `slug` in de blueprint hoort bij de bronomgeving. Pas hem aan naar de
slug van de doeltenant vóór het toepassen; anders meldt het verslag dat
die tenant daar niet bestaat en gebeurt er niets.

## 3. Proef en toepassen

Plak de JSON, klik eerst **Proef toepassen**: het verslag zegt regel voor
regel wat er zou veranderen zonder iets te schrijven. Daarna
**Toepassen**.

Grenzen van het toepassen, bewust:

- een collega die daar niet bestaat wordt gemeld, niet aangemaakt —
  zonder zijn serveracties zou hij lijken te werken en niets doen;
- een tenant wordt nooit aangemaakt (zie stap 2);
- een planning komt er **uitgeschakeld** in: een agent die door een
  import begint te draaien is een agent die niemand heeft aangezet;
- een reparatiebereik komt er zonder goedkeurder in en werkt dus nog
  niet; een mens moet het daar aanzetten;
- prompts gaan mee in de export maar worden niet teruggeschreven: de
  Knowledge-registry is de bron en zet de database elk uur terug;
- een leesscope of activiteitscope die in de doelomgeving bestaat maar
  niet in de blueprint staat, wordt uitgezet — anders mag de tweede
  omgeving stilzwijgend méér dan de eerste.

## 4. Controleren

Exporteer op de doelomgeving opnieuw en vergelijk de secties `agents`,
`schedules` en `tenants` met de bron. Verschillen die overblijven zijn
per definitie de dingen die bij de omgeving horen (naam, slug) of die een
mens moet aanzetten (planning actief, reparatiebereik goedgekeurd).

De nachtelijke momentopname (`daadit.config.snapshot`) gebruikt dezelfde
blueprint: na het toepassen legt de eerstvolgende opname de nieuwe
configuratie met een diff vast.

## 5. Terugrollen naar een eerder pakket

Instellingen → *Configuratiepakketten*. Zoek het pakketnummer van de
stand waar je naar terug wil (de kolom *Wijzigingen* zegt wat er in dat
pakket veranderde ten opzichte van het vorige), open het en:

1. **Proef terugrollen** — het pakket wordt door dezelfde importroute
   gehaald zonder te schrijven. Het resultaat blijft op het pakket staan
   in *Proef bij terugrollen*.
2. Lees de proef. Staat er iets in dat je niet wil, dan rol je niet
   terug: een terugrol is een import en volgt dus dezelfde grenzen als
   stap 3.
3. **Terugrollen** — nu echt. Wat de terugrol feitelijk veranderde, door
   wie en wanneer staat daarna op het pakket (*Terugrolverslag*,
   *Teruggerold op/door*).
4. Zet daarna zelf weer aan wat een mens moet aanzetten: planningen
   staan na een terugrol **uit**, ook als ze in het pakket aan stonden.

Een pakket bevat nooit een sleutel: bij het bewaren gaat de inhoud langs
dezelfde twee netten als de export (allowlist plus naamcontrole), dus ook
een pakket dat via de ORM of een migratie ontstaat kan geen
`mistral_key` dragen.

## 6. Procedure: nieuwe vestiging inrichten

De checklist die het importverslag zelf ook geeft. Het verslag splitst in
twee blokken: *Toegepast* (wat er nu staat) en *Dit moet een mens nog
zetten* — dat tweede blok is deze lijst, toegespitst op wat er in die
database daadwerkelijk ontbreekt.

1. **Modules installeren.** Elke app die de blueprint veronderstelt.
   Ontbreekt een model, dan bestaat de scope erop daar niet en meldt het
   verslag het model.
2. **Providersleutels zetten.** Zonder `daadit_ai_mistral.mistral_key`
   (of de sleutel van de provider van dat model) doet de collega niets,
   hoe volledig hij er ook uitziet. Het verslag noemt de parameternaam
   en welke collega's erop wachten; het citeert nooit een waarde.
3. **Klantomgeving invullen.** `mcp.instance` met url, database,
   gebruiker en API-sleutel van de klant-Odoo. Die reizen niet mee en
   worden ook nooit door een import aangemaakt.
4. **Collega's laten bestaan.** Een collega uit het pakket die daar niet
   bestaat wordt gemeld, niet aangemaakt. Maak hem aan (of installeer de
   module die hem levert) en pas het pakket opnieuw toe.
5. **Tools controleren.** Tools zijn serveracties uit modulecode. Meldt
   het verslag een ontbrekende tool, dan mist het onderwerp die tool —
   installeer de module, pas opnieuw toe.
6. **Slug goedzetten.** Zie stap 2 van dit runbook.
7. **Opnieuw toepassen tot het verslag niets meer wijzigt.** Een tweede
   toepassing die niets verandert is het bewijs dat de vestiging staat.
8. **Reparatiebereik goedkeuren.** Komt zonder goedkeurder binnen en
   werkt niet tot een mens het daar aanzet.
9. **Planningen aanzetten.** Als laatste, en pas nadat 1–8 klaar zijn.
10. **Pakket bewaren.** *Bewaren als pakket* op de nieuwe vestiging, zodat
    er vanaf dag één een genummerd terugrolpunt is.

Wat een import per definitie niet kan: het endpoint, de database en de
sleutel van de klant-Odoo meenemen, de providersleutels meenemen, en de
tools aanmaken die als serveractie in modulecode staan. Zolang die
ontbreken doet de betrokken collega niets — dat is precies wat het
verslag zegt, zodat het geen stille fout wordt.

## 7. Stand van de verificatie op productie (10-08-2026)

De module stond op `daadit.odoo.com` geïnstalleerd als 19.0.3.0.0 (vóór de vestigingspakketten in 19.0.4.0.0). Tegen de
echte productieconfiguratie is nagegaan of de export daar iets zinnigs
oplevert in plaats van stilzwijgend leeg te lopen — dat is het echte
risico bij een export die ontbrekende velden overslaat:

- elk veld en elke relatie die de export gebruikt bestaat daar:
  `ai.agent` (inclusief `daadit_read_scope_ids`,
  `daadit_activity_scope_ids`, `daadit_repair_scope_ids`), `ai.topic`,
  `daadit.ai.agent.schedule` en `mcp.instance` — geen enkel veld uit de
  allowlists ontbreekt;
- er is echte inhoud om te beschrijven: 29 collega's, 26 onderwerpen,
  27 planningen, 15 klantomgevingen;
- van de negen geëxporteerde parameterprefixen leveren er zes
  daadwerkelijk waarden op (10 parameters, plan/fair-use/kostenplafond).
  Geen van die namen valt in de geheimencontrole, dus de export breekt
  daar niet op af. De drie prefixen zonder match zijn ongebruikte
  instellingen, geen fout.

Wat een muisklik in productie vraagt en dus niet vanaf hier is gedaan:
één keer **Exporteren** en op een tweede omgeving **Proef toepassen**.
De gateway staat `build_blueprint` niet toe via `execute_kw` (alleen
`create`, `write`, `message_post` staan op de allowlist van de eigen
tenant), en dat blijft zo — een uitzondering voor gemak hoort niet in
een productie-allowlist.
