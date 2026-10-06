# -*- coding: utf-8 -*-
"""De kwaliteitsmeting per rol, binnen Odoo (taken 1072/739).

Elke moduleversie en elke modelwissel verandert het gedrag van een agent
op een manier die een promptreview niet vindt. Deze meting stelt elke
collega een vaste reeks realistische vragen en beoordeelt het antwoord op
de dingen die in productie stukgaan: verkeerde taal, terugvragen wat de
agent zelf kon opzoeken, werk teruggeven aan een mens, schrijven buiten
de eigen grens, een cijfer dat nergens vandaan komt, en de kosten van de
beurt.

Waarom binnen Odoo en niet als los script: een meting die een technisch
account met een API-sleutel nodig heeft, draait alleen wanneer iemand die
sleutel geeft — en dus niet. Deze variant draait op de cadans van een
cron en heeft geen credential nodig.

Wat dat kost aan onafhankelijkheid staat in het rapport zelf: het systeem
beoordeelt hier zijn eigen werk. Dat is te verdedigen zolang de meting
deterministisch is en niets kan goedkeuren wat ze niet heeft
vastgesteld — vandaar dat `untested` een eigen uitkomst is en niet stil
als `pass` telt. Het losse harnas in ``daadit_odoo/evals`` blijft de
onafhankelijke tegenhanger; deze twee gebruiken dezelfde casebestanden en
dezelfde scoringregels.

De beurt draait in **testmodus**: leestools werken echt, schrijftools
worden onderschept en met een gesimuleerd succes geantwoord. De meting
kan dus nooit iets in de productiedatabase wijzigen. Wat dat betekent
voor de criteria staat in ``services/eval_scoring.py``.

De poort valt op **verslechtering**, niet op een case die al rood was:
een poort die weken rood staat wordt genegeerd, en dan meet je niets meer.
Een verslechtering wordt een taak op het agentbord. Sluiten doet de
meting nooit zelf — of een kwaliteitsdaling acceptabel is, is een
menselijk besluit (de les van taak 773).
"""
import json
import logging
import os
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from ..services import eval_scoring

_logger = logging.getLogger(__name__)

# De casebestanden staan als JSON in de module, in exact het formaat van
# het losse harnas: één bron voor beide meetwegen. Cases wijzigen is een
# codewijziging en hoort dat ook te zijn — een meetlat die je in de
# database kunt bijstellen meet niets.
CASES_DIRNAME = "eval_cases"

# Welke rollen de cron mag meten, als kommalijst. Leeg = alle rollen die
# een casebestand hebben.
ROLES_PARAM = "daadit_ai_agent_schedule.eval_roles"
# Hoeveel rollen één cronronde meet. Eén per nacht: een beurt kost geld,
# en het beeld over de weken is belangrijker dan alles op één avond.
ROLES_PER_RUN_PARAM = "daadit_ai_agent_schedule.eval_roles_per_run"
DEFAULT_ROLES_PER_RUN = 1

TASK_PREFIX = "[Eval]"
GATE_PREFIX = "[Eval-poort]"

# De naam waaronder de meting per agent zijn eigen planning bijhoudt.
SCHEDULE_PREFIX = "[Eval]"

# Taak 1482: volgt een model de opbouw uit de instructies? Eén case, per
# model gedraaid; een model dat zakt gaat uit de automatische routering.
INSTRUCTION_CASE = "instructie-01"
INSTRUCTION_CRITERION = "instruction_following"
MODELS_PREFIX = "[Eval-modellen]"
# Per cronronde een paar modellen, zodat één ronde binnen de tijdslimiet
# van een cronworker blijft; elk model komt eens per week opnieuw aan bod.
MODELS_PER_RUN = 4
REMEASURE_DAYS = 7
# Een meting die na zoveel minuten nog geen uitkomst heeft, is afgebroken
# (worker gekilld, model blijft hangen); de volgende ronde gaat door.
HUNG_MINUTES = 30

PASS = eval_scoring.PASS
FAIL = eval_scoring.FAIL
UNTESTED = eval_scoring.UNTESTED


def _cases_dir():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        CASES_DIRNAME,
    )


def _clip(text, limit=20000):
    """Een tooluitkomst, afgekapt op een lengte die nog te verwerken is."""
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit]


