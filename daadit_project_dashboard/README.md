# daadit_project_dashboard

Generic portal-safe data layer for DAADit project dashboards (Avontuur,
Wilgenhaege, Goudmunter, Isolatie.com — anywhere we build a website-
builder QWeb dashboard for a project).

## Wat doet het

Exposeert één set HTTP-endpoints onder `/dashboard/project/<project_id>/…`
die alle dashboard-data als JSON terugstuurt:

- `whoami` — rol + capabilities van huidige user voor dit project
- `snapshot` — volledige dashboard-data in één call (role-filtered)
- `task/<id>` — detail van één taak (role-filtered fields)
- `task/<id>/stage` — kanban drag-and-drop move (write-permission gated)

Onder de motorkap doet de controller alle reads met `sudo()` en past
daarna strikt veld-allow-lists per rol toe. Zo kan een portal-user
data zien die hij normaal via Odoo's ACL niet zou kunnen lezen
(`hr.employee`, `planning.slot`, etc.), maar alleen de velden die we
expliciet hebben goedgekeurd voor zijn rol.

## Wie ziet wat

| Rol | Toegang |
|---|---|
| Internal user (`share=False`) | Volledig: alle velden zoals direct via Odoo-RPC |
| Portal user, **collaborator/follower/customer** | Subset: zie field-whitelists bovenaan `dashboard_data.py` |
| Anyone else | 403 |

Portal users zien **nooit**:
- `hr.employee` records (`EMPLOYEE_FIELDS_PORTAL = None`)
- `planning.slot` records (`PLANNING_SLOT_FIELDS_PORTAL = None`)
- E-mail/login van interne collega's (`USER_FIELDS_PORTAL = ['id','name']`)
- Task description (kan interne notities bevatten)
- Cost/uren-velden op tasks (`effective_hours`, `allocated_hours`)

Schrijven (stage-move) vereist `_can_edit()`: internal altijd; portal
alleen als `project.collaborator.limited_access = False`.

## Generiek per project

Alle endpoints accepteren `<int:project_id>` in het URL-pad. De
controller is dus **niet** Avontuur-specifiek; dezelfde module werkt
voor elk project in elke DAADit-implementatie.

De dashboard JS leest de project-ID uit `window.__DASH_PID` (zoals nu)
en stelt zijn URL's daarmee samen:
```js
fetch('/dashboard/project/'+PID+'/snapshot', {...})
```

## Doel, fasen en voortgang (v19.0.1.1.1)

De snapshot levert naast taken en milestones ook waar een
opdrachtgever als eerste naar kijkt:

- `goal` — het doel van het project als platte tekst. Het staat in de
  projectbeschrijving, in een blok met `class="o_daadit_goal"`, dat de
  projectmanager-agent bijwerkt (zie `daadit_ai_mistral`, model
  `ai_agent_project_report.py`). Buiten dat blok blijft de beschrijving
  onaangeroerd; het dashboard leest alleen het blok, zodat doel en
  rapportage nooit iets anders zeggen.
- `milestones` — de fasen, nu mét `task_count`, `done_task_count`,
  `sequence` en `reached_date`, gesorteerd op einddatum.
- `updates` + `latest_update` — de laatste acht voortgangsrapportages
  (`project.update`) met `status`, `progress` en beschrijving.

`project.update` is ook via `/sudo-rpc` te lezen, met dezelfde
project-scope als de andere modellen: alleen updates van dít project.

## Velden uitbreiden

Wil je een veld toevoegen aan wat portal users zien? Pas aan in
`controllers/dashboard_data.py`:

- Voeg veld toe aan `TASK_FIELDS_PORTAL`, `USER_FIELDS_PORTAL`, etc.
- **Denk eerst na over data-lekken**: salarissen, kostprijzen, interne
  notities — die horen NIET in de portal-set.

## Deploy

1. Klonen in DAADit custom-modules repo (naast `daadit_teams_meeting`)
2. `git commit -m "Add daadit_project_dashboard"` + `git push`
3. odoo.sh / runbot bouwt
4. In Odoo (staging eerst): **Apps** → **Update Apps List** → install
5. Test endpoint:
   ```bash
   curl -X POST https://<host>/dashboard/project/35/whoami \
     -b "session_id=<je-sessie>" \
     -H 'Content-Type: application/json' \
     -d '{"jsonrpc":"2.0","method":"call","params":{}}'
   ```

## Volgende stap (JS-refactor)

Zodra deze module live staat, kunnen we de dashboard-JS in de
website-builder views (`daadit.avontuur_dashboard_*` en latere
implementaties) omschrijven om de snapshot-endpoint te gebruiken in
plaats van directe `rpc('project.task',...)` calls. Resultaat:

- Portal users zien alle tabs zonder errors (read-only)
- Task-edit popup werkt voor portal (alleen safe velden)
- Internal users blijven werken zoals nu — één endpoint is ook sneller
- Module blijft generiek herbruikbaar voor toekomstige project-
  dashboards (Wilgenhaege, etc.)
