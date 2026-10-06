# -*- coding: utf-8 -*-
"""Het meldpad van de assurance-keten: verslagen zijn geen werk (taak 1070).

Op 14 augustus 2026 stonden er 28 open activiteiten op één
knowledge-artikel, allemaal op dezelfde gebruiker. Het merendeel was
*achteraf-informatie*: een reparatie die de watchdog al had uitgevoerd,
een voorstel dat niet toepasbaar bleek, een voorstel dat de vangrail had
geweigerd. Geen van die meldingen vroeg nog een handeling, maar ze vulden
samen de activity-cap van dat record — waardoor een échte melding stil
geweigerd werd. Precies het gat dat de deploy-wachter (taak 1069) al voor
zijn eigen bevindingen dichtte.

Dit model is het ene punt waar de assurance-keten zijn uitkomsten
neerlegt, met één onderscheid dat alles bepaalt:

* een **verslag** (uitgevoerd, niet toepasbaar, geweigerd) is een regel
  onder één *rollende* taak op het agentbord. Eén taak, bijgewerkt,
  nooit een tweede;
* een **voorstel dat nog werk vraagt** blijft een eigen, aparte taak.
  Als regel in een rapport verdwijnt het namelijk: niemand leest de
  vijftiende bullet van een verslag. Ontdubbelen gebeurt op de *inhoud*
  van het voorstel en niet op het onderwerp-voorvoegsel, want dat
  voorvoegsel wordt onderweg herschreven ("AUTO-APPLY …" wordt
  "VOORSTEL (niet toepasbaar): …").

Niets sluit zichzelf. Een taak die een mens moet beoordelen blijft open;
afronden is een menselijk besluit op basis van bewijs.

Waarom in deze module en niet in ``daadit_ai_mistral``: de keten die deze
verslagen produceert hangt aan de planning-runs
(``daadit.ai.agent.schedule.run``, cron 86) en het patroon van melden op
project "Odoo Agentic System" staat hier al twee keer
(``ai_deploy_watch``, ``ai_eval``). ``daadit_ai_mistral`` is de
provider-/toollaag; daar hoort de *beslissing* van een tool, niet de
rapportage van een planningsketen.
"""
import hashlib
import logging
import re

from markupsafe import Markup

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Het voorvoegsel van de rollende verslagtaak en van een voorstel-taak.
# Twee verschillende voorvoegsels, want ze hebben een verschillende
# levensduur: het verslag rolt door, een voorstel wordt afgehandeld.
REPORT_PREFIX = "[Assurance-verslag]"
PROPOSAL_PREFIX = "[Assurance-voorstel]"

# De soorten uitkomst die de keten kan melden. De eerste drie zijn
# verslagen (achteraf-informatie), de laatste twee vragen werk van een
# mens.
KIND_APPLIED = "toegepast"
KIND_NOT_APPLICABLE = "niet_toepasbaar"
KIND_REFUSED = "geweigerd"
KIND_PROPOSAL = "voorstel"
KIND_PROPOSAL_REFUSED = "voorstel_geweigerd"

REPORT_KINDS = (KIND_APPLIED, KIND_NOT_APPLICABLE, KIND_REFUSED)
WORK_KINDS = (KIND_PROPOSAL, KIND_PROPOSAL_REFUSED)

KIND_LABELS = {
    KIND_APPLIED: "Reparatie uitgevoerd",
    KIND_NOT_APPLICABLE: "Voorstel niet toepasbaar",
    KIND_REFUSED: "Voorstel geweigerd door de vangrail",
    KIND_PROPOSAL: "Voorstel vraagt een besluit",
    KIND_PROPOSAL_REFUSED: "Voorstel geweigerd — een mens moet beslissen",
}

# Hoeveel regels de rollende taak in zijn beschrijving houdt. Ouder dan
# dat blijft in de chatter van de taak staan: elke regel wordt daar ook
# gepost, dus afkappen verliest geen historie.
MAX_REPORT_LINES = 60

