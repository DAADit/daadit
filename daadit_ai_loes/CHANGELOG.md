# Changelog — DAADit AI — Loes.ai Provider

All notable changes to `daadit_ai_loes`. Versions follow Odoo's
`<odoo_major>.0.<feature>.<minor>.<patch>` scheme.

## 19.0.1.13.0 — 2026-10-07 — de toolaanroepen draaien in daadit_ai_agentic_system

- De eigen kopie van de tool-dispatch is weg; Loes gebruikt de dispatcher van `daadit_ai_agentic_system`, met dezelfde routerstatus als Mistral en Claude. Daarmee krijgt Loes ook de herstelregels die alleen in Mistral stonden (`claims`, `<x>_zoeken`).
- De tests van de resultaatbegrenzing en het domeinherstel staan nu in `daadit_ai_agentic_system`.

## 19.0.1.12.3 — 2026-10-02 — een lege lijst door de leesscope zegt dat ook

- Zelfde melding als daadit_ai_mistral 19.0.10.3.5: een door de leesscope lege zoekopdracht zegt dat expliciet (taak 820).

## 19.0.1.12.2 — 2026-10-02 — een domein met één haakje te veel komt toch door

- Zelfde herstel als daadit_ai_mistral 19.0.10.3.4: een genest domein dat één `]` te vroeg sluit wordt weer één lijst, en een domein als string in een string wordt uitgepakt (taak 775).

## 19.0.1.12.1 — 2026-10-01 — logregels op een eigen cursor (taak 1488)

- Diagnose- en toolregels in `ir.logging` worden op een eigen cursor
  geschreven in plaats van met `env.cr.commit()` op de cursor van de
  aanroeper. Een geplande run houdt zo zijn savepoint en kan bij een fout
  lokaal terugdraaien; de logregel blijft toch staan.

## 19.0.1.11.1 — 2026-09-16 — toolnaam uit de Engelse bronnaam

- De toollijst voor een chat of delegatie slugt `ir.actions.server.name`
  met `lang="en_US"`, zodat de schrijf- en orchestratorlijsten ook bij
  een Nederlandse gebruiker matchen (zelfde fix als daadit_ai_mistral
  19.0.10.0.1).

## 19.0.1.11.0 — 2026-08-18 — Korte antwoorden in spraakgesprekken

Een geschreven chatantwoord bevatte te veel rapportage om prettig handsfree
voor te lezen. Tijdens een gemarkeerde spraakbeurt stuurt Loes nu aan op
maximaal twee korte zinnen, ook bij delegatie naar een specialist. De
routerstatus geeft die spraakcontext door aan de specialist en voorkomt dat
de tussenstappen stil blijven voor de gebruiker.

## 19.0.1.10.7 — 2026-08-13 — standard-suites terug

`test_result_cap` weer in de Odoo.sh-standaardbuild. `#98` sloot hem
uit op een verkeerde diagnose (RelaxNG/registry, geen testhang).

## 19.0.1.10.6 — 2026-08-13 — dubbele registryrijen opgeruimd

Zelfde opruiming als `daadit_ai_mistral` 19.0.6.29.6. Hier ging het om een
paar: `hyai/loes-large-eu-eurollm-22b-2512` stond zowel gearchiveerd (met
het xml-id `model_loes_large_eu`) als actief, waardoor de unieke index niet
kon worden aangelegd. De actieve rij blijft staan en het xml-id wijst nu
daarnaar.

## 19.0.1.10.5 — 2026-08-13 — cost_cap op Odoo 19 _read_group

`daadit_ai_mistral` 19.0.6.29.2 verving de klassieke
`read_group(domain, fields, groupby)` in `cost_cap.daily_spend`, maar
loes bleef op de oude vorm staan — dezelfde aanroep die in productie
omviel. Nu `_read_group` met terugval op een mapped som, plus een
guard wanneer het usage-model niet geinstalleerd is. De
modelregistry gebruikt `models.Constraint` in plaats van de door
Odoo 19 genegeerde `_sql_constraints`-lijst.

## 19.0.1.10.3 — 2026-08-13 — read_group/search args, gelijk aan mistral

Zelfde reparatie als `daadit_ai_mistral` 19.0.6.29.2: JSON-gewikkelde
aggregates, date-groupby zonder granulariteit, `stage_id.is_close` →
`fold`.

