# -*- coding: utf-8 -*-
"""De deploy-wachter: een stille storing bestaat niet meer (taak 1069).

Van 10 tot 13 augustus 2026 brak elke build van ``main`` af in een
migratie. Drie gemergde fixes stonden in de code en niet in de database,
en geen enkel onderdeel van het systeem meldde dat — de melding kwam van
GitHub naar een mens. Een systeem dat onbeheerd moet draaien, hoort dat
zelf te zien.

Deze wachter kijkt niet naar de build (die kent hij niet) maar naar het
*gevolg* in de eigen database, en dat kan hij wel zien:

* de versie in het manifest op schijf tegenover de versie in de
  database — verschillen die, dan is er code gedeployed die nooit is
  geladen;
* modules die in een tussenstand blijven hangen (``to upgrade``,
  ``to install``, ``to remove``) — de helft van een upgrade;
* actieve crons waarvan de ``nextcall`` in het verleden ligt — dan
  draait de scheduler niet meer en staat alles stil zonder foutmelding;
* de plek van de MCP-masterkey (taak 642): staat de sleutel die elke
  klantcredential ontsleutelt nog in dezelfde database als de
  ciphertext, dan is één dump gelijk aan alle klantcredentials. Ook dat
  stond tot nu toe alleen in het serverlog.

Elke bevinding wordt een **taak** op het agentbord. Bewust geen
activiteit: een activiteit valt onder de dagcap en kan stil geweigerd
worden (taken 773/774), en dat is precies het gat dat we hier dichten.
Bestaat er al een open wachterstaak, dan wordt die bijgewerkt in plaats
van een tweede aangemaakt — één actuele lijst, nooit een gemiste.

De wachter leest en meldt. Hij herstelt niets, start geen build en
raakt geen module aan.
"""
import logging

from markupsafe import Markup

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

# Het project waarop bevindingen landen. Een instelling en geen id in
# code: ids uit de ene database betekenen niets in de andere. Leeg
# betekent: zoek het project op naam.
PROJECT_ID_PARAM = "daadit_ai_agent_schedule.deploy_watch_project_id"
PROJECT_NAME_PARAM = "daadit_ai_agent_schedule.deploy_watch_project_name"
DEFAULT_PROJECT_NAME = "Odoo Agentic System"
# Hoeveel minuten een cron achter mag lopen voordat het een bevinding is.
# Ruim boven het langste normale interval van de scheduler zelf, zodat
# een drukke worker geen valse melding oplevert.
CRON_GRACE_PARAM = "daadit_ai_agent_schedule.deploy_watch_cron_grace_minutes"
DEFAULT_CRON_GRACE = 90
# Het voorvoegsel waarop de wachter zijn eigen open taak terugvindt.
TASK_PREFIX = "[Deploy-wachter]"
# Het model waarop de masterkey-controle leunt. Deze module hangt niet
# aan daadit_mcp_multi_tenant (zie ``_master_key_findings``), dus de
# controle wordt op naam opgezocht en overgeslagen als de gateway niet
# geïnstalleerd is.
MCP_INSTANCE_MODEL = "mcp.instance"
# Tussenstanden: een module die hier langer dan een cyclus in blijft
# staan, is een afgebroken upgrade.
_PENDING_STATES = ("to upgrade", "to install", "to remove")