# De voorvoegsels waarmee de bestaande keten zijn meldingen aankondigt.
# Ze staan hier omdat de opruimer een verslag moet kunnen herkennen op
# een record waar hij niets anders over weet, en omdat ontdubbelen op
# inhoud betekent: eerst dit eraf.
REPORT_SUMMARY_TOKENS = (
    "♻️ Watchdog-reparatie uitgevoerd",
    "⚠️ AUTO-APPLY niet toepasbaar",
    "⚠️ Vangrail: AUTO-APPLY geweigerd",
)
SUBJECT_PREFIXES = REPORT_SUMMARY_TOKENS + (
    "VOORSTEL (niet toepasbaar)",
    "VOORSTEL (geweigerd door vangrail)",
    "VOORSTEL",
    "AUTO-APPLY",
)

# Het meldkanaal dat de opruimer mag leegmaken. Als parameter en niet als
# id in code: 182 betekent niets in een andere database, en een opruimer
# die op het verkeerde record losgaat is erger dan een volle stapel.
CLEANUP_MODEL_PARAM = "daadit_ai_agent_schedule.assurance_cleanup_model"
CLEANUP_RES_ID_PARAM = "daadit_ai_agent_schedule.assurance_cleanup_res_id"
DEFAULT_CLEANUP_MODEL = "knowledge.article"
# Standaard UIT. De stapel is iemands werkvoorraad: hem opruimen is een
# besluit van die persoon, geen bijwerking van een module-upgrade.
CLEANUP_ENABLED_PARAM = "daadit_ai_agent_schedule.assurance_cleanup_enabled"
CLEANUP_LIMIT_PARAM = "daadit_ai_agent_schedule.assurance_cleanup_limit"
DEFAULT_CLEANUP_LIMIT = 40

