# Rechtenreview van de agents die mogen uitvoeren

Taak 708. Vijf collega's in het OAS hebben het rechtenniveau
*uitvoeren*: ze publiceren, versturen of wijzigen records. Die niveaus
zijn per agent ooit bewust gezet, maar hun scope groeide daarna mee met
hun werk. Deze procedure maakt van "we kijken er af en toe naar" een
terugkerende review met een uitkomst die is vastgelegd.

De review **maakt zichtbaar en besluit niets**. Het rapport wijzigt geen
recht, geen scope en geen blocklist; dat doet de reviewer daarna in de
configuratie.

---

## Wanneer

Elk kwartaal. De cron **DAADit AI: Rechtenreview van de uitvoerende
agents** stelt het rapport samen, legt het onder **AI → Rechtenreview →
Rechtenreviews** en zet het als **To-Do met deadline** bij de
verantwoordelijke gebruiker (standaard 14 dagen).

Instellingen (Instellingen → Technisch → Systeemparameters):

| Parameter | Betekenis |
|---|---|
| `daadit_ai_agent_schedule.permission_review_login` | login of e-mail van de verantwoordelijke reviewer |
| `daadit_ai_agent_schedule.permission_review_deadline_days` | dagen tot de deadline van de To-Do (standaard 14) |

Staat de login niet ingesteld, dan gaat de To-Do naar de gebruiker onder
wiens rechten de meeste planningen draaien, en anders naar de beheerder.
Dat staat in het log; het is een terugval, geen keuze.

Tussentijds een review nodig (na een incident, na het verhogen van een
rechtenniveau): **AI → Rechtenreview → Nieuwe rechtenreview nu**.

## Wat er in het rapport staat

Het rapport wordt door code samengesteld uit de werkelijke
configuratie — er is geen handmatige lijst met agentnamen. Een agent
komt erin wanneer hij minstens één tool heeft die niet alleen leest, of
wanneer zijn rechtenniveau schrijven/uitvoeren zegt. Wie alleen zoekt en
leest hoort niet in een rechtenreview.

Per agent:

1. **Mag hij dit?**
   - rechtenniveau en rol;
   - de tools die schrijven, en apart de tools die **naar buiten**
     publiceren of versturen — een schrijfactie in Odoo is terug te
     draaien, een verstuurde mailing niet;
   - de tools die weghalen (verwijderen, archiveren, depubliceren);
   - **hard geblokkeerd**: wat uit de configuratie volgt en niet uit een
     prompt — een actie waarvoor geen tool bestaat, de modellen op de
     blokkeerlijst, alles buiten de leesscope, de velden op de
     PII-blocklist;
   - **gewijzigd sinds de vorige review**, vergeleken met de bestaande
     configuratie-momentopname (`daadit.config.snapshot`) die gold bij
     de vorige review.
2. **Klopt wat hij erover meldt?** Over de reviewperiode: runs, echte
   schrijfacties, geweigerde schrijfpogingen, runs met
   `claims_unverified` en runs met `needs_attention`.

De tweede vraag staat er omdat het rechtenniveau vaak niet het probleem
is. Bij Pim werd op 1-8 een foutieve aanroep door de scope-guard
geweigerd: er is niets geschreven, maar zijn rapport meldde het als
gedaan werk. Een geweigerde poging is dus geen bewijs van misbruik; een
onverifieerbare bewering is wel een bevinding.

## Wat de reviewer per agent doet

1. Lees de regel van de agent (**Uitkomst** staat op *Nog te
   beoordelen*).
2. Beantwoord vraag 1: past het rechtenniveau nog bij het werk dat hij
   doet? Kijk naar de gewijzigde configuratie: een tool of model dat er
   sinds de vorige review bij kwam, is niet eerder beoordeeld.
3. Beantwoord vraag 2: klopt zijn rapportage? Open bij twijfel de runs
   via **Runs in deze periode** en gebruik de feitenregel onder het
   runrapport, niet de tekst van de agent.
4. Kies de uitkomst en schrijf er één regel toelichting bij:

| Uitkomst | Wanneer | Wat er daarna gebeurt |
|---|---|---|
| **Niveau blijft** | scope past bij het werk, rapportage klopt | niets |
| **Scope inperken** | hij mag meer dan hij nodig heeft (leesscope, model, tool) | pas de leesscope/tools van de agent aan; het Knowledge-artikel van de agent is de bron van waarheid voor zijn prompt |
| **Recht intrekken** | hij hoort dit niveau niet meer te hebben, of zijn rapportage is niet te vertrouwen | verlaag het rechtenniveau en haal de schrijvende tools uit zijn onderwerp |

Bij een niet-kloppende rapportage is *scope inperken* niet automatisch
het antwoord: eerst uitzoeken of de agent iets doet wat hij niet mag, of
iets meldt wat hij niet deed. Het tweede is een prompt- of
rapportageprobleem en geen rechtenprobleem.

## Waar het wordt vastgelegd

- **Uitkomst en toelichting**: op de regel van de agent in de review.
  Die blijft staan, dus de volgende review begint bij het vorige besluit.
- **Doorgevoerde wijzigingen**: in de configuratie zelf. De nachtelijke
  configuratie-momentopname legt ze vast, en de volgende review meldt ze
  als wijziging — daarmee is een stille verruiming later terug te
  vinden.
- **Prompts en planningsinstructies**: in het Knowledge-artikel van de
  agent. Wijzig een prompt nooit alleen in de database; de uurlijkse
  sync zet die terug.

## Grenzen

- De review leest de configuratie; ze wijzigt er niets in.
- Wijzigingen zijn alleen zichtbaar zolang er momentopnamen zijn (de
  bewaartermijn is standaard 90 dagen). Ontbreekt de snapshotmodule of
  is er geen eerdere opname, dan zegt het rapport dat expliciet in
  plaats van "niets gewijzigd" te suggereren.
- De leesscope en de PII-blocklist worden vanaf module-versie
  19.0.2.1.0 van `daadit_tenant_blueprint` in de momentopname
  meegenomen. Verruimingen van vóór die versie staan niet in de reeks en
  zijn dus niet als wijziging te melden.