class AiDeployWatch(models.TransientModel):
    """Controleert of gedeployde code ook echt geladen is."""

    _name = "daadit.ai.deploy.watch"
    _description = "Deploy-wachter"

    # ------------------------------------------------------------------
    # De controles
    # ------------------------------------------------------------------
    @api.model
    def _manifest_version(self, module):
        """De versie uit het manifest op schijf, of een lege string.

        Lukt het opzoeken niet — een andere Odoo-versie kan deze hulp
        anders noemen — dan slaat de controle over en zegt de log dat.
        Een wachter die crasht is erger dan een wachter die één ding
        niet weet.
        """
        try:
            info = module.get_module_info(module.name) or {}
        except Exception:  # noqa: BLE001
            _logger.warning(
                "deploy-wachter: kon het manifest van %s niet lezen; "
                "versievergelijking overgeslagen", module.name,
            )
            return ""
        return (info.get("version") or "").strip()

    @api.model
    def _version_findings(self):
        """Modules waarvan de code op schijf niet in de database staat.

        Alleen ``daadit_*``: Odoo-core en Enterprise hebben we niet in
        onze git-repo, en ``get_module_info`` over álle geïnstalleerde
        modules (vaak honderden) kost geheugen/IO op elke cronrun —
        genoeg om op een krappe Odoo.sh-worker te knellen (signal 9).
        """
        Module = self.env["ir.module.module"].sudo()
        out = []
        for module in Module.search([
            ("state", "=", "installed"),
            ("name", "=like", "daadit_%"),
        ]):
            on_disk = self._manifest_version(module)
            in_db = (module.latest_version or "").strip()
            if not on_disk or not in_db:
                continue
            if on_disk != in_db:
                out.append(_(
                    "%(module)s: manifest %(disk)s, database %(db)s — "
                    "gedeployde code is niet geladen",
                    module=module.name, disk=on_disk, db=in_db,
                ))
        return out

    @api.model
    def _pending_findings(self):
        """Modules die in een tussenstand zijn blijven hangen."""
        Module = self.env["ir.module.module"].sudo()
        modules = Module.search([("state", "in", list(_PENDING_STATES))])
        return [
            _(
                "%(module)s staat op '%(state)s' — een upgrade is "
                "afgebroken",
                module=module.name, state=module.state,
            )
            for module in modules
        ]

    @api.model
    def _cron_grace_minutes(self):
        icp = self.env["ir.config_parameter"].sudo()
        try:
            minutes = int(
                icp.get_param(CRON_GRACE_PARAM, DEFAULT_CRON_GRACE)
                or DEFAULT_CRON_GRACE
            )
        except (TypeError, ValueError):
            minutes = DEFAULT_CRON_GRACE
        return max(5, minutes)

    @api.model
    def _cron_findings(self):
        """Actieve crons die hun eigen ritme missen."""
        grace = self._cron_grace_minutes()
        cutoff = fields.Datetime.subtract(
            fields.Datetime.now(), minutes=grace,
        )
        crons = self.env["ir.cron"].sudo().search([
            ("active", "=", True), ("nextcall", "<", cutoff),
        ])
        return [
            _(
                "cron '%(name)s' stond gepland op %(nextcall)s en is "
                "meer dan %(grace)s minuten te laat",
                name=cron.name,
                nextcall=fields.Datetime.to_string(cron.nextcall),
                grace=grace,
            )
            for cron in crons
        ]

    @api.model
    def _master_key_findings(self):
        """Bevindingen over de plek van de MCP-masterkey (taak 642).

        De afhankelijkheid gaat de andere kant op: ``daadit_ai_mistral``
        en deze module weten niets van ``daadit_mcp_multi_tenant``, en
        die richting omdraaien zou de gateway aan het agentbord knopen.
        Daarom kijkt de wachter of het model *bestaat* en laat hij de
        gateway zelf de bevinding formuleren
        (``mcp.instance._master_key_watch_findings``). Staat de gateway
        er niet, dan is er niets te melden.

        De wachter ziet de sleutel nooit — alleen de bron ervan.
        """
        if MCP_INSTANCE_MODEL not in self.env:
            return []
        model = self.env[MCP_INSTANCE_MODEL].sudo()
        if not hasattr(model, "_master_key_watch_findings"):
            # Een oudere versie van de gateway: melden dat de controle
            # niet gedraaid heeft is eerlijker dan zwijgen.
            _logger.warning(
                "deploy-wachter: %s kent geen masterkey-controle; werk "
                "daadit_mcp_multi_tenant bij", MCP_INSTANCE_MODEL,
            )
            return []
        try:
            return list(model._master_key_watch_findings())
        except Exception:  # noqa: BLE001
            _logger.exception(
                "deploy-wachter: de masterkey-controle liep vast; de "
                "overige controles gaan door",
            )
            return []

    # ------------------------------------------------------------------
    # Melden
    # ------------------------------------------------------------------
    @api.model
    def _watch_project(self):
        """Het project waarop bevindingen landen, of een leeg recordset."""
        icp = self.env["ir.config_parameter"].sudo()
        Project = self.env["project.project"].sudo()
        raw = (icp.get_param(PROJECT_ID_PARAM, "") or "").strip()
        if raw:
            try:
                project = Project.browse(int(raw)).exists()
            except (TypeError, ValueError):
                project = Project.browse()
            if project:
                return project
            _logger.warning(
                "deploy-wachter: %s verwijst naar een project dat niet "
                "bestaat (%s)", PROJECT_ID_PARAM, raw,
            )
        name = (
            icp.get_param(PROJECT_NAME_PARAM, "") or DEFAULT_PROJECT_NAME
        ).strip()
        project = Project.search([("name", "=", name)], limit=1)
        if not project:
            _logger.warning(
                "deploy-wachter: geen project '%s'; zet %s op het id van "
                "het bord waarop bevindingen horen", name, PROJECT_ID_PARAM,
            )
        return project

    @api.model
    def _open_task(self, project):
        """De open wachterstaak op dit project, of een leeg recordset."""
        return self.env["project.task"].sudo().search([
            ("project_id", "=", project.id),
            ("name", "=like", TASK_PREFIX + "%"),
            ("state", "not in", ("1_done", "1_canceled")),
        ], order="id desc", limit=1)

    @api.model
    def _compose(self, findings):
        """De tekst van de melding: geteld, met de bevindingen eronder."""
        body = [_(
            "<p>De deploy-wachter vond %(count)s punt(en) op "
            "%(stamp)s.</p>",
            count=len(findings),
            stamp=fields.Datetime.to_string(fields.Datetime.now()),
        ), "<ul>"]
        body.extend("<li>%s</li>" % finding for finding in findings)
        body.append("</ul>")
        body.append(_(
            "<p>De wachter leest alleen. Herstellen is mensenwerk: een "
            "hangende upgrade vraagt een nieuwe build, een achterlopende "
            "cron een werkende scheduler.</p>"
        ))
        return "".join(body)

    @api.model
    def _report(self, findings):
        """Leg de bevindingen vast als taak; werk een bestaande bij."""
        project = self._watch_project()
        if not project:
            return self.env["project.task"]
        name = _(
            "%(prefix)s %(count)s punt(en) — %(date)s",
            prefix=TASK_PREFIX, count=len(findings),
            date=fields.Date.to_string(fields.Date.context_today(self)),
        )
        body = self._compose(findings)
        task = self._open_task(project)
        if task:
            task.write({"name": name, "description": body})
            task.message_post(body=Markup(body))
            return task
        return self.env["project.task"].sudo().create({
            "name": name,
            "project_id": project.id,
            "description": body,
            "priority": "1",
        })

    # ------------------------------------------------------------------
    @api.model
    def check(self):
        """Voer de controles uit en geef de bevindingen terug."""
        findings = (
            self._version_findings()
            + self._pending_findings()
            + self._cron_findings()
            + self._master_key_findings()
        )
        if not findings:
            _logger.info(
                "deploy-wachter: geen bevindingen — code, moduletoestand, "
                "cronritme en de plek van de masterkey lopen gelijk",
            )
            return findings
        task = self._report(findings)
        _logger.warning(
            "deploy-wachter: %s bevinding(en) vastgelegd%s",
            len(findings),
            " op taak %s" % task.id if task else " (geen bord gevonden)",
        )
        return findings

    @api.model
    def _cron_check(self):
        """De cadans van de wachter."""
        self.check()