_LI_RE = re.compile(r"<li>(.*?)</li>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# De serveractie in de database wordt een doorgeefluik, precies zoals
# SERVER_ACTION_CODE in daadit_ai_mistral dat voor de scope-guard doet:
# de beslissing waar een uitkomst landt, staat in deze module.
SERVER_ACTION_CODE = '''# ASSURANCE-MELDPAD — waar een uitkomst landt, staat in
# daadit_ai_agent_schedule, model daadit.ai.assurance.report,
# methode record(). Wijzig het meldpad daar en niet hier.
try:
    _kind = kind
except Exception:
    _kind = 'toegepast'
try:
    _subject = subject
except Exception:
    _subject = ''
try:
    _detail = detail
except Exception:
    _detail = ''
try:
    _source = source
except Exception:
    _source = ''

ai['result'] = env['daadit.ai.assurance.report'].sudo().record(
    kind=_kind,
    subject=_subject,
    detail=_detail,
    source=_source,
)
'''


class AiAssuranceReport(models.TransientModel):
    """Eén meldpad voor de uitkomsten van de assurance-keten."""

    _name = "daadit.ai.assurance.report"
    _description = "Assurance-meldpad"

    # ------------------------------------------------------------------
    # Hulp
    # ------------------------------------------------------------------
    @api.model
    def _project(self):
        """Het bord waarop de keten meldt.

        Bewust dezelfde instelling als de deploy-wachter en de
        kwaliteitsmeting gebruiken: drie parameters voor hetzelfde bord
        leveren op een dag drie borden op.
        """
        return self.env["daadit.ai.deploy.watch"]._watch_project()

    @api.model
    def _open_task(self, project, prefix):
        """De open taak met dit voorvoegsel, of een leeg recordset."""
        return self.env["project.task"].sudo().search([
            ("project_id", "=", project.id),
            ("name", "=like", prefix + "%"),
            ("state", "not in", ("1_done", "1_canceled")),
        ], order="id desc", limit=1)

    @api.model
    def _plain(self, html):
        """De leesbare tekst uit een stuk HTML, zonder witruimteruis."""
        text = _TAG_RE.sub(" ", html or "")
        for entity, char in (
            ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
            ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
        ):
            text = text.replace(entity, char)
        return _WS_RE.sub(" ", text).strip()

    @api.model
    def strip_prefix(self, subject):
        """Het onderwerp zonder het voorvoegsel van de keten.

        De keten herschrijft zijn eigen onderwerpen onderweg — een
        "AUTO-APPLY …" wordt bij afkeuring "VOORSTEL (niet toepasbaar):
        AUTO-APPLY …". Ontdubbelen op het onderwerp zoals het er staat
        levert daarom voor hetzelfde voorstel twee taken op.
        """
        text = (subject or "").strip()
        changed = True
        while changed:
            changed = False
            for prefix in SUBJECT_PREFIXES:
                if text.upper().startswith(prefix.upper()):
                    text = text[len(prefix):].lstrip(" :—-–").strip()
                    changed = True
                    break
        return text

    @api.model
    def is_report_summary(self, summary):
        """Is dit de samenvatting van een verslag (en dus geen werk)?"""
        text = (summary or "").strip()
        return any(
            text.upper().startswith(token.upper())
            for token in REPORT_SUMMARY_TOKENS
        )

    @api.model
    def _fingerprint(self, subject, detail):
        """De vingerafdruk van de *inhoud* van een voorstel.

        Onderwerp zonder voorvoegsel plus de platte tekst van de
        toelichting: hetzelfde voorstel dat morgen opnieuw langskomt
        krijgt dezelfde vingerafdruk, ook als het voorvoegsel inmiddels
        anders is.
        """
        raw = "%s|%s" % (
            self.strip_prefix(subject).lower(),
            self._plain(detail).lower(),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Het meldpad
    # ------------------------------------------------------------------
    @api.model
    def record(self, kind, subject, detail="", source=""):
        """Leg één uitkomst van de assurance-keten vast.

        Dit is de enige methode die de serveractie hoeft te kennen. De
        soort uitkomst bepaalt waar hij landt: een verslag als regel
        onder de rollende taak, een voorstel als eigen taak.

        Geeft een dict terug (``ok``, ``kind``, ``task_id``) zodat de
        aanroepende kant kan loggen wat er gebeurd is; een onbekende
        soort is een fout die zichtbaar hoort te zijn en geen stille
        overslag.
        """
        kind = (kind or "").strip() or KIND_APPLIED
        if kind in REPORT_KINDS:
            task = self._append_report(kind, subject, detail, source)
        elif kind in WORK_KINDS:
            task = self._proposal_task(kind, subject, detail, source)
        else:
            _logger.warning(
                "assurance-meldpad: onbekende soort %r voor %r", kind, subject,
            )
            return {
                "ok": False,
                "kind": kind,
                "error": _(
                    "Onbekende soort uitkomst %(kind)r. Toegestaan: "
                    "%(kinds)s.",
                    kind=kind,
                    kinds=", ".join(REPORT_KINDS + WORK_KINDS),
                ),
            }
        return {
            "ok": bool(task),
            "kind": kind,
            "task_id": task.id if task else False,
            "is_report": kind in REPORT_KINDS,
        }

    @api.model
    def _line(self, kind, subject, source=""):
        """Eén verslagregel: wanneer, wat voor uitkomst, waarover."""
        who = " (%s)" % source if source else ""
        return "%s — <b>%s</b>%s: %s" % (
            fields.Datetime.to_string(fields.Datetime.now()),
            KIND_LABELS.get(kind, kind),
            who,
            self.strip_prefix(subject) or _("zonder onderwerp"),
        )

    @api.model
    def _compose(self, lines):
        """De beschrijving van de rollende taak: geteld, met de regels."""
        body = [_(
            "<p>De assurance-keten legde %(count)s verslag(en) neer. Dit is "
            "achteraf-informatie over al afgehandeld werk: hier staat niets "
            "open. Een uitkomst die wél een besluit vraagt, staat als eigen "
            "taak op dit bord (%(prefix)s).</p>",
            count=len(lines), prefix=PROPOSAL_PREFIX,
        ), "<ul>"]
        body.extend("<li>%s</li>" % line for line in lines)
        body.append("</ul>")
        body.append(_(
            "<p>Deze taak rolt door en sluit zichzelf niet. Oudere regels "
            "staan in de chatter hieronder.</p>"
        ))
        return "".join(body)

    @api.model
    def _append_report(self, kind, subject, detail="", source=""):
        """Voeg een verslagregel toe aan de ene rollende taak."""
        project = self._project()
        if not project:
            _logger.warning(
                "assurance-meldpad: geen bord gevonden; verslag %r is "
                "nergens vastgelegd", subject,
            )
            return self.env["project.task"]
        line = self._line(kind, subject, source)
        task = self._open_task(project, REPORT_PREFIX)
        lines = _LI_RE.findall(task.description or "") if task else []
        lines.append(line)
        lines = lines[-MAX_REPORT_LINES:]
        name = _(
            "%(prefix)s %(count)s verslag(en) — %(date)s",
            prefix=REPORT_PREFIX, count=len(lines),
            date=fields.Date.to_string(fields.Date.context_today(self)),
        )
        values = {"name": name, "description": self._compose(lines)}
        if not task:
            values.update({"project_id": project.id, "priority": "0"})
            task = self.env["project.task"].sudo().create(values)
        else:
            task.write(values)
        body = "<p>%s</p>" % line
        if detail:
            body += detail if "<" in (detail or "") else "<p>%s</p>" % detail
        task.message_post(body=Markup(body))
        return task

    @api.model
    def _proposal_task(self, kind, subject, detail="", source=""):
        """Een voorstel dat werk vraagt: eigen taak, ontdubbeld op inhoud."""
        project = self._project()
        if not project:
            _logger.warning(
                "assurance-meldpad: geen bord gevonden; voorstel %r is "
                "nergens vastgelegd", subject,
            )
            return self.env["project.task"]
        marker = "assurance-fp:%s" % self._fingerprint(subject, detail)
        existing = self.env["project.task"].sudo().search([
            ("project_id", "=", project.id),
            ("description", "like", marker),
            ("state", "not in", ("1_done", "1_canceled")),
        ], order="id desc", limit=1)
        title = self.strip_prefix(subject) or _("voorstel zonder onderwerp")
        name = "%s %s" % (PROPOSAL_PREFIX, title[:150])
        body = _(
            "<p><b>%(label)s</b>%(who)s</p>",
            label=KIND_LABELS.get(kind, kind),
            who=" (%s)" % source if source else "",
        )
        if detail:
            body += detail if "<" in (detail or "") else "<p>%s</p>" % detail
        body += _(
            "<p>Dit voorstel is <b>niet</b> uitgevoerd en sluit zichzelf "
            "niet: beoordelen en afronden is een menselijk besluit.</p>"
        )
        body += "<!-- %s -->" % marker
        if existing:
            # Hetzelfde voorstel opnieuw: één taak die zegt dat het nog
            # steeds langskomt, niet een tweede taak met dezelfde inhoud.
            existing.message_post(body=_(
                "Dit voorstel kwam opnieuw langs en staat nog open."
            ))
            return existing
        return self.env["project.task"].sudo().create({
            "name": name,
            "project_id": project.id,
            "priority": "1",
            "description": body,
        })

    # ------------------------------------------------------------------
    # De bestaande stapel opruimen
    # ------------------------------------------------------------------
    @api.model
    def _cleanup_target(self):
        """(model, res_id) van het meldkanaal, of (model, 0)."""
        icp = self.env["ir.config_parameter"].sudo()
        model = (
            icp.get_param(CLEANUP_MODEL_PARAM, "") or DEFAULT_CLEANUP_MODEL
        ).strip()
        raw = (icp.get_param(CLEANUP_RES_ID_PARAM, "") or "").strip()
        try:
            res_id = int(raw)
        except (TypeError, ValueError):
            res_id = 0
        return model, res_id

    @api.model
    def _cleanup_enabled(self):
        """Staat de opruimer aan? Standaard niet."""
        raw = (self.env["ir.config_parameter"].sudo().get_param(
            CLEANUP_ENABLED_PARAM, "") or "").strip().lower()
        return raw in ("1", "true", "yes", "ja", "aan")

    @api.model
    def _cleanup_limit(self):
        try:
            limit = int(
                self.env["ir.config_parameter"].sudo().get_param(
                    CLEANUP_LIMIT_PARAM, DEFAULT_CLEANUP_LIMIT,
                ) or DEFAULT_CLEANUP_LIMIT
            )
        except (TypeError, ValueError):
            limit = DEFAULT_CLEANUP_LIMIT
        return max(1, min(200, limit))

    @api.model
    def _report_activities(self, model, res_id, limit):
        """De verslag-activiteiten op dit record, oudste eerst.

        Alles wat géén verslag is blijft buiten deze selectie: een
        voorstel dat nog een besluit vraagt, is iemands werkvoorraad en
        geen afval.
        """
        activities = self.env["mail.activity"].sudo().search([
            ("res_model", "=", model), ("res_id", "=", res_id),
        ], order="id asc")
        matching = activities.filtered(
            lambda a: self.is_report_summary(a.summary)
        )
        return matching[:limit]

    @api.model
    def cleanup_report_activities(self, dry_run=True):
        """Vat de verslag-stapel samen op de rollende taak en sluit hem af.

        Sluiten gebeurt met ``action_feedback``: de tekst blijft dan in de
        chatter van het record staan. Verwijderen doet deze opruimer
        nooit — dit is historie, en historie die je weggooit kun je niet
        narekenen.

        Een droogloop (``dry_run=True``, de standaard) schrijft niets:
        niet op het record, niet op het bord. Hij rapporteert alleen wat
        hij zou sluiten.
        """
        model, res_id = self._cleanup_target()
        if not res_id:
            return {
                "ok": False,
                "dry_run": dry_run,
                "closed": 0,
                "reason": _(
                    "Geen meldkanaal geconfigureerd: zet %(param)s op het id "
                    "van het record waarvan de verslagen mogen worden "
                    "afgesloten.", param=CLEANUP_RES_ID_PARAM,
                ),
            }
        if not dry_run and not self._cleanup_enabled():
            return {
                "ok": False,
                "dry_run": False,
                "closed": 0,
                "reason": _(
                    "De opruimer staat uit. Dit is iemands werkvoorraad; zet "
                    "%(param)s op 1 om hem aan te zetten.",
                    param=CLEANUP_ENABLED_PARAM,
                ),
            }
        activities = self._report_activities(model, res_id, self._cleanup_limit())
        summaries = [(a.id, a.summary or "") for a in activities]
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "closed": 0,
                "would_close": summaries,
                "count": len(summaries),
            }
        task = self.env["project.task"]
        for activity_id, summary in summaries:
            task = self._append_report(
                self._kind_of(summary), summary,
                detail=_(
                    "<p>Overgenomen van activiteit %(aid)s op %(model)s "
                    "#%(rid)s en daar afgesloten; de tekst blijft in de "
                    "chatter van dat record staan.</p>",
                    aid=activity_id, model=model, rid=res_id,
                ),
                source=_("opruimer"),
            ) or task
        for activity in activities:
            activity.action_feedback(feedback=_(
                "Afgesloten door de assurance-opruimer (taak 1070): dit is "
                "een verslag van al afgehandeld werk en geen openstaande "
                "handeling. Het staat nu als regel onder de rollende taak "
                "%(prefix)s op het agentbord. De tekst blijft hier in de "
                "chatter staan.", prefix=REPORT_PREFIX,
            ))
        _logger.warning(
            "assurance-opruimer: %s verslag(en) samengevat en afgesloten op "
            "%s #%s", len(summaries), model, res_id,
        )
        return {
            "ok": True,
            "dry_run": False,
            "closed": len(summaries),
            "task_id": task.id if task else False,
        }

    @api.model
    def _kind_of(self, summary):
        """De soort verslag die bij deze samenvatting hoort."""
        text = (summary or "").upper()
        if text.startswith("♻️ WATCHDOG-REPARATIE UITGEVOERD"):
            return KIND_APPLIED
        if text.startswith("⚠️ VANGRAIL"):
            return KIND_REFUSED
        return KIND_NOT_APPLICABLE

    @api.model
    def _cron_cleanup(self):
        """De cadans van de opruimer; standaard staat de cron uit."""
        result = self.cleanup_report_activities(dry_run=False)
        if not result.get("ok"):
            _logger.info(
                "assurance-opruimer: niets gedaan — %s",
                result.get("reason") or _("geen reden gegeven"),
            )
        return result
