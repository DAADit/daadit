# Spike — Claude/Anthropic als provider in de AI Router

*R&D-spike, 18-07-2026. Aanleiding: de Native AI-strategie noemt "Claude als
primair model", maar alle operationele agents draaien op Mistral en de router
kende geen Anthropic-provider. Deze spike onderzoekt wat nodig is en levert een
werkende proof-of-concept.*

## Bevinding: de router-kern is al provider-agnostisch

`ai.router.run(purpose, messages)` kiest een route → provider → model, roept
`provider._chat(...)` aan, logt latency/tokens/status en valt bij een fout terug
op de fallback-provider. **In die hele lus zit géén provider-specifieke code**
(geen `if code == 'mistral'`). Alle providerkennis zit in één bestand:
`models/ai_router_provider.py`.

Gevolg: een nieuwe provider toevoegen raakt alleen de provider-laag + de
data-seeding. Geen wijziging aan routes, dispatch, logging of de aanroepende
modules.

## Waarom config-only niet kon

`ai.router.provider.code` was een `Selection` met exact `mistral / openai /
google`. Zonder code-wijziging is er geen `anthropic`-waarde selecteerbaar —
een provider aanmaken via de UI/data was dus onmogelijk. Dít was de echte
blokkade, niet een ontbrekende sleutel of URL.

## Het verschil dat er toe doet: Messages-API ≠ OpenAI-compat

Mistral, OpenAI en Gemini spreken allemaal het OpenAI `chat/completions`-schema
(Gemini via zijn compat-laag), dus deelt de router één `_chat`. Anthropic wijkt
op drie punten af:

| | OpenAI-compat (bestaand) | Anthropic Messages-API |
|---|---|---|
| Endpoint | `/v1/chat/completions` | `/v1/messages` |
| Auth-header | `Authorization: Bearer <key>` | `x-api-key: <key>` + `anthropic-version` |
| System-prompt | rol `system` in `messages[]` | apart top-level `system`-veld |
| `max_tokens` | optioneel | **verplicht** |
| Antwoord | `choices[0].message.content` | `content[]`-blokken (`type=text`) |
| Tokens | `usage.prompt_tokens / completion_tokens` | `usage.input_tokens / output_tokens` |

Anthropic biedt óók een OpenAI-compat endpoint, maar dat is een shim met
beperkingen. Voor een provider die de lange termijn moet halen is de **native
Messages-API** de juiste keuze — en het is maar ~35 regels.

## Wat deze PoC wijzigt (branch `claude/ai-router-anthropic-provider`)

1. **`code`-selection** uitgebreid met `('anthropic', 'Anthropic Claude')`.
2. **`_DEFAULT_URLS`** + `_ANTHROPIC_VERSION` + **`_CONFIG_PARAMS`** (sleutel via
   provider-veld `api_key` of config-parameter `daadit_ai_router.anthropic_key`).
3. **`_endpoint()`** short-circuit voor anthropic (→ `/v1/messages`).
4. **`_chat()`** vertakt naar nieuwe **`_chat_anthropic()`**: extraheert
   system-prompts naar het aparte veld, zet de juiste headers, en normaliseert
   het antwoord terug naar hetzelfde return-contract
   (`{'content', 'usage': {'prompt','completion'}}`) — zodat de router-laag
   agnostisch blijft.
5. **Data-seeding**: provider `Anthropic Claude` (inactief) + modellen
   Opus 4.8 / Sonnet 5 / Haiku 4.5.
6. **Versie** 19.0.2.1.1 → 19.0.2.2.0 (feature = MINOR).

Bewust géén gedragsverandering: de provider staat **inactief** (`active=False`),
net als OpenAI/Gemini. Niets gaat vanzelf via Claude draaien.

## Zo zet je het live (na review)

1. Module upgraden op staging (versie-bump triggert dit op Odoo SH).
2. `api_key` op de Anthropic-provider zetten (of de config-parameter).
3. Provider activeren; de `_check_active_configured`-constraint eist een sleutel.
4. Rooktest: `env['ai.router.provider'].browse(<id>)._chat('claude-sonnet-5',
   [{'role':'user','content':'ping'}], max_tokens=16)` → verwacht `content`
   gevuld en `usage.prompt/completion` niet leeg.
5. Pas dán een route/fallback op Claude zetten (bv. `default`-route fallback →
   Anthropic), zodat je meteen de governance-eis "fallback" invult.

## Open punten voor productie (buiten deze spike)

- **Sleutelbeheer**: ai_app kent geen native Anthropic-sleutel; nu via
  provider-veld of config-parameter. Overweeg een klein settings-blok analoog
  aan `daadit_ai_mistral`.
- **`ai.agent`-koppeling**: de chat-agents (Robin, Hilda, …) draaien via Odoo's
  native AI + `daadit_ai_mistral`, niet via `ai.router`. Deze router bedient nu
  vooral classificatie/werkregels. Agents óók op Claude laten draaien is een
  aparte vraag (Odoo's `ai.agent`-modelselectie), niet opgelost door deze spike.
- **Kosten/limieten**: cost-cap en token-logging bestaan al op routeniveau; check
  dat Anthropic-usage correct wordt gelogd (input/output-mapping is gedaan).
- **Streaming / tool-use**: niet meegenomen; de router doet enkel synchrone
  chat-calls.

## Verificatiestatus

- `python -m py_compile` op de gewijzigde provider ✓
- XML well-formed check op de data-seeding ✓
- **Niet** end-to-end getest tegen de live Anthropic-API (vereist sleutel +
  staging). Dat is stap 4 hierboven.