def _model_of(arguments):
    """Het Odoo-model uit de argumenten van een vastgelegde toolaanroep."""
    if not arguments:
        return "?"
    try:
        data = json.loads(arguments)
    except (TypeError, ValueError):
        return "?"
    if isinstance(data, dict):
        for key in ("model", "res_model", "model_name"):
            if isinstance(data.get(key), str):
                return data[key]
    return "?"


class AiEvalResult(models.Model):
    """De uitkomst van één case in één meting."""

    _name = "daadit.ai.eval.result"
    _description = "Eval-uitkomst per case"
    _order = "eval_run_id desc, verdict, case_ref"

    eval_run_id = fields.Many2one(
        "daadit.ai.eval.run", string="Meting", required=True,
        ondelete="cascade", index=True,
    )
    case_ref = fields.Char(string="Case", required=True, index=True)
    role = fields.Char(string="Rol", index=True)
    agent_id = fields.Many2one(
        "ai.agent", string="Collega", ondelete="set null",
    )
    llm_model = fields.Char(string="Model")
    verdict = fields.Selection(
        [(PASS, "Geslaagd"),
         (FAIL, "Gezakt"),
         (UNTESTED, "Niet te beoordelen")],
        string="Uitkomst", required=True, index=True,
    )
    failed_detail = fields.Text(
        string="Gezakte criteria",
        help="De criteria die zakten, met de reden erbij.",
    )
    untested_detail = fields.Text(
        string="Niet te beoordelen",
        help="Criteria die deze meting niet kon vaststellen. Bewust "
             "apart: dit is geen geslaagd criterium.",
    )
    criteria_json = fields.Text(
        string="Criteria (ruw)",
        help="Alle criteria met hun uitkomst, zoals de scoring ze gaf.",
    )
    reply = fields.Text(string="Antwoord van de collega")
    cost_usd = fields.Float(string="Kosten (USD)", digits=(12, 4))
    schedule_run_id = fields.Many2one(
        "daadit.ai.agent.schedule.run", string="Run (bewijs)",
        ondelete="set null",
        help="De run waarin deze beurt is uitgevoerd: daar staan de "
             "toolaanroepen, de argumenten en de resultaten waarop deze "
             "uitkomst is gebaseerd.",
    )