## 19.0.1.10.2 — 2026-08-13 — Afkapping per veld, gelijk aan daadit_ai_mistral

De resultaatcap knipt eerst lange tekstvelden (`[AFGEKAPT]` +
`truncated_fields`) en pas daarna records. `AI: Assign User` vraagt
dezelfde schrijfscope wanneer `daadit_ai_mistral` naast deze module staat
(taken 1076/1079).

## 19.0.1.9.1 — 2026-08-03

`mail` staat nu in `depends`. Deze module breidt `mail.activity` uit,
maar leunde erop dat `mail` toevallig eerder in de laadvolgorde stond.
Die volgorde verschuift zodra er een module bijkomt: bij het installeren
van `daadit_tenant_blueprint` viel het registry-laden om met
`Model 'mail.activity' does not exist in registry` — een storing die niet
in deze module leek te zitten. Alleen de afhankelijkheid, geen
gedragswijziging.

## 19.0.1.9.0 — 2026-08-03

Zichtbare denkstappen in de chat, gelijk aan `daadit_ai_mistral`
19.0.6.17.0. Loes stuurt nu tijdens een antwoord korte, PII-vrije
voortgangsregels over de bus (per round-trip, per tool-aanroep, en een
`done`-markering aan het eind), met `turn_id`/`seq`/`depth` zodat de
frontend ze als één groeiende, terugleesbare lijst groepeert. De labels
en het bus-verkeer komen uit de gedeelde laag
`daadit_ai_agent_schedule.services.agent_steps`; deze module roept die
alleen aan.

## 19.0.1.8.1 — 2026-08-03

Neemt de zeef op het uitgaande antwoord uit `daadit_ai_mistral`
19.0.6.16.1 over: gelekt intern verkeer (de deelvraag aan een collega
en het ruwe sub-runresultaat) en een doorgeslagen staart worden van het
eindantwoord afgesneden voordat het de chat in gaat.

## 19.0.1.8.0 — 2026-08-03

Neemt de domeinfix uit `daadit_ai_mistral` 19.0.6.16.0 over: een OR die
het model in een eigen lijst wikkelt, wordt uitgevlakt naar de platte
prefixvorm. Zonder die fix leest de ORM de groep als leaf en valt de
hele aanroep om met `'list' object has no attribute 'lower'`.

## 19.0.1.7.0 — 2026-08-03

Trekt de toollaag gelijk met `daadit_ai_mistral` 19.0.6.15.0. Deze
module heeft een eigen kopie van de dispatcher, dus de drie fixes die
daar landden bestonden hier nog niet — met dezelfde gevolgen zodra een
agent op Loes draait.

- **Parameternamen (741).** Aangeleverde keys worden op de namen van het
  schema geschoven: leestekens en kapitalen, een Nederlandse alias
  (`onderwerp` → `title`), en de kale naam van een id-parameter
  (`campagne` → `campaign_id`). Alleen naar een naam die de aanroep leeg
  liet, en nooit over een key die het schema zelf declareert. De
  foutmelding noemt nu de ontvangen keys en de keys die de tool
  accepteert; de onjuiste slotzin "Do not call this tool with empty
  arguments" is weg.
- **Resultaatlimiet (744).** De cap komt uit
  `daadit_ai_loes.max_tool_result_chars` in plaats van uit een
  hardcoded 50.000, en een te groot resultaat wordt afgekapt tot de
  eerste N records met `truncated: true` plus het totaal, in plaats van
  geweigerd. Een afgekapt resultaat is bruikbaar; een weigering leverde
  alleen dezelfde query opnieuw.
- **Domein (742).** Bij een ontbrekende `domain` staat de verwachte vorm
  erbij, inclusief `{"domain": []}` voor alle records.
- **Activiteiten (746).** Een activiteit op een model zonder
  `mail.activity.mixin` wordt geweigerd met een alternatief: de chatter
  van het record als het model die heeft, en anders een `project.task`
  met het record-id in de beschrijving.

## 19.0.1.6.0 — 2026-07-30

Fixes HTTP 400 "19 validation errors" from the router: the Loes models
run on vLLM, whose pydantic schemas reject ``content: null`` outright —
one such message fails the entire call. OpenAI itself tolerates a null
content on an assistant message carrying ``tool_calls``, and stock Odoo
builds exactly that shape, so ``_sanitize_messages`` now coerces
``None`` → ``""`` on every outgoing message as a last-mile fix-up.
Empty user turns are pruned too, but never the last conversational
turn (a system-prompt-only payload would be worse).

