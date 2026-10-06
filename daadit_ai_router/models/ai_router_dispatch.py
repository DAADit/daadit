import json
import logging
import re

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class AiRouterDispatch(models.Model):
    """Werkregel (fase 2): classificeert nieuwe werkitems via de gateway en
    legt een concept-voorstel (To-Do-activiteit) bij de juiste collega.

    Draft-only: de regel wijst nooit zelf toe; een mens beoordeelt het
    voorstel en handelt."""

    _name = 'ai.router.dispatch'
    _description = 'AI Router Werkregel'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    source = fields.Selection(
        [('crm.lead', 'CRM-lead')], required=True, default='crm.lead',
        help="Bronmodel; meer bronnen volgen in latere versies.")
    purpose = fields.Char(
        required=True, default='classify',
        help="Routesleutel waarmee de classificatie-aanroep door de "
             "gateway loopt.")
    team_id = fields.Many2one(
        'crm.team', string="Beperk tot team",
        help="Alleen leads van dit team; leeg = alle teams.")
    max_per_run = fields.Integer(
        default=10,
        help="Maximaal aantal voorstellen per run (throttle).")
    activity_summary = fields.Char(
        required=True, default="AI-routering: voorstel opvolging",
        help="Onderwerp van de concept-activiteit; dient ook als "
             "dubbel-voorstel-detectie.")
    last_run = fields.Datetime(readonly=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)

    @api.model
    def _cron_run_all(self):
        for rule in self.search([]):
            try:
                rule._run()
            except Exception:
                _logger.exception(
                    "AI-router werkregel '%s' faalde", rule.name)

    def _run(self):
        self.ensure_one()
        if self.source != 'crm.lead':
            return 0
        started_at = fields.Datetime.now()
        if not self.last_run:
            # Koude start: alleen de referentietijd zetten, geen backlog
            # verwerken — anders krijgen de oudste leads alsnog voorstellen.
            self.last_run = started_at
            _logger.info(
                "Werkregel '%s': eerste run zet alleen de referentietijd; "
                "voorstellen volgen voor leads van na dit moment.", self.name)
            return 0
        domain = [('type', '=', 'lead'),
                  ('create_date', '>', self.last_run)]
        if self.team_id:
            domain.append(('team_id', '=', self.team_id.id))
        leads = self.env['crm.lead'].search(
            domain, order='create_date', limit=self.max_per_run)
        handled = 0
        for lead in leads:
            if self._already_proposed(lead):
                continue
            try:
                if self._propose(lead):
                    handled += 1
            except Exception as exc:
                # één kapotte lead mag de rest van de run niet blokkeren
                _logger.warning(
                    "Werkregel '%s': lead %s overgeslagen (%s)",
                    self.name, lead.id, exc)
        self.last_run = started_at
        _logger.info(
            "Werkregel '%s': %s voorstel(len) uit %s nieuwe lead(s)",
            self.name, handled, len(leads))
        return handled

    def _already_proposed(self, lead):
        return bool(self.env['mail.activity'].sudo().search_count([
            ('res_model', '=', 'crm.lead'),
            ('res_id', '=', lead.id),
            ('summary', '=', self.activity_summary),
        ]))

    def _candidates(self, lead):
        if lead.team_id and lead.team_id.member_ids:
            return lead.team_id.member_ids
        if self.team_id and self.team_id.member_ids:
            return self.team_id.member_ids
        return self.env['crm.team'].search([]).member_ids

    def _build_prompt(self, lead, candidates):
        lines = [
            "Je bent de werkverdeler van DAADit. Kies uit de kandidaten de "
            "beste opvolger voor deze nieuwe CRM-lead.",
            'Antwoord UITSLUITEND met JSON: {"login": "...", "reden": "..."}'
            " — reden in één korte Nederlandse zin.",
            "",
            "Kandidaten:",
        ]
        lines += [
            "- login=%s naam=%s" % (user.login, user.name)
            for user in candidates
        ]
        lines += [
            "",
            "Lead:",
            "Naam: %s" % (lead.name or '-'),
            "Bedrijf: %s" % (
                lead.partner_name or lead.partner_id.display_name or '-'),
            "E-mail: %s" % (lead.email_from or '-'),
            "Omschrijving: %s" % ((lead.description or '-')[:1500]),
        ]
        return "\n".join(lines)

    def _parse_choice(self, content, candidates):
        match = re.search(r'\{.*\}', content or '', re.S)
        if not match:
            return False
        try:
            data = json.loads(match.group(0))
        except ValueError:
            return False
        login = (data.get('login') or '').strip()
        user = candidates.filtered(lambda u: u.login == login)[:1]
        if not user:
            return False
        reden = (data.get('reden') or '').strip() or _("geen motivatie")
        return user, reden

    def _propose(self, lead):
        candidates = self._candidates(lead)
        if not candidates:
            return False
        result = self.env['ai.router'].run(
            self.purpose,
            self._build_prompt(lead, candidates),
            caller='dispatch:%s' % self.name,
        )
        choice = self._parse_choice(result.get('content'), candidates)
        if not choice:
            _logger.warning(
                "Werkregel '%s': antwoord voor lead %s niet bruikbaar",
                self.name, lead.id)
            return False
        user, reden = choice
        lead.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=self.activity_summary,
            note=_(
                "Voorstel van de AI-router: <b>%(user)s</b>.<br/>"
                "Motivatie: %(reden)s<br/>"
                "<i>Concept — beoordeel en wijs zelf toe.</i>",
                user=user.name, reden=reden),
            user_id=user.id,
        )
        return True
