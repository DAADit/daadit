# DAADit — Processtooling (YoMoRo)

Verankert de door YoMoRo geleverde, self-contained **HTML-processtools per klant**
in de DAADit-Group Odoo (v19), met een configureerbaar proces-model en
herbruikbare templates.

## Model `daadit.process`

Een klant kan meerdere processen hebben, elk apart configureerbaar:

| Veld | Betekenis |
|------|-----------|
| `name`, `code`, `sequence` | identificatie + volgorde |
| `partner_id` | de klant (leeg bij een template) |
| `is_template` | klant-onafhankelijk sjabloon |
| `template_id` | template waarvan dit proces is afgeleid (vult velden voor) |
| `tool_html` (+ `tool_filename`, `version`, `uploaded_on`) | de HTML-tool (filestore) |
| `portal_visible` | **per-proces config:** tonen in portal |
| `allow_validation` | **per-proces config:** valideren/bewerken toegestaan |
| `active` | actief/gearchiveerd |
| `description` | omschrijving |

`res.partner` (company) krijgt de hoofdschakelaar `process_tooling_active`, een
One2many `process_ids` en `process_count`.

## Templates

Processen met `is_template=True` zijn klant-onafhankelijke sjablonen. Maak een
klantproces aan en kies een template bij `template_id`: naam, omschrijving en
tool worden voorgevuld. Zo start een klant zonder ingericht proces vanaf een
sjabloon i.p.v. blanco. Beheer via **Processtooling → Templates**.

## UI

- **Contacts → tab Processtooling:** hoofdschakelaar + processenlijst.
- **Menu Processtooling:** *Klantprocessen* en *Templates*.
- **Portal (Mijn account):** kaart *Processen* → `/my/processen` (overzicht) →
  `/my/processen/<id>` (één proces in een sandboxed iframe).

## Toegangsmodel

Een portalgebruiker bereikt uitsluitend processen van het eigen bedrijf
(via `commercial_partner_id`), alleen als de hoofdschakelaar aan staat én het
proces `portal_visible` is. Serveren met `sudo()` ná de autorisatiecheck.
Interne preview: `/process-tooling/process/<id>/tool`.

## Routes

- `GET /my/processen` — overzicht.
- `GET /my/processen/<id>` — één proces (iframe).
- `GET /my/processen/<id>/tool` — serveert de tool (eigen bedrijf).
- `GET /process-tooling/process/<id>/tool` — interne-only preview.

## Bewust geen automatische tests

Op deze codebase kan een falende test alle odoo.sh-prod-builds blokkeren; dit
module blijft daarom testvrij.

## Bron

YoMoRo AI (Wytse), mail "Procesmodel" 9 juli 2026 — **vertrouwelijk**.