## 19.0.1.4.0 — 2026-07-29

- The separate "Loes Models" menu under AI → Configuration is gone —
  the registry is managed from the Router's "Providers & modellen"
  page, which mirrors it live in both directions (the active-toggle
  there writes back to this registry). The action itself remains for
  the "Refresh models" button in Settings.

## 19.0.1.3.0 — 2026-07-29

- Model registry now pushes every change (API sync and manual edits)
  straight to the AI Router's "Providers & modellen" page
  (`ai.router.model._sync_from_registries`) — no waiting on the daily
  router cron; loosely coupled (no-op when `daadit_ai_router` is not
  installed). Same hook added to the Mistral and Claude registries.

## 19.0.1.2.0 — 2026-07-29

Real Loes model ids wired in, per the API docs the user pointed at
(loes.ai/#api): the router namespaces Loes under ``hyai/``.

- Seed/selection/pricing/strict-JSON lists now use ``hyai/loes-large``
  (default; World variant on Qwen3.5-27B) and
  ``hyai/loes-large-eu-eurollm-22b-2512`` (fully-EU alternative);
  the invented ``loes-*-latest`` placeholders are gone.
- ``is_loes_model`` also matches the ``hyai/loes`` prefix — without
  this the model-sync filter would have skipped every real Loes model.
- Fallback/default chat model is now ``hyai/loes-large``
  (still overridable via ICP ``daadit_ai_loes.default_chat_model``).
- Settings help card rewritten: points at loes.ai/#api and
  hostyourai.com registration (``hyai-`` keys, prepaid balance).

## 19.0.1.1.0 — 2026-07-29

Endpoint rewired to the real Loes.ai serving platform, confirmed by the
user: Loes is HostYourAI's sovereign EU-trained model, served via the
HostYourAI EU-router (hostyourai.com/#router).

- Default base URL is now the HostYourAI router (`/api/v1`,
  OpenAI-compatible); `hostyourai.com` added to the host allowlist
  (`api.loes.ai` kept as a legacy alias).
- `from_env` now reads the default from `LOES_DEFAULT_BASE_URL`
  (single source of truth in `res_config_settings.py`).
- Model sync now filters the router's 390+ model catalogue to `loes*`
  models only — prevents dropdown bloat and selection-key collisions
  with the Mistral/Claude sibling modules.
- `is_loes_model` also accepts a bare `loes` model id.
- Settings/help texts point to hostyourai.com (keys prefixed `hyai-`).
- Known cosmetic leftover: one help-card list in
  `res_config_settings_views.xml` still shows the old loes.ai
  onboarding steps.

## 19.0.1.0.0 — 2026-07-29

Initial release. Built as a 1-on-1 mirror of `daadit_ai_mistral`
v19.0.6.3.2 (same functionality, same restrictions), rebranded and
rewired for Loes.ai:

- **Provider**: routes `loes-*` models to the OpenAI-compatible
  Loes.ai API (`https://api.loes.ai/v1` by default; base URL
  configurable behind a strict https-only host allowlist).
- **Models**: seeds `loes-large-latest`, `loes-medium-latest`,
  `loes-small-latest` plus `loes-embed` for embeddings; a daily cron
  and a manual "Refresh models" button sync the live list from
  `GET /v1/models` into the `daadit.ai.loes.model` registry.
- **Restrictions** (inherited unchanged from the Mistral blueprint):
  - base-URL allowlist (https only, no IP literals, no userinfo) with
    cross-provider hints (Mistral / Anthropic / Azure / OpenAI / Google);
  - per-agent allowed/blocked model lists and field-level PII
    blocklist, enforced in tool dispatch before Odoo RBAC;
  - hard per-agent read scopes (`daadit.ai.agent.read.scope`);
  - usage logging (`daadit.ai.loes.usage`) with cost estimates and
    daily/monthly cost caps;
  - separate optional batch API key for scheduled agent runs.
- **Shared tools**: the write-side AI server actions (Assign User,
  Schedule Activity, Search Knowledge, Ask Agent) are NOT duplicated —
  they ship with `daadit_ai_mistral` and are resolved by name slug,
  following the `daadit_ai_claude` precedent.
- Pricing table placeholder mirrors the Mistral tiering — override via
  ICP once Loes.ai publishes pricing.