class AiEvalRun(models.Model):
    """Eén meting: een reeks cases, hun uitkomst en het verschil met de
    vorige meting."""

    _name = "daadit.ai.eval.run"
    _description = "Eval-meting"
    _order = "date desc, id desc"
    _rec_name = "display_name"

    display_name = fields.Char(compute="_compute_display_name")
    date = fields.Datetime(
        string="Gestart", required=True, readonly=True,
        default=fields.Datetime.now, index=True,
    )
    role = fields.Char(
        string="Rol", readonly=True, index=True,
        help="De gemeten rol. Leeg = alle rollen in één meting.",
    )
    state = fields.Selection(
        [("running", "Bezig"), ("done", "Afgerond")],
        string="Status", default="running", required=True, readonly=True,
    )
    result_ids = fields.One2many(
        "daadit.ai.eval.result", "eval_run_id", string="Uitkomsten",
        readonly=True,
    )
    case_count = fields.Integer(
        string="Cases", compute="_compute_counts", store=True,
    )
    pass_count = fields.Integer(
        string="Geslaagd", compute="_compute_counts", store=True,
    )
    fail_count = fields.Integer(
        string="Gezakt", compute="_compute_counts", store=True,
    )
    untested_count = fields.Integer(
        string="Niet te beoordelen", compute="_compute_counts", store=True,
    )
    cost_usd = fields.Float(
        string="Kosten (USD)", compute="_compute_counts", store=True,
        digits=(12, 4),
    )
    regression_count = fields.Integer(
        string="Verslechterd", readonly=True,
        help="Cases die nu zakken en bij de vorige meting slaagden of "
             "niet te beoordelen waren. Dit is het getal waarop de poort "
             "valt.",
    )
    report = fields.Text(string="Rapport (ruw)", readonly=True)
    report_html = fields.Html(
        string="Rapport", readonly=True, sanitize=False,
        compute="_compute_report_html", store=True,
    )
    task_id = fields.Many2one(
        "project.task", string="Taak op het bord", readonly=True,
        ondelete="set null",
    )

    @api.depends("date", "role", "pass_count", "fail_count")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "%s — %s (%s/%s geslaagd)" % (
                fields.Datetime.to_string(rec.date) or "",
                rec.role or _("alle rollen"),
                rec.pass_count, rec.case_count,
            )

    @api.depends("result_ids", "result_ids.verdict", "result_ids.cost_usd")
    def _compute_counts(self):
        for rec in self:
            results = rec.result_ids
            rec.case_count = len(results)
            rec.pass_count = len(results.filtered(
                lambda r: r.verdict == PASS))
            rec.fail_count = len(results.filtered(
                lambda r: r.verdict == FAIL))
            rec.untested_count = len(results.filtered(
                lambda r: r.verdict == UNTESTED))
            rec.cost_usd = sum(results.mapped("cost_usd"))

    @api.depends("report")
    def _compute_report_html(self):
        for rec in self:
            rec.report_html = rec._as_html(rec.report or "")

    @staticmethod
    def _as_html(text):
        escaped = (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        return "<pre>" + escaped + "</pre>"

    # ------------------------------------------------------------------
    # Cases
    # ------------------------------------------------------------------
    @api.model
    def _load_cases(self, role=None, case_ref=None):
        """De cases uit de meegeleverde JSON-bestanden.

        Hetzelfde formaat als het losse harnas leest, zodat een case op
        één plek staat en niet op twee kan gaan afwijken.
        """
        cases = []
        directory = _cases_dir()
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(directory, name), "r") as handle:
                data = json.load(handle)
            if role and data.get("role") != role:
                continue
            for case in data.get("cases", []):
                if case_ref and case.get("id") != case_ref:
                    continue
                case["role"] = data.get("role")
                case["agent"] = data.get("agent")
                cases.append(case)
        return cases

    @api.model
    def _roles(self):
        """De rollen waarvoor een casebestand bestaat."""
        roles = []
        for name in sorted(os.listdir(_cases_dir())):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(_cases_dir(), name), "r") as handle:
                role = json.load(handle).get("role")
            if role:
                roles.append(role)
        return roles

    # ------------------------------------------------------------------
    # De beurt
    # ------------------------------------------------------------------
    @api.model
    def _agent(self, name):
        return self.env["ai.agent"].sudo().search(
            [("name", "=", name)], limit=1,
        )

    @api.model
    def _eval_schedule(self, agent):
        """De eigen planning waarmee de meting deze agent laat draaien.

        Eén blijvende, uitgezette planning per agent in plaats van een
        wegwerprecord: de runs hangen eraan, en dat is het bewijs onder
        elke uitkomst. Een weggegooide planning neemt haar runs mee.

        Draaien gebeurt met de rechten van de gebruiker waaronder het
        échte werk van deze agent loopt. Meten onder ruimere rechten dan
        de agent in productie heeft, meet een agent die niet bestaat.
        """
        Schedule = self.env["daadit.ai.agent.schedule"].sudo()
        name = "%s %s" % (SCHEDULE_PREFIX, agent.name)
        existing = Schedule.with_context(active_test=False).search(
            [("name", "=", name), ("agent_id", "=", agent.id)], limit=1,
        )
        if existing:
            return existing
        production = Schedule.with_context(active_test=False).search(
            [("agent_id", "=", agent.id)], order="active desc, id", limit=1,
        )
        run_as = production.user_id or self.env.user
        if run_as.share:
            run_as = self.env.user
        return Schedule.create({
            "name": name,
            "agent_id": agent.id,
            "prompt": _("(wordt per case gezet door de kwaliteitsmeting)"),
            "user_id": run_as.id,
            "company_id": (production.company_id or self.env.company).id,
            "active": False,
            # Ruim in de toekomst: deze planning hoort nooit door de
            # gewone cron te worden opgepakt. Uitgezet zijn is de eerste
            # grens, dit de tweede.
            "nextcall": fields.Datetime.add(
                fields.Datetime.now(), years=10,
            ),
            "interval_number": 1,
            "interval_type": "days",
            # Geen circuit breaker: een gezakte case is een meetuitkomst
            # en mag de meting niet uitzetten.
            "failure_threshold": 0,
            "alert_user_ids": [(6, 0, [])],
        })

    @api.model
    def _turn(self, agent, case, llm_model=None):
        """Laat de agent één case doen en geef de run terug.

        Testmodus: leestools draaien echt, schrijftools worden
        onderschept. Een meting mag nooit iets in de productiedatabase
        wijzigen — ook niet per ongeluk, ook niet als een case verkeerd
        is geschreven.
        """
        schedule = self._eval_schedule(agent)
        schedule.write({"prompt": case["prompt"]})
        if llm_model:
            schedule = schedule.with_context(daadit_eval_llm_model=llm_model)
        return schedule._execute(trigger="test")

    @api.model
    def _cost_usd(self, run):
        """De kosten van deze beurt, of None als niets is vastgelegd.

        None is geen nul: een provider zonder verbruiksregistratie levert
        geen meting op, en dan hoort het kostencriterium `untested` te
        zijn in plaats van geslaagd.
        """
        model = (run.usage_model or "").strip()
        if model and run.usage_row_id and model in self.env:
            row = self.env[model].sudo().browse(run.usage_row_id).exists()
            if row and "estimated_cost_usd" in row._fields:
                return row.estimated_cost_usd or 0.0
        return None

    @api.model
    def _observe(self, run, case):
        """Wat er in deze beurt feitelijk is gebeurd."""
        if not run:
            return eval_scoring.Observation(
                error=_("de beurt leverde geen run op"),
            )
        reply = run.findings or ""
        cut = reply.find(run._FOOTER_MARK)
        if cut != -1:
            # De feitenvoetregel is door ons geschreven, niet door het
            # model. Die meescoren zou de meting haar eigen tekst laten
            # beoordelen.
            reply = reply[:cut].rstrip()
        if run.state == "error":
            return eval_scoring.Observation(
                reply=reply, error=run.error or _("de run eindigde in een fout"),
            )
        tools = []
        results = []
        attempts = []
        writes = []
        for action in run.action_ids:
            tools.append(action.tool_name or "")
            results.append(_clip(action.arguments))
            results.append(_clip(action.result))
            entry = {
                "model": _model_of(action.arguments),
                "tool": action.tool_name,
            }
            if action.is_write:
                attempts.append(entry)
            if action.is_effective_write:
                writes.append(entry)
        return eval_scoring.Observation(
            reply=reply,
            tools_called=tuple(tools),
            tools_known=True,
            write_attempts=tuple(attempts),
            effective_writes=tuple(writes),
            writes_simulated=True,
            cost_usd=self._cost_usd(run),
            iterations=run.iterations or None,
            tool_results=tuple(r for r in results if r),
        )

    # ------------------------------------------------------------------
    # Meten
    # ------------------------------------------------------------------
    @api.model
    def _score_case(self, agent, case, llm_model=None):
        """Draai één case en beoordeel hem: ``(run, observatie, criteria)``."""
        run = self.env["daadit.ai.agent.schedule.run"]
        obs = eval_scoring.Observation()
        # Een case die niet gedraaid kón worden is niet gezakt: het
        # dagbudget, een bezette runlock of een ontbrekende collega
        # zeggen niets over de kwaliteit van het antwoord. Zulke
        # cases worden `niet te beoordelen` — anders leest een
        # budgetstop als een kwaliteitsdaling en valt de poort op
        # het verkeerde.
        blocked = ""
        if not agent:
            blocked = _(
                "geen collega met de naam %s in deze database",
            ) % case["agent"]
        else:
            try:
                run = self._turn(agent, case, llm_model=llm_model)
            except UserError as exc:
                blocked = str(exc)
            except Exception as exc:  # noqa: BLE001
                _logger.exception(
                    "eval: case %s liep vast", case.get("id"),
                )
                blocked = "%s: %s" % (type(exc).__name__, exc)
            else:
                if run:
                    obs = self._observe(run, case)
                else:
                    blocked = _("de beurt leverde geen run op")
        if blocked:
            criteria = [
                eval_scoring.Criterion("completed", UNTESTED, blocked),
            ]
        else:
            criteria = eval_scoring.score(case, obs)
        return run, obs, criteria

    @api.model
    def measure(self, role=None, case_ref=None, publish=True):
        """Meet een rol (of één case) en leg de uitkomst vast."""
        cases = self._load_cases(role, case_ref)
        if not cases:
            raise UserError(_(
                "Geen enkele case past bij rol %(role)s / case %(case)s.",
                role=role or "*", case=case_ref or "*",
            ))
        measurement = self.sudo().create({
            "role": role or "",
            "state": "running",
            "date": fields.Datetime.now(),
        })
        Result = self.env["daadit.ai.eval.result"].sudo()
        for case in cases:
            agent = self._agent(case["agent"])
            run, obs, criteria = self._score_case(agent, case)
            Result.create({
                "eval_run_id": measurement.id,
                "case_ref": case["id"],
                "role": case.get("role") or "",
                "agent_id": agent.id if agent else False,
                "llm_model": agent.llm_model if agent else "",
                "verdict": eval_scoring.verdict_of(criteria),
                "failed_detail": self._detail(criteria, FAIL),
                "untested_detail": self._detail(criteria, UNTESTED),
                "criteria_json": json.dumps(
                    [
                        {"name": c.name, "verdict": c.verdict,
                         "detail": c.detail}
                        for c in criteria
                    ],
                    ensure_ascii=False,
                ),
                "reply": obs.reply,
                "cost_usd": obs.cost_usd or 0.0,
                "schedule_run_id": run.id if run else False,
            })
        changes = measurement._changes()
        measurement.write({
            "state": "done",
            "regression_count": len(
                [c for c in changes if c["is_regression"]]
            ),
            "report": measurement._render(changes),
        })
        if publish:
            measurement._publish(changes)
        _logger.info(
            "eval: meting %s (%s) — %s geslaagd, %s gezakt, %s niet te "
            "beoordelen, %s verslechterd",
            measurement.id, role or "alle rollen", measurement.pass_count,
            measurement.fail_count, measurement.untested_count,
            measurement.regression_count,
        )
        return measurement

    # ------------------------------------------------------------------
    # Instructie-opvolging per model (taak 1482)
    # ------------------------------------------------------------------
    @api.model
    def _instruction_candidates(self):
        """De modelcodes die de meting per model langsloopt.

        Elk actief Mistral-model dat de router kan kiezen; zonder router
        de actieve rijen van het Mistral-register.
        """
        if "ai.router.model" in self.env:
            rows = self.env["ai.router.model"].sudo().search([
                ("provider_id.code", "=", "mistral"),
            ])
            return [code for code in rows.mapped("code") if code]
        rows = self.env["daadit.ai.mistral.model"].sudo().search([])
        return [code for code in rows.mapped("technical_name") if code]

    @api.model
    def _mark_rotation(self, code, verdict, detail):
        """Leg de uitkomst op het routermodel vast.

        Gezakt haalt het model uit de automatische routering, geslaagd
        zet het terug. Het model blijft actief in het register: dat is
        de lijst waar bestaande collega's op leunen, en een gearchiveerde
        ``-latest`` komt bij de volgende synchronisatie niet terug.
        Niet te beoordelen verandert niets.
        """
        if "ai.router.model" not in self.env:
            return
        rows = self.env["ai.router.model"].sudo().with_context(
            active_test=False,
        ).search([
            ("code", "=", code), ("provider_id.code", "=", "mistral"),
        ])
        vals = {
            "instruction_verdict": verdict,
            "instruction_checked_at": fields.Datetime.now(),
            "instruction_detail": (detail or "")[:250],
        }
        if verdict == FAIL:
            vals["rotation_excluded"] = True
        elif verdict == PASS:
            vals["rotation_excluded"] = False
        rows.write(vals)

    @api.model
    def measure_models(self, codes=None, case_ref=INSTRUCTION_CASE,
                       publish=True):
        """Draai de instructie-case over elk model en zet zakkers uit de
        routering."""
        cases = self._load_cases(case_ref=case_ref)
        if not cases:
            raise UserError(_("De case %s bestaat niet.") % case_ref)
        case = cases[0]
        agent = self._agent(case["agent"])
        if not agent:
            raise UserError(_(
                "Geen collega met de naam %s om de meting mee te doen.",
            ) % case["agent"])
        measurement = self.sudo().create({
            "role": "%s:modellen" % (case.get("role") or ""),
            "state": "running",
            "date": fields.Datetime.now(),
        })
        Result = self.env["daadit.ai.eval.result"].sudo()
        lines = []
        failed = []
        for code in (codes or self._instruction_candidates()):
            run, obs, criteria = self._score_case(agent, case, llm_model=code)
            follow = [c for c in criteria if c.name == INSTRUCTION_CRITERION]
            verdict = follow[0].verdict if follow else UNTESTED
            detail = (
                follow[0].detail if follow
                else self._detail(criteria, UNTESTED)
            )
            Result.create({
                "eval_run_id": measurement.id,
                "case_ref": case["id"],
                "role": case.get("role") or "",
                "agent_id": agent.id,
                "llm_model": code,
                "verdict": verdict,
                "failed_detail": detail if verdict == FAIL else "",
                "untested_detail": detail if verdict == UNTESTED else "",
                "reply": obs.reply,
                "cost_usd": obs.cost_usd or 0.0,
                "schedule_run_id": run.id if run else False,
            })
            self._mark_rotation(code, verdict, detail)
            if verdict == FAIL:
                failed.append(code)
            lines.append("- %s: %s%s" % (
                code, verdict, (" (%s)" % detail) if detail else "",
            ))
        report = [_("Instructie-opvolging per model (%s)") % case["id"], ""]
        report += lines
        users = self.env["ai.agent"].sudo().search(
            [("llm_model", "in", failed)],
        ) if failed else self.env["ai.agent"]
        if users:
            report += ["", _(
                "Deze collega's draaien op een model dat de opbouw niet "
                "volgt; een ander model kiezen is een menselijk besluit: %s",
            ) % ", ".join(users.mapped("name"))]
        measurement.write({"state": "done", "report": "\n".join(report)})
        if publish:
            measurement._publish_models()
        return measurement

    def _publish_models(self):
        """Eén rollende taak voor de meting per model."""
        self.ensure_one()
        project = self._project()
        if not project:
            return self.env["project.task"]
        title = "%s %s geslaagd, %s gezakt, %s niet te beoordelen" % (
            MODELS_PREFIX, self.pass_count, self.fail_count,
            self.untested_count,
        )
        if "ai.router.model" in self.env:
            Model = self.env["ai.router.model"].sudo()
            mistral = [("provider_id.code", "=", "mistral")]
            title = _(
                "%s %s van %s modellen uit de routering, %s nog niet gemeten",
            ) % (
                MODELS_PREFIX,
                Model.search_count(mistral + [("rotation_excluded", "=", True)]),
                Model.search_count(mistral),
                Model.search_count(
                    mistral + [("instruction_checked_at", "=", False)],
                ),
            )
        task = self._open_task(project, MODELS_PREFIX)
        if task:
            task.write({"name": title})
        else:
            task = self.env["project.task"].sudo().create({
                "name": title, "project_id": project.id, "priority": "0",
            })
        task.message_post(body=Markup(self._as_html(self.report or "")))
        self.task_id = task
        return task

    @api.model
    def _due_model_codes(self, limit=None):
        """De modellen die het langst niet gemeten zijn, nooit gemeten eerst."""
        limit = limit or MODELS_PER_RUN
        if "ai.router.model" not in self.env:
            return self._instruction_candidates()[:limit]
        stale = fields.Datetime.now() - timedelta(days=REMEASURE_DAYS)
        rows = self.env["ai.router.model"].sudo().search([
            ("provider_id.code", "=", "mistral"),
            ("code", "!=", False),
            "|", ("instruction_checked_at", "=", False),
            ("instruction_checked_at", "<", stale),
        ], order="instruction_checked_at asc nulls first, id", limit=limit)
        return rows.mapped("code")

    @api.model
    def _close_hung_attempts(self):
        """Sluit metingen af die geen uitkomst meer krijgen.

        Een model dat blijft hangen, zou anders elke ronde als eerste
        terugkomen en de rest van het register blokkeren. Het telt als
        niet te beoordelen en blijft in de routering.
        """
        cutoff = fields.Datetime.now() - timedelta(minutes=HUNG_MINUTES)
        self.sudo().search([
            ("role", "=like", "%:modellen"),
            ("state", "=", "running"),
            ("date", "<", cutoff),
        ]).write({
            "state": "done",
            "report": _("Afgebroken: de meting kreeg geen uitkomst."),
        })
        if "ai.router.model" not in self.env:
            return
        runs = self.env["daadit.ai.agent.schedule.run"].sudo().search([
            ("schedule_id.name", "=like", SCHEDULE_PREFIX + " %"),
            ("state", "in", ("running", "error")),
            ("model", "!=", False),
            ("create_date", "<", cutoff),
            ("create_date", ">", fields.Datetime.now()
             - timedelta(days=REMEASURE_DAYS)),
        ])
        Model = self.env["ai.router.model"].sudo()
        for run in runs:
            rows = Model.search([
                ("code", "=", run.model),
                ("provider_id.code", "=", "mistral"),
                "|", ("instruction_checked_at", "=", False),
                ("instruction_checked_at", "<", run.create_date),
            ])
            if rows:
                self._mark_rotation(run.model, UNTESTED, _(
                    "Geen antwoord binnen de tijd (run %s)",
                ) % run.id)

    @api.model
    def _cron_measure_models(self):
        self._close_hung_attempts()
        codes = self._due_model_codes()
        if not codes:
            return
        try:
            self.measure_models(codes=codes)
        except Exception:  # noqa: BLE001
            _logger.exception("eval: meting per model mislukte")

    @staticmethod
    def _detail(criteria, verdict):
        return "\n".join(
            "%s: %s" % (c.name, c.detail) if c.detail else c.name
            for c in criteria if c.verdict == verdict
        )

    # ------------------------------------------------------------------
    # Vergelijken met de vorige meting
    # ------------------------------------------------------------------
    def _baseline(self):
        """De vorige afgeronde meting van dezelfde rol, of leeg."""
        self.ensure_one()
        return self.sudo().search([
            ("id", "!=", self.id),
            ("state", "=", "done"),
            # Een lege rol is 'alle rollen' en staat als NULL in de
            # database; vergelijken met "" vindt die niet terug.
            ("role", "=", self.role if self.role else False),
            ("date", "<=", self.date),
        ], order="date desc, id desc", limit=1)

    def _changes(self):
        """Elke case waarvan de uitkomst afwijkt van de vorige meting.

        Een case die niet in de basismeting stond, kan niet verslechterd
        zijn: dan is er niets om tegen af te zetten. Anders zou elke
        nieuwe case de poort laten vallen en zou niemand er nog een
        durven toevoegen.
        """
        self.ensure_one()
        baseline = self._baseline()
        if not baseline:
            return []
        was = {r.case_ref: r.verdict for r in baseline.result_ids}
        changes = []
        for result in self.result_ids:
            before = was.get(result.case_ref)
            if before is None or before == result.verdict:
                continue
            changes.append({
                "case_ref": result.case_ref,
                "role": result.role or "?",
                "was": before,
                "now": result.verdict,
                "detail": (result.failed_detail or "").replace("\n", "; "),
                "is_regression": (
                    result.verdict == FAIL and before in (PASS, UNTESTED)
                ),
            })
        changes.sort(key=lambda c: (not c["is_regression"], c["case_ref"]))
        return changes

    # ------------------------------------------------------------------
    # Rapporteren
    # ------------------------------------------------------------------
    def _render(self, changes):
        self.ensure_one()
        lines = [
            "# Eval-rapport",
            "",
            "%s · %s cases · %s geslaagd · %s gezakt · %s niet te "
            "beoordelen · kosten $%.3f" % (
                fields.Datetime.to_string(self.date), self.case_count,
                self.pass_count, self.fail_count, self.untested_count,
                self.cost_usd,
            ),
            "",
            "| Case | Rol | Model | Uitkomst | Kosten | Gezakte criteria |",
            "|---|---|---|---|---|---|",
        ]
        for result in self.result_ids:
            lines.append("| %s | %s | %s | %s | $%.4f | %s |" % (
                result.case_ref, result.role or "—",
                result.llm_model or "—", result.verdict,
                result.cost_usd,
                (result.failed_detail or "—").replace("\n", "; "),
            ))
        untested = [r for r in self.result_ids if r.untested_detail]
        if untested:
            lines += ["", "## Niet te beoordelen", ""]
            lines += [
                "- `%s` — %s" % (
                    r.case_ref, (r.untested_detail or "").replace("\n", "; "),
                )
                for r in untested
            ]
        lines += ["", "## Verschil met de vorige meting", ""]
        baseline = self._baseline()
        if not baseline:
            lines.append(
                "Geen eerdere meting van deze rol: dit is de basismeting."
            )
        elif not changes:
            lines.append(
                "Geen enkele case veranderde van uitkomst tegenover de "
                "meting van %s." % fields.Datetime.to_string(baseline.date)
            )
        else:
            lines += [
                "| Case | Rol | Was | Nu | Gezakte criteria |",
                "|---|---|---|---|---|",
            ]
            lines += [
                "| %s | %s | %s | %s | %s |" % (
                    c["case_ref"], c["role"], c["was"], c["now"],
                    c["detail"] or "—",
                )
                for c in changes
            ]
        lines += ["", "## Wat deze meting niet ziet", ""]
        lines += ["- %s" % limit for limit in eval_scoring.LIMITS]
        lines.append(
            "- Onafhankelijkheid: deze meting draait in dezelfde database "
            "als het werk dat ze beoordeelt. Het losse harnas in "
            "daadit_odoo/evals is de onafhankelijke tegenhanger."
        )
        return "\n".join(lines) + "\n"

    def _project(self):
        """Het bord waarop de meting rapporteert.

        Bewust dezelfde instelling als de deploy-wachter gebruikt: twee
        parameters voor hetzelfde bord leveren op een dag twee borden op.
        """
        return self.env["daadit.ai.deploy.watch"]._watch_project()

    def _open_task(self, project, prefix):
        return self.env["project.task"].sudo().search([
            ("project_id", "=", project.id),
            ("name", "=like", prefix + "%"),
            ("state", "not in", ("1_done", "1_canceled")),
        ], order="id desc", limit=1)

    def _publish(self, changes):
        """Zet het rapport op het bord en maak van verslechtering een taak."""
        self.ensure_one()
        project = self._project()
        if not project:
            _logger.warning(
                "eval: geen bord gevonden; het rapport van meting %s staat "
                "alleen op de meting zelf", self.id,
            )
            return self.env["project.task"]
        title = "%s %s — %s geslaagd, %s gezakt, %s niet te beoordelen, "\
                "$%.2f" % (
                    TASK_PREFIX, self.role or _("alle rollen"),
                    self.pass_count, self.fail_count, self.untested_count,
                    self.cost_usd,
                )
        body = self._as_html(self.report or "")
        task = self._open_task(project, TASK_PREFIX)
        if task:
            # Eén rollende taak per bord: een taak per meting begraaft
            # het bord, terwijl de geschiedenis in de chatter leesbaar
            # blijft.
            task.write({"name": title})
        else:
            task = self.env["project.task"].sudo().create({
                "name": title,
                "project_id": project.id,
                "priority": "0",
            })
        task.message_post(body=Markup(body))
        self.task_id = task
        self._publish_gate(project, changes)
        return task

    def _publish_gate(self, project, changes):
        """Verslechtering wordt een taak; niets sluit zichzelf."""
        self.ensure_one()
        regressions = [c for c in changes if c["is_regression"]]
        task = self._open_task(project, GATE_PREFIX)
        if not regressions:
            if task:
                task.message_post(body=_(
                    "Deze meting gaf geen verslechtering meer. Sluiten "
                    "is een menselijk besluit; ik laat de taak open."
                ))
            return task
        name = "%s %s case(s) verslechterd sinds de vorige meting" % (
            GATE_PREFIX, len(regressions),
        )
        body = _(
            "<p><b>%s case(s) zakken nu en slaagden bij de vorige "
            "meting.</b></p>"
        ) % len(regressions) + self._as_html(self.report or "")
        if task:
            task.write({"name": name, "priority": "1"})
            task.message_post(body=Markup(body))
            return task
        return self.env["project.task"].sudo().create({
            "name": name,
            "project_id": project.id,
            "priority": "1",
            "description": body,
        })

    # ------------------------------------------------------------------
    # Cadans
    # ------------------------------------------------------------------
    @api.model
    def _cron_roles(self):
        """De rollen die deze ronde gemeten worden.

        De rol die het langst niet is gemeten gaat voor. Zo komt elke
        collega aan de beurt zonder dat één nacht alle beurten van alle
        rollen betaalt.
        """
        icp = self.env["ir.config_parameter"].sudo()
        allowed = [
            piece.strip()
            for piece in (icp.get_param(ROLES_PARAM, "") or "").split(",")
            if piece.strip()
        ]
        roles = [r for r in self._roles() if not allowed or r in allowed]
        try:
            per_run = int(
                icp.get_param(ROLES_PER_RUN_PARAM, DEFAULT_ROLES_PER_RUN)
                or DEFAULT_ROLES_PER_RUN
            )
        except (TypeError, ValueError):
            per_run = DEFAULT_ROLES_PER_RUN
        per_run = max(1, per_run)
        last = {}
        for measurement in self.sudo().search([("state", "=", "done")]):
            role = measurement.role or ""
            if role in roles and role not in last:
                last[role] = measurement.date
        roles.sort(key=lambda r: (last.get(r) is not None, last.get(r) or ""))
        return roles[:per_run]

    @api.model
    def _cron_measure(self):
        """De cadans van de meting: per ronde een paar rollen."""
        for role in self._cron_roles():
            try:
                self.measure(role=role)
            except Exception:  # noqa: BLE001
                _logger.exception("eval: meting van rol %s mislukte", role)

    # ------------------------------------------------------------------
    def action_measure_now(self):
        """Meet de rol van deze meting opnieuw."""
        self.ensure_one()
        new = self.measure(role=self.role or None)
        return {
            "type": "ir.actions.act_window",
            "res_model": "daadit.ai.eval.run",
            "res_id": new.id,
            "view_mode": "form",
        }
