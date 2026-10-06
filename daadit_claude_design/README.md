# DAADit Claude Design

Laat Odoo AI-agents het **live** design system uit [Claude Design](https://claude.ai/design) ophalen, zodat marketingcontent meeloopt als de huisstijl beweegt.

## Wat het toevoegt

| Onderdeel | Doel |
|---|---|
| AI-tool `AI Marketing: haal_claude_design` | Read-only tool: haalt projectmetadata, bestanden, tokens en brand-notes op |
| Instellingen → AI → Claude Design | Project-URL + OAuth access/refresh tokens |
| Systeemparameter `daadit_claude_design.project_url` | Standaard Design-project (DAADit huisstijl) |

API (zelfde als Claude Code / community design-MCP):

- `GET https://api.anthropic.com/v1/design/projects/<uuid>`
- `GET .../files`
- `GET .../download?path=...`

Auth: OAuth Bearer met scopes `user:design:read` / `user:design:write`.

## Installatie

1. Wacht tot Odoo.sh de branch met deze module heeft gebuild.
2. **Apps** → ⋮ → **Apps-lijst bijwerken**.
3. Verwijder de filter **Apps** (technische modules staan daar vaak achter), of zoek op technische naam `daadit_claude_design`.
4. Installeer **DAADit Claude Design**.
5. Tokens zetten onder **Instellingen → AI → Claude Design (huisstijl)**, bv. via:
   - Claude Code: `/design-login`, of
   - `npx -y github:erdnj/claude-design-mcp login` en daarna credentials plakken.
6. De tool koppelen aan de `ai.topic`s van Mark / Lux / Nova / Penny.
7. In hun prompt: *roep `haal_claude_design` altijd aan vóór drafts*.

Zonder installatie zie je geen Claude Design-blok in Instellingen.

## Gebruik door agents

```text
1. haal_claude_design  (live huisstijl)
2. schrijf draft blog / social in die stijl
3. leg werk vast op project.task in project Marketing
4. NIET publiceren tenzij expliciet gevraagd
```

De tool is read-only en hoort niet als publicatie/schrijf-effect te tellen.
