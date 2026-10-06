# Incidentprocedure: een agent heeft iets gedaan wat niet had gemoeten

Taak 705. Deze procedure gaat niet over een agent die *niets* doet — dat
is de runbook van de gateway — maar over een agent die wél iets deed en
het verkeerd was: een mail verstuurd, een blogpost gepubliceerd, 40
records aangepast, een activiteit bij de verkeerde persoon gelegd.

Weet je nog niet of er iets is gebeurd, of komt de melding van een
klant? Begin dan bij `docs/SUPPORT_RUNBOOK.md` (taak 639): dat blad
stelt eerst vast of het aan ons, aan het model of aan de Odoo van de
klant ligt, en verwijst hierheen zodra er echt iets is gewijzigd.

Uitgangspunt: **elke geslaagde schrijfaanroep is vastgelegd** met wat hij
raakte (`change_kind`, `target_model`, `target_ref` op de toolactie).
Terugdraaien is daarom een kwestie van de lijst aflopen, niet van
reconstrueren uit de tekst van het model.

---

## 0. Eerst stoppen, dan denken (≤ 2 minuten)

De eerste vraag is nooit "wat is er gebeurd" maar "gebeurt het nog".

1. **AI → Schedules** → de planning van de agent → **Archiveren**
   (vinkje *Active* uit). Dat stopt alleen deze planning.
2. Draait er nog een run? De runlog toont hem als *Running*. Wacht hem
   uit of laat hem door de deadline (`max_run_seconds`, standaard 900 s)
   aflopen; runs draaien maximaal één tegelijk, dus er kan niets
   parallel bijkomen.
3. Twijfel je of het één agent is of iets breders: zet de cron
   **DAADit AI: Run due agent schedules** uit (Instellingen →
   Technisch → Geplande acties). Dat stopt *al* het geplande werk. Chat
   blijft werken.
4. Is de schade extern zichtbaar (mail, publicatie, klantcontact),
   informeer dan direct de eigenaar van dat kanaal. Techniek kan wachten,
   een verzonden mail niet.

## 1. Vaststellen wat er echt is gebeurd

Ga naar **AI → Schedule Runs** en open de run. Gebruik de *feitenregel*
onderaan het rapport en het tabblad met toolacties, niet de tekst van de
agent — een agent die dingen verzint over wat hij deed is precies het
scenario waar dit voor bestaat (taak 714).

Per toolactie staat:

| veld | betekenis |
| --- | --- |
| `change_kind` | `Aangemaakt` / `Gewijzigd` / `Verwijderd` |
| `target_model` | het model dat hij raakte, bv. `crm.lead` |
| `target_ref` | het record-id |
| `arguments` | wat hij precies meestuurde |
| `result` | wat de tool teruggaf |

Filter op *Write* om het ruis-vrije lijstje te krijgen. Staat de lijst
leeg, dan is er niets gewijzigd — hoe overtuigend het rapport ook klinkt.

Noteer voor het incident: run-id, agent, tijdvenster, en de lijst
`model #id`. Dat is je werklijst.

## 2. Terugdraaien per soort wijziging

Odoo heeft geen "undo". Werk het lijstje af, per soort:

**Aangemaakt** — verwijder of archiveer het record. Archiveren is te
prefereren zolang het incident nog loopt: het haalt het uit beeld en
bewaart het bewijs. Bij een mail (`mail.mail`, `mail.message`) is
verwijderen zinloos; die is weg. Ga naar stap 4.

**Gewijzigd** — de vorige waarde staat in de chatter van het record
(*trackingvelden*) of in de tabel `mail.tracking.value`. Zet handmatig
terug. Staat het veld niet onder tracking, dan is de vorige waarde niet
uit Odoo te halen; gebruik dan het `arguments`-veld van de toolactie —
daar staat wat de agent *wilde* zetten, en dus wat er is overschreven,
en anders de back-up (stap 3).

**Verwijderd** — alleen terug uit de back-up. Odoo.sh houdt dagelijkse
back-ups; een enkel record haal je terug door de back-up als
staging-database te herstellen en het record daar te exporteren, niet
door de productie terug te zetten. Nooit een hele database
terugdraaien voor een handvol records: je verliest al het andere werk
van die dag.

**Bulk (meer dan ~20 records)** — draai niet handmatig terug. Exporteer
de lijst ids uit de toolacties, herstel de back-up als staging, exporteer
daar de juiste waarden en importeer die over productie heen.

## 3. Back-up en peilmoment

Odoo.sh → project → **Backups**. Kies de laatste back-up van *vóór* het
tijdstip in `start_date` van de run. Herstel hem als **staging**, nooit
over productie. De configuratie zelf staat los onder
**Instellingen → Configuratiegeschiedenis** (taak 706): daarmee zet je
whitelists, topics en planningen terug zonder aan data te raken.

## 4. Wat niet terug te draaien is

Verzonden mail, gepubliceerde content, een aangemaakte activiteit die
iemand al heeft gezien, een call naar een extern systeem. Hier is de
route niet technisch maar menselijk: bericht de ontvanger, corrigeer
publiek waar het publiek was, en leg vast dat het een geautomatiseerde
actie was.

## 5. Voorkomen dat het opnieuw gebeurt (zelfde dag)

Kies bewust één van deze, en leg de keuze vast op de taak:

- **Tool eruit** — haal de tool uit het onderwerp van de agent. Dit is
  het enige middel dat architectonisch werkt; de rest is discipline van
  een taalmodel.
- **Scope-guard** — beperk `model`/`domein` van de agent zodat het doel
  buiten bereik ligt.
- **Concept-only** — laat de agent concepten maken en de publicatie/het
  versturen aan een mens.
- **Opdracht aanscherpen** — het zwakste middel. Doe dit nooit als enige
  maatregel bij iets dat naar buiten ging.

Zet de planning daarna weer aan en laat één **testrun** lopen (dry run:
schrijfacties worden gesimuleerd, de runlog toont wél wat hij van plan
was). Pas als die klopt, zet je hem terug op zijn ritme.

## 6. Vastleggen

Maak een taak in het project met: run-id, wat er gewijzigd is (de lijst
uit stap 1), wat er teruggedraaid is, wat niet terug te draaien was, en
welke maatregel uit stap 5 is genomen. Zonder die laatste regel komt
hetzelfde incident terug — dat is drie keer gebeurd met publicatie-
claims voordat de bewijsplicht in de code zat.

## 7. Escalatie

- Data van meerdere klanten geraakt, of twijfel daarover: direct Nick,
  en behandel het als datalek-verdenking (72-uursklok, zie
  `daadit_mcp_multi_tenant/docs/RUNBOOK.md` §6).
- Alleen deze database, geen externe zichtbaarheid: binnen kantoortijd
  afhandelen, melden in de dagelijkse restlijst van Argus.
