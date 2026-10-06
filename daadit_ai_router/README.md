# daadit_ai_router

**DAADit — AI Router** · Odoo 19 · LGPL-3

Eén centrale module waar **al het AI-verkeer binnen Odoo doorheen loopt**.
Geen losse AI-aanpak meer per module of agent: één punt dat ontvangt, beslist,
verstuurt en logt.

## Fasering

| Fase | Wat | Status |
|---|---|---|
| **1. Model-router (gateway)** | Alle AI-aanroepen via één service naar het juiste model, met fallback en logging | ✅ |
| **2. Werk-router** | Werkregels: nieuwe CRM-leads classificeren via de gateway en als concept-To-Do bij de juiste collega leggen (draft-only, idempotent, gethrottled) | ✅ v2.0.0 — bron: CRM-leads; tickets/mail volgen |

## Automatische, kostenefficiënte routering (v3)

Routes staan standaard op **Automatisch**: de router bepaalt per aanroep de
vereiste capaciteitsklasse (licht / standaard / zwaar) op prompt- en
outputlengte, en kiest daarbinnen het **goedkoopste actieve model** (op basis
van de indicatieve prijzen per model, bewerkbaar in de modellenlijst). Faalt
een model, dan escaleert hij automatisch naar de volgende kandidaat of een
hogere klasse. Het logboek toont per aanroep de routeringskeuze
(`auto/light`, `auto/heavy`, `vast`). Jouw toggles bepalen de kandidatenpool;
een route op **Vast model** behoudt het klassieke gedrag. Aanroepers kunnen
de klasse forceren met `opts['tier']`.

## Hoe het werkt (fase 1)

```
aanroepende module ──> env['ai.router'].run(purpose, prompt)
                              │
                        ai.router.route   (welke provider/model voor dit doel?)
                              │
                        ai.router.provider (Mistral / ChatGPT / Gemini —
                                            de AI-diensten uit Instellingen → AI)
                              │  └── fout? → fallback-provider
                              ▼
                        ai.router.log     (status, latency, tokens — elke aanroep)
```

### Gebruik vanuit elke Odoo-module

```python
result = env['ai.router'].run(
    'classify',                          # routesleutel (valt terug op 'default')
    "Welke afdeling hoort bij deze melding: …",
    caller='daadit_helpdesk',            # vrij veld voor het logboek
)
result['content']   # het antwoord
result['status']    # 'ok' | 'fallback'
```

### Configuratie (in de bestaande **AI-app**, alleen beheerders)

1. **AI → Configuration → Router: Providers** — sleutel invullen óf leeg laten:
   zonder eigen sleutel hergebruikt de provider automatisch de sleutel uit de
   centrale AI-instellingen (bijv. `daadit_ai_mistral.mistral_key`).
2. **AI → Configuration → Router: Routes** — per doel een route: `default`
   (vangnet), daarnaast bijv. `classify`, `draft`, `heavy`. Per route:
   provider + model, optionele fallback, max_tokens en temperature.
3. **AI → Router-logboek** (naast Schedule Runs) — elke aanroep: doel,
   provider, model, status (ok / fallback / fout), latency en tokens.

## Modellen

| Model | Doel |
|---|---|
| `ai.router` (abstract) | Service: `run(purpose, messages, caller=None, **opts)` |
| `ai.router.provider` | Provider-configuratie + API-implementatie per leverancier |
| `ai.router.route` | Doel → provider/model-mapping met fallback en limieten |
| `ai.router.log` | Auditlog van elke aanroep |

## Deploy (Odoo.sh)

1. Module-map kopiëren in de Odoo.sh-projectrepo en pushen naar de doelbranch.
2. Apps → *Update Apps List* → installeer **DAADit — AI Router**.
3. Providers + routes configureren (zie boven); zonder actieve provider en
   route doet de module niets en breekt hij ook niets.

## Roadmap fase 2 — werk-router

- `ai.router.dispatch`-regels per bronmodel (helpdesk.ticket, crm.lead, mail)
- Classificatie via de fase-1-gateway (route `classify`)
- Acties: toewijzen aan AI-agent, medewerker of team — draft-only conform het
  bestaande agent-beleid, bewaakt door de agent-assurance watchdog
