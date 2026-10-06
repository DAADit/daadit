# -*- coding: utf-8 -*-
"""
DAADit Project Dashboard — portal-safe data layer (generic).

Single source of truth for what dashboard data a user is allowed to see.
Uses sudo() under the hood so we can fetch hr.employee, planning.slot,
calendar.event, etc. that portal users can't read directly. Then
strictly whitelists fields and records per role before returning.

DO NOT add fields to the portal whitelist without considering whether
they leak commercially sensitive data (salaries, internal cost rates,
private partner notes, etc.).
"""
import base64
import binascii
import json
import logging
import re
from datetime import datetime, timedelta

from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError

import werkzeug.exceptions

_logger = logging.getLogger(__name__)


def _decode_holder_cfg(holder_description):
    """The dashboard config holder task stores its data as a base64-encoded
    JSON blob wrapped in HTML. Strip HTML, strip non-base64 chars, decode,
    parse JSON. Returns {} on any failure (never raises).
    """
    if not holder_description:
        return {}
    try:
        raw = re.sub(r'<[^>]*>', '', holder_description)        # strip HTML tags
        raw = re.sub(r'&[a-zA-Z#0-9]+;', '', raw)                # strip HTML entities
        raw = re.sub(r'[^A-Za-z0-9+/=]', '', raw)                # keep only b64 chars
        if not raw:
            return {}
        decoded = base64.b64decode(raw).decode('utf-8', errors='replace')
        obj = json.loads(decoded)
        return obj if isinstance(obj, dict) else {}
    except (binascii.Error, ValueError, UnicodeDecodeError) as e:
        _logger.debug("holder cfg decode failed: %s", e)
        return {}


def _team_partner_ids(project_id):
    """Return the set of res.partner ids listed in the dashboard's Team tab.

    Each holder.team entry has shape (see views/4435 daadit_dashboard_team):
        {name, role, org, mail, src: 'intern'|'klant'|'free',
         uid: <res.users id or 0>, pid: <res.partner id or 0>, ...}

    Robust against duplicate holder tasks (which can happen after a staging
    sync): iterates EVERY matching holder and merges team entries. Resolves
    both `pid` directly AND `uid → res.users.partner_id` so any way the
    project leader added someone (by user account or by contact), that
    person's partner_id ends up in the access set.

    Returns set() if no holder task or no team data anywhere.
    """
    Task = request.env['project.task'].sudo().with_context(active_test=False)
    holders = Task.search([
        ('project_id', '=', project_id),
        ('name', 'ilike', '__dashboard_cfg__'),
    ])

    direct_pids = set()
    uids = set()
    for holder in holders:
        if not holder.description:
            continue
        cfg = _decode_holder_cfg(holder.description)
        team = cfg.get('team') or []
        for m in team:
            if not isinstance(m, dict):
                continue
            pid = m.get('pid')
            if isinstance(pid, int) and pid > 0:
                direct_pids.add(pid)
            uid = m.get('uid')
            if isinstance(uid, int) and uid > 0:
                uids.add(uid)

    out = set(direct_pids)
    if uids:
        users = request.env['res.users'].sudo().browse(list(uids)).read(['partner_id'])
        for u in users:
            partner = u.get('partner_id')
            if partner:
                out.add(partner[0] if isinstance(partner, (list, tuple)) else partner)
    return out


# =====================================================================
# Field whitelists
# =====================================================================
# Design intent (v19.0.1.0.3+):
#   Once a user is authorized for the project via _project_or_403() — i.e.,
#   they are internal, a collaborator, a follower, or share the customer's
#   commercial_partner — they get THE SAME data as an internal user. No
#   field stripping per role. Access is gated at the project level, not at
#   the field level. Anyone without project access gets 403.

TASK_FIELDS = [
    'id', 'name', 'description', 'parent_id', 'child_ids', 'stage_id',
    'user_ids', 'partner_id', 'date_deadline', 'planned_date_begin',
    'priority', 'state', 'sequence', 'tag_ids', 'milestone_id',
    'progress', 'allocated_hours', 'effective_hours', 'total_hours_spent',
    'display_in_project',
    'color',
    # NOTE: `kanban_state` was removed from project.task in Odoo 19;
    # use `state` (selection) instead.
    'create_uid', 'create_date', 'write_uid', 'write_date',
]

STAGE_FIELDS = ['id', 'name', 'sequence', 'fold']

USER_FIELDS = ['id', 'name', 'login', 'partner_id', 'active']

PARTNER_FIELDS = ['id', 'name', 'email', 'phone', 'function']

PROJECT_FIELDS = [
    'id', 'name', 'partner_id', 'user_id', 'date', 'date_start',
    'description', 'favorite_user_ids', 'collaborator_ids',
    'privacy_visibility',
]

# Fasen: de aantallen erbij, want een fase zonder voortgang zegt niets.
# `task_count` en `done_task_count` rekent Odoo zelf uit op de milestone.
MILESTONE_FIELDS = [
    'id', 'name', 'deadline', 'is_reached', 'reached_date', 'sequence',
    'task_count', 'done_task_count',
]

# Voortgangsrapportages (project.update). Het dashboard toont de
# laatste; de reeks eronder maakt zichtbaar of de stand vooruitgaat.
PROJECT_UPDATE_FIELDS = [
    'id', 'name', 'date', 'status', 'progress', 'description',
    'user_id', 'create_date',
]
PROJECT_UPDATE_LIMIT = 8

# Het doel staat in de projectbeschrijving, in een eigen blok dat de
# projectmanager-agent bijwerkt (zie daadit_ai_mistral, model
# ai_agent_project_report). Het dashboard leest datzelfde blok, zodat
# doel en rapportage nooit twee verschillende doelen tonen.
# De marker is een class: de beschrijving is een gesanitiseerd
# html-veld en Odoo's sanitizer verwijdert onbekende data-* attributen.
GOAL_BLOCK_RE = re.compile(
    r'<div[^>]*class="[^"]*\bo_daadit_goal\b[^"]*"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)


def _project_goal(description):
    """The project goal as plain text, or '' when it isn't set yet."""
    match = GOAL_BLOCK_RE.search(description or '')
    if not match:
        return ''
    text = re.sub(r'<[^>]*>', ' ', match.group(1))
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
    text = re.sub(r'^\s*Doel\s*', '', re.sub(r'\s+', ' ', text).strip())
    return text.strip()


CALENDAR_FIELDS = [
    'id', 'name', 'description', 'start', 'stop', 'allday',
    'location', 'videocall_location', 'partner_ids', 'user_id',
    'rrule', 'recurrency', 'recurrence_id', 'duration',
    'use_teams_meeting', 'is_teams_videocall', 'ms_teams_join_url',
]

EMPLOYEE_FIELDS = [
    'id', 'name', 'user_id', 'resource_id', 'work_email',
    'job_title', 'department_id',
]

PLANNING_SLOT_FIELDS = [
    'id', 'resource_id', 'project_id', 'start_datetime', 'end_datetime',
    'allocated_hours', 'allocated_percentage', 'role_id',
]

# Models + methods allowlist for the generic /sudo-rpc passthrough.
# These are READ-ONLY operations the dashboard JS performs across all tabs.
# Once authorized via _project_or_403, a portal user gets the same reads as
# an internal user — via sudo, bypassing Odoo's row-level ACL.
SUDO_RPC_ALLOWLIST = {
    'project.task':           {'read', 'search_read', 'search', 'search_count',
                               'name_search', 'read_group', 'web_read_group'},
    'project.task.type':      {'read', 'search_read', 'search', 'search_count'},
    'project.project':        {'read', 'search_read', 'search', 'search_count'},
    'project.milestone':      {'read', 'search_read', 'search', 'search_count'},
    'project.update':         {'read', 'search_read', 'search', 'search_count'},
    'project.collaborator':   {'read', 'search_read', 'search', 'search_count'},
    'res.users':              {'read', 'search_read', 'search', 'search_count', 'name_search'},
    'res.partner':            {'read', 'search_read', 'search', 'search_count', 'name_search'},
    'calendar.event':         {'read', 'search_read', 'search', 'search_count'},
    'calendar.alarm':         {'read', 'search_read', 'search'},
    'hr.employee':            {'read', 'search_read', 'search', 'search_count'},
    'planning.slot':          {'read', 'search_read', 'search', 'search_count', 'read_group'},
    'mail.message':           {'read', 'search_read', 'search', 'search_count'},
    'mail.notification':      {'read', 'search_read', 'search'},
    'mail.activity':          {'read', 'search_read', 'search'},
    'mail.activity.type':     {'read', 'search_read', 'search'},
    'mail.followers':         {'read', 'search_read', 'search'},
    'ir.attachment':          {'read', 'search_read', 'search'},
    'project.tags':           {'read', 'search_read', 'search', 'name_search'},
}

# Writes allowlisted via sudo for users with edit access (internal OR
# team-member portal). Project-scope is enforced per-method below so a
# portal user can't accidentally (or maliciously) write outside their project.
SUDO_WRITE_ALLOWLIST = {
    'project.task':         {'create', 'write', 'unlink'},
    'project.milestone':    {'create', 'write', 'unlink'},
    'calendar.event':       {'create', 'write', 'unlink'},
    'calendar.alarm':       {'create'},
    'res.partner':          {'create'},  # team-modal "Nieuwe contactpersoon"
}
# Models whose `create` MUST include project_id == current project.
PROJECT_SCOPED_CREATE = {'project.task', 'project.milestone'}
# Models whose write/unlink targets MUST belong to the current project.
PROJECT_SCOPED_RECORDS = {'project.task', 'project.milestone', 'calendar.event'}


# =====================================================================
# Access helpers
# =====================================================================

def _project_or_403(project_id):
    """Return the project as sudo, or raise AccessError if denied.

    Access criteria (any one grants access):
      1. Internal user (share=False) — full access for ALL internal users.
      2. User listed in the dashboard Team tab (holder.team[].partnerId).
      3. Standard project sharing: collaborator, follower, or customer-link.

    Anyone not matching any of these gets a 403.
    """
    user = request.env.user
    project = request.env['project.project'].sudo().browse(project_id).exists()
    if not project:
        raise AccessError("Project not found.")

    # (1) Internal users: blanket access. The dashboard is an internal
    # operational tool — every employee should be able to look at it,
    # even those not formally attached to this specific project.
    if not user.share:
        return project

    # (2) Team-tab membership — primary criterion for portal users.
    team_pids = _team_partner_ids(project.id)
    if user.partner_id.id in team_pids:
        return project
    # Also check commercial_partner — when a sub-contact is in the team
    # but the user logs in with the parent contact (or vice versa), the
    # team-membership should still resolve.
    commercial_pid = user.partner_id.commercial_partner_id.id
    if commercial_pid and commercial_pid in team_pids:
        return project

    # (3) Standard Odoo project sharing — fallback so existing collaborators
    # keep working even if they aren't (yet) in the dashboard Team tab.
    Collab = request.env['project.collaborator'].sudo()
    is_collab = bool(Collab.search_count([
        ('project_id', '=', project.id),
        ('partner_id', '=', user.partner_id.id),
    ]))
    is_follower = user.partner_id.id in (
        project.message_partner_ids.ids if project.message_partner_ids else []
    )
    is_customer = (
        bool(project.partner_id)
        and commercial_pid
        == project.partner_id.commercial_partner_id.id
    )
    if not (is_collab or is_follower or is_customer):
        raise AccessError(
            "Geen toegang tot dit project — je staat niet in het Team van de "
            "projectrapportage en bent geen interne gebruiker. Vraag de projectleider "
            "om je toe te voegen."
        )

    return project


def _can_edit(project):
    """Internal always; portal only if full-access collaborator OR explicit team member."""
    user = request.env.user
    if not user.share:
        return True
    # Team members get edit-rights so they can save settings, holder cfg, etc.
    team_pids = _team_partner_ids(project.id)
    if user.partner_id.id in team_pids:
        return True
    commercial_pid = user.partner_id.commercial_partner_id.id
    if commercial_pid and commercial_pid in team_pids:
        return True
    # Otherwise fall back to standard collaborator check.
    collab = request.env['project.collaborator'].sudo().search([
        ('project_id', '=', project.id),
        ('partner_id', '=', user.partner_id.id),
    ], limit=1)
    return bool(collab and not collab.limited_access)


def _is_portal():
    return bool(request.env.user.share)


def _filter_fields(values, allowed):
    if allowed is None:
        return None
    if isinstance(values, list):
        return [_filter_fields(v, allowed) for v in values]
    return {k: v for k, v in values.items() if k in allowed}


def _ok(payload):
    return {'ok': True, 'data': payload}


def _err(msg, code=400):
    return {'ok': False, 'error': msg, 'code': code}


# Methods that act on a recordset (Odoo's `call_kw` browses ids first,
# then calls the method on the resulting recordset). We must do the same;
# otherwise calling Model.read([ids], fields) interprets the id-list as
# the fields argument (Model.read signature is `read(fields, load)`).
RECORDSET_METHODS = frozenset({
    'read', 'write', 'unlink', 'copy', 'browse',
    'export_data', 'message_post', 'message_subscribe',
    'message_unsubscribe', 'toggle_active', 'action_archive',
    'action_unarchive', 'message_format',
})

# Methods that take a domain as their first positional arg. The sudo-rpc
# endpoint AND-prepends a project-scope filter into that domain so a user
# with project X access can never read records from project Y.
DOMAIN_METHODS = frozenset({
    'search', 'search_read', 'search_count', 'read_group', 'web_read_group',
})

# Per-model field whitelist for sudo-rpc reads. Anything not listed is
# stripped from the requested fields BEFORE the read happens. Prevents
# leaking sensitive fields like private_email, salary, contracts, etc.
FIELD_WHITELIST = {
    'project.task':         set(TASK_FIELDS) | {'description'},
    'project.task.type':    set(STAGE_FIELDS) | {'project_ids', 'description'},
    'project.project':      set(PROJECT_FIELDS),
    'project.milestone':    set(MILESTONE_FIELDS) | {'project_id'},
    'project.update':       set(PROJECT_UPDATE_FIELDS) | {'project_id'},
    'project.collaborator': {'id', 'partner_id', 'limited_access', 'project_id'},
    'project.tags':         {'id', 'name', 'color'},
    'res.users':            set(USER_FIELDS) | {'image_128', 'avatar_128'},
    'res.partner':          set(PARTNER_FIELDS) | {
        'image_128', 'avatar_128', 'commercial_partner_id', 'parent_id',
        'is_company', 'type', 'company_name'},
    'calendar.event':       set(CALENDAR_FIELDS),
    'calendar.alarm':       {'id', 'name', 'duration', 'interval', 'alarm_type'},
    'hr.employee':          set(EMPLOYEE_FIELDS) | {'image_128', 'avatar_128'},
    'planning.slot':        set(PLANNING_SLOT_FIELDS) | {'employee_id'},
    'mail.message':         {'id', 'subject', 'body', 'date', 'author_id',
                             'model', 'res_id', 'message_type',
                             'subtype_id', 'partner_ids'},
    'mail.notification':    {'id', 'mail_message_id', 'res_partner_id',
                             'notification_type', 'notification_status',
                             'is_read'},
    'mail.activity':        {'id', 'res_model', 'res_id', 'activity_type_id',
                             'summary', 'date_deadline', 'user_id', 'state'},
    'mail.activity.type':   {'id', 'name', 'icon', 'category', 'sequence'},
    'mail.followers':       {'id', 'res_model', 'res_id', 'partner_id', 'subtype_ids'},
    'ir.attachment':        {'id', 'name', 'mimetype', 'file_size',
                             'res_model', 'res_id', 'create_date',
                             'create_uid', 'type', 'url'},
}


def _project_scope_domain(model, project):
    """Return a Polish-notation domain that limits the model's records to
    those belonging to this project. Used to AND-prepend the user-supplied
    domain so a user with project X cannot read records of project Y.

    Returns an empty list `[]` for shared / safe models (stage definitions,
    tag dictionaries, alarm definitions) where cross-project visibility is
    acceptable.
    """
    env = request.env
    pid = project.id

    if model == 'project.task':
        return [('project_id', '=', pid)]
    if model == 'project.project':
        return [('id', '=', pid)]
    if model == 'project.milestone':
        return [('project_id', '=', pid)]
    if model == 'project.update':
        return [('project_id', '=', pid)]
    if model == 'project.collaborator':
        return [('project_id', '=', pid)]
    if model == 'planning.slot':
        return [('project_id', '=', pid)]
    if model == 'calendar.event':
        return [('res_model', '=', 'project.project'), ('res_id', '=', pid)]
    if model == 'project.task.type':
        # Stages can legitimately be shared. Limit to stages used by THIS
        # project (m2m) so unrelated workflows don't leak.
        return [('project_ids', 'in', [pid])]
    if model == 'calendar.alarm':
        # Shared resource (e.g. "15 min before"). Not sensitive.
        return []
    if model == 'project.tags':
        # Tag names. Low sensitivity, dashboard JS does name_search on them.
        return []
    if model == 'mail.activity.type':
        # Activity type definitions, low sensitivity.
        return []

    # ---- res.users: project members + assignees + the requesting user ----
    if model == 'res.users':
        uids = set()
        uids.update(project.favorite_user_ids.ids)
        if project.user_id:
            uids.add(project.user_id.id)
        # Users assigned to any task in this project
        tasks = env['project.task'].sudo().search([('project_id', '=', pid)])
        for t in tasks:
            uids.update(t.user_ids.ids)
        # Users referenced from holder team-tab uids
        team_pids = _team_partner_ids(pid)  # resolves uid→pid
        if team_pids:
            team_users = env['res.users'].sudo().search([('partner_id', 'in', list(team_pids))])
            uids.update(team_users.ids)
        # Requesting user can always read themselves
        uids.add(request.env.user.id)
        return [('id', 'in', list(uids))]

    # ---- res.partner: project customer hierarchy + attendees + team ----
    if model == 'res.partner':
        partner_ids = set()
        if project.partner_id:
            commercial = project.partner_id.commercial_partner_id
            partner_ids.add(commercial.id)
            partner_ids.update(commercial.child_ids.ids)
        # Calendar event attendees on this project
        events = env['calendar.event'].sudo().search([
            ('res_model', '=', 'project.project'), ('res_id', '=', pid),
        ])
        for ev in events:
            partner_ids.update(ev.partner_ids.ids)
        # Task assignees' partners
        tasks = env['project.task'].sudo().search([('project_id', '=', pid)])
        for t in tasks:
            partner_ids.update(t.user_ids.partner_id.ids)
        # Team-tab members
        partner_ids |= _team_partner_ids(pid)
        # Self
        partner_ids.add(request.env.user.partner_id.id)
        return [('id', 'in', list(partner_ids))]

    # ---- hr.employee: only those with planning slots or assigned tasks on this project ----
    if model == 'hr.employee':
        slots = env['planning.slot'].sudo().search([('project_id', '=', pid)])
        res_ids = list({r for r in slots.mapped('resource_id.id') if r})
        tasks = env['project.task'].sudo().search([('project_id', '=', pid)])
        task_user_ids = set()
        for t in tasks:
            task_user_ids.update(t.user_ids.ids)
        if not res_ids and not task_user_ids:
            return [('id', '=', 0)]  # no scope → return empty set
        domain = []
        if res_ids and task_user_ids:
            domain = ['|', ('resource_id', 'in', res_ids), ('user_id', 'in', list(task_user_ids))]
        elif res_ids:
            domain = [('resource_id', 'in', res_ids)]
        else:
            domain = [('user_id', 'in', list(task_user_ids))]
        return domain

    # ---- mail.message / mail.followers / mail.activity / ir.attachment ----
    # Tied to records of this project: the project itself, its tasks, its calendar events.
    if model in ('mail.message', 'mail.followers', 'mail.activity', 'ir.attachment'):
        task_ids = env['project.task'].sudo().search([('project_id', '=', pid)]).ids
        cal_ids = env['calendar.event'].sudo().search([
            ('res_model', '=', 'project.project'), ('res_id', '=', pid),
        ]).ids
        field_model = 'model' if model in ('mail.message', 'mail.activity') else 'res_model'
        field_id = 'res_id'
        # Domain: model in {project.project=pid, project.task=task_ids, calendar.event=cal_ids}
        leaves = []
        if cal_ids:
            leaves.append(['&', (field_model, '=', 'calendar.event'), (field_id, 'in', cal_ids)])
        if task_ids:
            leaves.append(['&', (field_model, '=', 'project.task'), (field_id, 'in', task_ids)])
        leaves.append(['&', (field_model, '=', 'project.project'), (field_id, '=', pid)])
        # Combine with OR
        domain = []
        for i, leaf in enumerate(leaves):
            if i < len(leaves) - 1:
                domain.append('|')
            domain.extend(leaf)
        return domain

    if model == 'mail.notification':
        # Notifications are tied to mail.message; scope by their message id
        msg_domain = _project_scope_domain('mail.message', project)
        msg_ids = env['mail.message'].sudo().search(msg_domain).ids
        if not msg_ids:
            return [('id', '=', 0)]
        return [('mail_message_id', 'in', msg_ids)]

    # Unknown model → deny by returning impossible domain
    return [('id', '=', 0)]


def _filter_fields_arg(model, fields_arg):
    """Strip fields not in the per-model whitelist. Defends against PII /
    sensitive-field exfiltration via sudo-rpc.read or .search_read."""
    if not isinstance(fields_arg, (list, tuple)):
        return fields_arg
    wl = FIELD_WHITELIST.get(model)
    if wl is None:
        # No whitelist defined → deny all fields except id
        return ['id']
    return [f for f in fields_arg if f in wl]


def _dispatch(Model, method, args, kwargs):
    """Dispatch a method on a model in the same way Odoo's web/dataset/call_kw does.

    For recordset methods (read/write/unlink/...) the first positional arg is
    the id list; we browse those ids first, then invoke the method on the
    resulting recordset. For everything else (search_read, search, create,
    name_search, etc.) we pass args/kwargs through directly.
    """
    args = list(args or [])
    kwargs = dict(kwargs or {})
    if method in RECORDSET_METHODS:
        ids = args[0] if args else []
        if not isinstance(ids, list):
            ids = [ids] if ids else []
        records = Model.browse(ids)
        return getattr(records, method)(*args[1:], **kwargs)
    return getattr(Model, method)(*args, **kwargs)


# =====================================================================
# Routes
# =====================================================================

class ProjectDashboardController(http.Controller):

    @http.route(
        '/project', type='http', auth='user', website=True,
        methods=['GET'], sitemap=False,
    )
    def avontuur_dashboard_page(self, **kw):
        """Render the Avontuur dashboard page.

        The dashboard is published as a website.page with 'connected'
        visibility, which returns a hard 403 to anonymous visitors. By
        serving the same view through a controller with auth='user',
        Odoo redirects anonymous visitors to the login page (and back
        here afterwards) instead of a dead-end 403. Authenticated users
        render the dashboard exactly as before; project-level access
        stays enforced by the JSON data endpoints.
        """
        page = request.env['website.page'].sudo().search(
            [('url', '=', '/project')], limit=1)
        view = (page.view_id if page and page.view_id
                else request.env.ref('daadit.avontuur_dashboard',
                                     raise_if_not_found=False))
        if not view:
            raise werkzeug.exceptions.NotFound()
        return request.render(view.id, {'main_object': page, 'seo_object': page})

    @http.route(
        '/dashboard/project/<int:project_id>/whoami',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def whoami(self, project_id, **kwargs):
        """Cheap role-check endpoint for the dashboard JS."""
        user = request.env.user
        try:
            project = _project_or_403(project_id)
            access = 'allowed'
        except AccessError:
            project = None
            access = 'denied'
        return _ok({
            'uid': user.id,
            'name': user.name,
            'login': user.login,
            'partner_id': user.partner_id.id,
            'is_internal': not user.share,
            'is_portal': bool(user.share),
            'can_edit': bool(project and _can_edit(project)),
            'project_access': access,
        })

    @http.route(
        '/dashboard/project/<int:project_id>/snapshot',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def snapshot(self, project_id, **kwargs):
        """Return the full dashboard snapshot in one round-trip.

        Any user authorized via _project_or_403 (internal OR portal collaborator/
        follower/customer) gets the same unified data set.
        """
        try:
            project = _project_or_403(project_id)
        except AccessError as e:
            return _err(str(e), 403)

        env = request.env

        # ---------- project ----------
        proj_data = project.read(PROJECT_FIELDS)[0]

        # ---------- stages ----------
        stages = env['project.task.type'].sudo().search_read(
            [('project_ids', 'in', [project.id])],
            STAGE_FIELDS,
            order='sequence asc, id asc',
        )
        if not stages:
            stages = env['project.task.type'].sudo().search_read(
                [], STAGE_FIELDS, order='sequence asc, id asc',
            )

        # ---------- tasks ----------
        tasks = env['project.task'].sudo().search_read(
            [('project_id', '=', project.id), ('is_template', '=', False)],
            TASK_FIELDS + ['description'],
            order='sequence asc, id asc',
            limit=2000,
        )

        # ---------- users (id -> info) ----------
        uids = set()
        for t in tasks:
            for u in (t.get('user_ids') or []):
                uids.add(u)
        if proj_data.get('user_id'):
            uids.add(proj_data['user_id'][0])
        if proj_data.get('favorite_user_ids'):
            uids.update(proj_data['favorite_user_ids'])
        users = []
        if uids:
            users = env['res.users'].sudo().browse(list(uids)).read(USER_FIELDS)

        # ---------- partners ----------
        partner_ids = set()
        if proj_data.get('partner_id'):
            partner_ids.add(proj_data['partner_id'][0])
        partners = []
        if partner_ids:
            partners = env['res.partner'].sudo().browse(list(partner_ids)).read(PARTNER_FIELDS)

        # ---------- milestones ----------
        milestones = env['project.milestone'].sudo().search_read(
            [('project_id', '=', project.id)],
            MILESTONE_FIELDS,
            order='deadline asc, sequence asc, id asc',
        )

        # ---------- goal + progress reports ----------
        goal = _project_goal(proj_data.get('description'))
        updates = env['project.update'].sudo().search_read(
            [('project_id', '=', project.id)],
            PROJECT_UPDATE_FIELDS,
            order='date desc, id desc',
            limit=PROJECT_UPDATE_LIMIT,
        )

        # ---------- calendar events linked to this project ----------
        now = datetime.utcnow()
        lo = (now - timedelta(days=60)).strftime('%Y-%m-%d %H:%M:%S')
        hi = (now + timedelta(days=180)).strftime('%Y-%m-%d %H:%M:%S')
        cal_events = env['calendar.event'].sudo().search_read(
            [
                ('res_model', '=', 'project.project'),
                ('res_id', '=', project.id),
                ('start', '>=', lo),
                ('start', '<=', hi),
            ],
            CALENDAR_FIELDS,
            order='start asc',
            limit=500,
        )

        # ---------- team members (collaborators + internal users + employees) ----------
        collabs = env['project.collaborator'].sudo().search_read(
            [('project_id', '=', project.id)],
            ['partner_id', 'limited_access'],
        )
        collab_pids = [c['partner_id'][0] for c in collabs if c.get('partner_id')]
        collab_partners = env['res.partner'].sudo().browse(collab_pids).read(
            PARTNER_FIELDS
        ) if collab_pids else []

        fav_uids = proj_data.get('favorite_user_ids') or []
        pm_uid = proj_data.get('user_id') and proj_data['user_id'][0]
        internal_uids = set(fav_uids)
        if pm_uid:
            internal_uids.add(pm_uid)
        internal_users = env['res.users'].sudo().browse(list(internal_uids)).read(
            USER_FIELDS
        ) if internal_uids else []

        slots = env['planning.slot'].sudo().search_read(
            [('project_id', '=', project.id)],
            ['resource_id'],
        )
        res_ids = {s['resource_id'][0] for s in slots if s.get('resource_id')}
        employees = []
        if res_ids:
            employees = env['hr.employee'].sudo().search_read(
                [('resource_id', 'in', list(res_ids))],
                EMPLOYEE_FIELDS,
            )
        team_members = {
            'collaborator_partners': collab_partners,
            'internal_users': internal_users,
            'employees': employees,
        }

        capacity_slots = env['planning.slot'].sudo().search_read(
            [('project_id', '=', project.id)],
            PLANNING_SLOT_FIELDS,
            order='start_datetime asc',
            limit=2000,
        )

        # ---------- holder task description (dashboard config blob) ----------
        # Robust against duplicate holders (post-sync staging artefact): pick
        # the one with the longest description (= most data), or the lowest id
        # if all are empty.
        holders = env['project.task'].sudo().with_context(active_test=False).search([
            ('project_id', '=', project.id),
            ('name', 'ilike', '__dashboard_cfg__'),
        ])
        holder = None
        if holders:
            holders_sorted = holders.sorted(key=lambda h: (-(len(h.description or '')), h.id))
            holder = holders_sorted[0]
        holder_description = holder.description if (holder and holder.description) else ''

        return _ok({
            'role': 'portal' if _is_portal() else 'internal',
            'can_edit': _can_edit(project),
            'project': proj_data,
            'stages': stages,
            'tasks': tasks,
            'users': users,
            'partners': partners,
            'milestones': milestones,
            'goal': goal,
            'updates': updates,
            'latest_update': updates[0] if updates else None,
            'calendar': cal_events,
            'team_members': team_members,
            'capacity_slots': capacity_slots,
            'holder_description': holder_description,
            'holder_task_id': holder.id if holder else 0,
        })

    @http.route(
        '/dashboard/project/<int:project_id>/task/<int:task_id>',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def task_detail(self, project_id, task_id, **kwargs):
        """Return full detail for one task. Same fields for all authorized users."""
        try:
            project = _project_or_403(project_id)
        except AccessError as e:
            return _err(str(e), 403)

        env = request.env
        task = env['project.task'].sudo().browse(task_id).exists()
        if not task:
            return _err("Task not found.", 404)
        if task.project_id.id != project.id:
            return _err("Task does not belong to this project.", 403)

        data = task.read(TASK_FIELDS + ['description'])[0]
        return _ok(data)

    @http.route(
        '/dashboard/project/<int:project_id>/sudo-rpc',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def sudo_rpc(self, project_id, model=None, method=None, args=None, kwargs=None, **kw):
        """Generic passthrough that executes allowlisted read-methods on
        allowlisted models via sudo(), once the caller is authorized for
        the project.

        Designed to give portal users with project access the same read
        surface as internal users without having to grant them broad
        Odoo ACL. The dashboard portal-shim routes models that aren't in
        the snapshot cache (hr.employee, planning.slot, mail.message,
        ir.attachment, etc.) through this endpoint.
        """
        try:
            project = _project_or_403(project_id)
        except AccessError as e:
            return _err(str(e), 403)

        if not model or not method:
            return _err("model and method are required", 400)

        allowed_methods = SUDO_RPC_ALLOWLIST.get(model)
        if not allowed_methods:
            return _err("model %r not in dashboard allowlist" % model, 403)
        if method not in allowed_methods:
            return _err("method %r not allowed on %s" % (method, model), 403)

        # ===== SECURITY: scope all reads to the granted project =====
        args = list(args or [])
        kwargs = dict(kwargs or {})
        scope = _project_scope_domain(model, project)

        # (a) Domain-based methods: AND-prepend the scope filter into the
        # user-supplied domain. Polish notation: `+ N '&' for N additional
        # leaves we add (here we just concat — Odoo treats consecutive
        # leaves as implicit AND).
        if method in DOMAIN_METHODS:
            user_domain = args[0] if args else []
            if not isinstance(user_domain, list):
                user_domain = []
            combined = list(scope) + list(user_domain)
            if args:
                args[0] = combined
            else:
                args = [combined]

        # (b) name_search: signature is (name, args, operator, limit, ...)
        # The 'args' parameter is the domain. Inject scope there.
        elif method == 'name_search':
            ns_domain = args[1] if len(args) > 1 else kwargs.get('args')
            if not isinstance(ns_domain, list):
                ns_domain = []
            combined = list(scope) + list(ns_domain)
            if len(args) > 1:
                args[1] = combined
            elif args:
                args.append(combined)
            else:
                args = [args[0] if args else '', combined]
            kwargs.pop('args', None)

        # (c) read by id list: restrict ids to those in scope.
        elif method == 'read':
            requested_ids = args[0] if args else []
            if not isinstance(requested_ids, list):
                requested_ids = [requested_ids] if requested_ids else []
            in_scope_ids = set(request.env[model].sudo().search(scope).ids)
            allowed_ids = [i for i in requested_ids if i in in_scope_ids]
            if not allowed_ids:
                # Deny silently with empty result rather than info-leak whether the
                # id exists somewhere else.
                return _ok([])
            if args:
                args[0] = allowed_ids
            else:
                args = [allowed_ids]

        # ===== SECURITY: field whitelist for read / search_read =====
        if method in ('read', 'search_read'):
            # search_read signature: search_read(domain, fields, offset, limit, order)
            #   args[0] = domain, args[1] = fields
            # read signature on recordset: read(fields, load)
            #   args[0] = ids (already in scope), args[1] = fields
            fields_idx = 1
            if len(args) > fields_idx:
                args[fields_idx] = _filter_fields_arg(model, args[fields_idx])
            elif kwargs.get('fields'):
                kwargs['fields'] = _filter_fields_arg(model, kwargs['fields'])

        Model = request.env[model].sudo()
        try:
            result = _dispatch(Model, method, args, kwargs)
        except Exception as e:
            _logger.warning("sudo-rpc %s.%s failed: %s", model, method, e)
            return _err("%s: %s" % (type(e).__name__, e), 500)

        # Recordsets / records → coerce to JSON-serializable
        if hasattr(result, 'ids') and not isinstance(result, list):
            result = result.ids
        return _ok(result)

    @http.route(
        '/dashboard/project/<int:project_id>/task/<int:task_id>/stage',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def task_set_stage(self, project_id, task_id, stage_id=None, **kwargs):
        """Move a task to a new stage (kanban drag-and-drop)."""
        try:
            project = _project_or_403(project_id)
        except AccessError as e:
            return _err(str(e), 403)

        if not _can_edit(project):
            return _err("No write access.", 403)
        if not stage_id:
            return _err("stage_id required.", 400)

        env = request.env
        task = env['project.task'].sudo().browse(task_id).exists()
        if not task or task.project_id.id != project.id:
            return _err("Task not found or not in this project.", 404)

        stage = env['project.task.type'].sudo().browse(stage_id).exists()
        if not stage:
            return _err("Stage not found.", 404)

        task.write({'stage_id': stage_id})
        return _ok({'task_id': task_id, 'stage_id': stage_id})

    @http.route(
        '/dashboard/project/<int:project_id>/sudo-write',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def sudo_write(self, project_id, model=None, method=None, args=None, kwargs=None, **kw):
        """Generic write-passthrough for users with edit access.

        Allows portal users on the Team to create / write / unlink records
        of allowlisted models. Enforces project-scope per model so a user
        cannot write to a task or event that belongs to another project.

        Used by the dashboard portal-shim so portal team members can do
        the same actions as internal users without granting them broad
        Odoo ACL.
        """
        try:
            project = _project_or_403(project_id)
        except AccessError as e:
            return _err(str(e), 403)

        if not _can_edit(project):
            return _err(
                "Geen schrijfrechten in dit project. Vraag een team-lid om "
                "je te bevorderen tot volledige collaborator.", 403,
            )

        if not model or not method:
            return _err("model and method are required", 400)
        if model not in SUDO_WRITE_ALLOWLIST:
            return _err("model %r not in write allowlist" % model, 403)
        if method not in SUDO_WRITE_ALLOWLIST[model]:
            return _err("%s not allowed on %s" % (method, model), 403)

        args = list(args or [])
        kwargs = dict(kwargs or {})

        # ---- Scope-enforce CREATE ----
        if method == 'create':
            vals_arg = args[0] if args else kwargs.pop('vals_list', None)
            if isinstance(vals_arg, dict):
                vals_list = [vals_arg]
            elif isinstance(vals_arg, list):
                vals_list = vals_arg
            else:
                return _err("create requires a dict or list of vals", 400)

            for vals in vals_list:
                if not isinstance(vals, dict):
                    return _err("each vals entry must be a dict", 400)
                if model in PROJECT_SCOPED_CREATE:
                    # Force project_id to the authorized project — ignore any client value
                    vals['project_id'] = project.id
                if model == 'calendar.event':
                    # Force link to this project so the agenda can show it
                    vals['res_model_id'] = request.env['ir.model'].sudo().search(
                        [('model', '=', 'project.project')], limit=1).id
                    vals['res_id'] = project.id
                if model == 'res.partner':
                    # New contacts go under the project's customer hierarchy
                    customer = project.partner_id
                    if customer:
                        parent_id = customer.commercial_partner_id.id or customer.id
                        # Allow the client to specify a sub-contact parent only if
                        # that parent is the customer or a descendant of it.
                        cli_parent = vals.get('parent_id')
                        if cli_parent and isinstance(cli_parent, int):
                            cli = request.env['res.partner'].sudo().browse(cli_parent)
                            if cli.exists() and (
                                cli.commercial_partner_id.id
                                != customer.commercial_partner_id.id
                            ):
                                vals['parent_id'] = parent_id  # rewrite to customer
                        else:
                            vals['parent_id'] = parent_id
                    vals.setdefault('type', 'contact')
                # SECURITY: drop fields outside the whitelist (prevents sneaking
                # in sensitive/private fields like is_company, user_id rewrites,
                # bank_account_id, etc.)
                wl = FIELD_WHITELIST.get(model, set()) | {
                    'project_id', 'res_model_id', 'res_id', 'type',
                    'parent_id'}
                # For project.task creates we also need to allow Odoo's own
                # internal m2m/o2m commands which use special keys
                if model == 'project.task':
                    wl = wl | {'partner_ids', 'user_ids', 'tag_ids', 'depend_on_ids'}
                if model == 'calendar.event':
                    wl = wl | {'partner_ids', 'alarm_ids', 'attendee_ids'}
                vals_clean = {k: v for k, v in vals.items() if k in wl}
                if len(vals_clean) != len(vals):
                    dropped = sorted(set(vals) - set(vals_clean))
                    _logger.info("sudo-write %s.create dropped non-whitelisted fields: %s",
                                 model, dropped)
                # Replace dict contents in-place so it propagates back
                vals.clear()
                vals.update(vals_clean)
            # Repack vals into args
            if args:
                args[0] = vals_list[0] if len(vals_list) == 1 else vals_list
            else:
                kwargs['vals_list'] = vals_list

        # ---- Scope-enforce WRITE / UNLINK ----
        elif method in ('write', 'unlink'):
            ids = args[0] if args else None
            if not isinstance(ids, list):
                return _err("%s requires a list of ids as first arg" % method, 400)
            # SECURITY: every record being written/deleted MUST be inside the
            # project scope. We resolve via the same domain that bounds reads —
            # no chance of writing to records of another project.
            scope = _project_scope_domain(model, project)
            # active_test=False so archived records in-scope (e.g. the archived
            # __dashboard_cfg__ holder task #393) can still be written by an
            # authorized editor — otherwise holder persistence 403s for portal.
            in_scope = set(
                request.env[model].sudo().with_context(
                    active_test=False).search(scope).ids)
            for rid in ids:
                if rid not in in_scope:
                    return _err("Record %s on %s is not in this project" % (rid, model), 403)
            # For write: also filter the values dict to whitelisted fields so a
            # caller can't write to sensitive/private fields via sudo.
            if method == 'write':
                vals = args[1] if len(args) > 1 else kwargs.get('vals')
                if isinstance(vals, dict):
                    wl = FIELD_WHITELIST.get(model, set())
                    filtered = {k: v for k, v in vals.items() if k in wl}
                    if len(filtered) != len(vals):
                        dropped = sorted(set(vals) - set(filtered))
                        _logger.info("sudo-write %s.write dropped non-whitelisted fields: %s",
                                     model, dropped)
                    if len(args) > 1:
                        args[1] = filtered
                    else:
                        kwargs['vals'] = filtered

        # ---- Execute ----
        Model = request.env[model].sudo()
        try:
            result = _dispatch(Model, method, args, kwargs)
        except Exception as e:
            _logger.warning("sudo-write %s.%s failed: %s", model, method, e)
            return _err("%s: %s" % (type(e).__name__, e), 500)

        # Recordset → ids list for create; bool for write/unlink — both already
        # JSON-serializable.
        if hasattr(result, 'ids') and not isinstance(result, list):
            result = result.ids
        return _ok(result)

    # =================================================================
    # Image endpoint (avatars)
    # =================================================================

    # Allowed (model, field) combos for the image passthrough. Strictly
    # avatar/photo fields only — never anything binary that could be a
    # document, attachment, signature, scan, etc.
    _IMAGE_ALLOWLIST = {
        'res.users':    {'avatar_128', 'avatar_256', 'avatar_512',
                         'image_128', 'image_256', 'image_512',
                         'image_1024', 'image_1920'},
        'res.partner':  {'avatar_128', 'avatar_256', 'avatar_512',
                         'image_128', 'image_256', 'image_512',
                         'image_1024', 'image_1920'},
        'hr.employee':  {'avatar_128', 'avatar_256', 'avatar_512',
                         'image_128', 'image_256', 'image_512',
                         'image_1024', 'image_1920'},
    }

    @http.route(
        '/dashboard/project/<int:project_id>/image/<string:model>/<int:rid>/<string:field>',
        type='http', auth='user', methods=['GET'], csrf=False,
    )
    def project_image(self, project_id, model, rid, field, **kw):
        """Avatar passthrough — bypasses Odoo's built-in /web/image placeholder
        that portal users get for users/employees/partners they can't directly
        read.

        Strictly scoped:
          * caller must pass _project_or_403 for this project_id;
          * (model, field) must be in _IMAGE_ALLOWLIST (only avatar/photo
            variants — never arbitrary binary fields);
          * record id must either be in the project's read-scope set for
            that model, or be the caller's own user/partner record.
        Returns 403/404 (without leaking which) when access is denied.
        """
        try:
            project = _project_or_403(project_id)
        except AccessError:
            return werkzeug.exceptions.Forbidden()

        if model not in self._IMAGE_ALLOWLIST or field not in self._IMAGE_ALLOWLIST[model]:
            return werkzeug.exceptions.Forbidden()

        env = request.env
        # Is the record in scope?
        scope = _project_scope_domain(model, project)
        in_scope = set(env[model].sudo().search(scope).ids)

        my_uid = env.user.id
        my_pid = env.user.partner_id.id
        is_self = (
            (model == 'res.users' and rid == my_uid)
            or (model == 'res.partner' and rid == my_pid)
        )

        if rid not in in_scope and not is_self:
            # Don't leak existence: same 404 whether scope-miss or really missing
            return werkzeug.exceptions.NotFound()

        rec = env[model].sudo().browse(rid).exists()
        if not rec:
            return werkzeug.exceptions.NotFound()

        image_data = getattr(rec, field, None)
        if not image_data:
            # Field exists but no data: 404 (browser falls back to placeholder
            # if dashboard JS provides one)
            return werkzeug.exceptions.NotFound()

        # Field stores base64-encoded bytes; decode for serving.
        try:
            if isinstance(image_data, str):
                raw = base64.b64decode(image_data)
            elif isinstance(image_data, (bytes, bytearray)):
                raw = bytes(image_data)
            else:
                return werkzeug.exceptions.NotFound()
        except (binascii.Error, ValueError):
            return werkzeug.exceptions.NotFound()

        # Detect content-type from magic bytes (PNG / JPEG / WebP / GIF).
        ct = 'image/png'
        if raw[:3] == b'\xff\xd8\xff':
            ct = 'image/jpeg'
        elif raw[:6] in (b'GIF87a', b'GIF89a'):
            ct = 'image/gif'
        elif raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
            ct = 'image/webp'

        return request.make_response(raw, [
            ('Content-Type', ct),
            ('Content-Length', str(len(raw))),
            # Short cache because access-state can change (someone leaves the team)
            ('Cache-Control', 'private, max-age=120'),
        ])

    # =================================================================
    # User-activity reporting (internal-only)
    # =================================================================

    @http.route(
        '/dashboard/project/<int:project_id>/user-activity/<int:uid>',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def user_activity(self, project_id, uid, **kw):
        """Login + activity snapshot for one team member.

        Returns last_login, days_since_login, recent message + task counts,
        and last-task-write timestamp. Internal-only (would leak private
        timing data otherwise).
        """
        if request.env.user.share:
            return _err("Internal users only", 403)
        try:
            project = _project_or_403(project_id)
        except AccessError as e:
            return _err(str(e), 403)

        target = request.env['res.users'].sudo().browse(uid).exists()
        if not target:
            return _err("User not found", 404)

        partner_id = target.partner_id.id if target.partner_id else 0

        # Recent activity window: 30 days
        from datetime import datetime, timedelta
        since = datetime.utcnow() - timedelta(days=30)
        since_str = since.strftime('%Y-%m-%d %H:%M:%S')

        # Authored messages on the project's records
        # (project itself, tasks, calendar events for this project)
        task_ids = request.env['project.task'].sudo().search([
            ('project_id', '=', project.id)
        ]).ids
        cal_ids = request.env['calendar.event'].sudo().search([
            ('res_model', '=', 'project.project'),
            ('res_id', '=', project.id),
        ]).ids

        message_domain = [
            ('author_id', '=', partner_id),
            ('date', '>=', since_str),
            '|', '|',
            '&', ('model', '=', 'project.project'), ('res_id', '=', project.id),
            '&', ('model', '=', 'project.task'), ('res_id', 'in', task_ids or [0]),
            '&', ('model', '=', 'calendar.event'), ('res_id', 'in', cal_ids or [0]),
        ]
        msg_count = request.env['mail.message'].sudo().search_count(message_domain)
        last_msg = request.env['mail.message'].sudo().search(
            message_domain, limit=1, order='date desc'
        )
        last_message_date = last_msg.date.isoformat() if last_msg and last_msg.date else None

        # Tasks the user wrote to in this project recently
        last_task_write = request.env['project.task'].sudo().search(
            [
                ('project_id', '=', project.id),
                ('write_uid', '=', target.id),
                ('write_date', '>=', since_str),
            ],
            limit=1, order='write_date desc',
        )
        last_task_write_date = (
            last_task_write.write_date.isoformat()
            if last_task_write and last_task_write.write_date else None)
        last_task_write_name = last_task_write.name if last_task_write else None

        # Tasks currently assigned to this user
        assigned_count = request.env['project.task'].sudo().search_count([
            ('project_id', '=', project.id),
            ('user_ids', 'in', [target.id]),
        ])

        last_login = target.login_date.isoformat() if target.login_date else None
        days_since_login = None
        if target.login_date:
            days_since_login = (datetime.utcnow() - target.login_date).days

        return _ok({
            'uid': target.id,
            'name': target.name,
            'login': target.login,
            'is_share': bool(target.share),
            'is_active': bool(target.active),
            'create_date': target.create_date.isoformat() if target.create_date else None,
            'last_login': last_login,
            'days_since_login': days_since_login,
            'authored_messages_30d': msg_count,
            'last_message_date': last_message_date,
            'tasks_assigned': assigned_count,
            'last_task_write_date': last_task_write_date,
            'last_task_write_name': last_task_write_name,
        })

    # =================================================================
    # Capacity overview (read-only, all users incl. portal)
    # =================================================================
    # The capacity/vacation tab is computed client-side from hr.employee,
    # planning.slot and resource.calendar(.leaves) — models portal users
    # cannot read directly. This endpoint returns exactly the records the
    # capacity view reads, scoped to THIS project's team, with sudo(). The
    # view (for portal) routes its reads through this and renders read-only.
    # No write capability is exposed here.

    @http.route(
        '/dashboard/project/<int:project_id>/capacity-overview',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def capacity_overview(self, project_id, **kw):
        try:
            project = _project_or_403(project_id)
        except AccessError as e:
            return _err(str(e), 403)

        env = request.env
        P = project

        # ---- Holder config (extraEmpIds etc.) ----
        Task = env['project.task'].sudo().with_context(active_test=False)
        holders = Task.search([
            ('project_id', '=', P.id),
            ('name', 'ilike', '__dashboard_cfg__'),
        ])
        holder = max(holders, key=lambda r: len(r.description or '')) if holders else None
        holder_desc = (holder.description if holder else '') or ''
        try:
            cfg = _decode_holder_cfg(holder_desc) if holder_desc else {}
        except Exception:
            cfg = {}
        capcfg = ((cfg.get('capacity') or {}).get(str(P.id)) or {})
        extra_emp_ids = [e for e in (capcfg.get('extraEmpIds') or []) if isinstance(e, int)]

        manager_uid = P.user_id.id if P.user_id else 0
        fav_uids = P.favorite_user_ids.ids

        def m2o(rec):
            return [rec.id, rec.display_name] if rec else False

        # ---- Planning slots (whole project) ----
        slots = env['planning.slot'].sudo().search([('project_id', '=', P.id)])
        slot_recs = []
        used_res = set()
        for s in slots:
            rid = s.resource_id.id if s.resource_id else False
            if rid:
                used_res.add(rid)
            slot_recs.append({
                'id': s.id,
                'resource_id': m2o(s.resource_id),
                'start_datetime': str(s.start_datetime) if s.start_datetime else False,
                'end_datetime': str(s.end_datetime) if s.end_datetime else False,
                'allocated_hours': s.allocated_hours or 0.0,
            })

        # ---- Relevant employees (project team only) ----
        emps = env['hr.employee'].sudo().search([('active', '=', True)])
        emp_recs = []
        cal_ids = set()
        res_ids = set()
        for e in emps:
            rid = e.resource_id.id if e.resource_id else False
            uid = e.user_id.id if e.user_id else False
            keep = (
                (uid and uid == manager_uid)
                or (uid and uid in fav_uids)
                or (rid and rid in used_res)
                or (e.id in extra_emp_ids)
            )
            if not keep:
                continue
            calid = e.resource_calendar_id.id if e.resource_calendar_id else False
            if calid:
                cal_ids.add(calid)
            if rid:
                res_ids.add(rid)
            emp_recs.append({
                'id': e.id,
                'name': e.name,
                'user_id': m2o(e.user_id),
                'resource_calendar_id': m2o(e.resource_calendar_id),
                'resource_id': m2o(e.resource_id),
            })

        # ---- Calendars (hours_per_week) ----
        cal_recs = []
        if cal_ids:
            for c in env['resource.calendar'].sudo().browse(list(cal_ids)):
                if c.exists():
                    cal_recs.append({'id': c.id, 'hours_per_week': c.hours_per_week or 0.0})

        # ---- Leaves for those resources/calendars ----
        leave_recs = []
        if res_ids or cal_ids:
            dom = ['|', ('resource_id', 'in', list(res_ids)),
                   '&', ('resource_id', '=', False),
                        ('calendar_id', 'in', list(cal_ids) or [0])]
            for leave in env['resource.calendar.leaves'].sudo().search(dom):
                leave_recs.append({
                    'id': leave.id,
                    'name': leave.name or '',
                    'date_from': (
                        str(leave.date_from) if leave.date_from else False),
                    'date_to': str(leave.date_to) if leave.date_to else False,
                    'resource_id': m2o(leave.resource_id),
                    'calendar_id': m2o(leave.calendar_id),
                })

        # ---- Collaborators + partners + users (portal/klant members) ----
        collabs = env['project.collaborator'].sudo().search([('project_id', '=', P.id)])
        collab_recs = []
        collab_pids = set()
        for c in collabs:
            pid = c.partner_id.id if c.partner_id else False
            if pid:
                collab_pids.add(pid)
            collab_recs.append({'id': c.id, 'partner_id': m2o(c.partner_id)})

        partner_ids = set(collab_pids)
        if P.partner_id:
            partner_ids.add(P.partner_id.id)
        partner_recs = []
        for p in env['res.partner'].sudo().browse(list(partner_ids)):
            if p.exists():
                partner_recs.append({
                    'id': p.id, 'name': p.name, 'email': p.email or '',
                    'user_ids': p.user_ids.ids,
                })

        user_ids = set(fav_uids)
        if manager_uid:
            user_ids.add(manager_uid)
        for p in env['res.partner'].sudo().browse(list(collab_pids)):
            if p.exists():
                for u in p.user_ids:
                    user_ids.add(u.id)
        user_recs = []
        for u in env['res.users'].sudo().browse(list(user_ids)):
            if u.exists():
                user_recs.append({
                    'id': u.id, 'name': u.name, 'login': u.login,
                    'share': bool(u.share), 'partner_id': m2o(u.partner_id),
                })

        return _ok({
            'holder_description': holder_desc,
            'project.project': [{
                'id': P.id, 'name': P.name,
                'user_id': m2o(P.user_id),
                'favorite_user_ids': fav_uids,
                'date': str(P.date) if P.date else False,
                'date_start': str(P.date_start) if P.date_start else False,
            }],
            'hr.employee': emp_recs,
            'planning.slot': slot_recs,
            'project.collaborator': collab_recs,
            'resource.calendar': cal_recs,
            'resource.calendar.leaves': leave_recs,
            'res.partner': partner_recs,
            'res.users': user_recs,
        })

    # =================================================================
    # Self-service profile (phone / mobile / function)
    # =================================================================
    # Lets any project-authorized user read & update a LIMITED set of their
    # OWN contact fields, and lets editors update those same fields for a
    # team member. Portal users have no direct write-ACL on res.partner, so
    # this endpoint performs the write with sudo() after strict authorization
    # and field-whitelisting. Only phone / mobile / function are ever touched
    # — never name, email, login, company, or any sensitive field.

    _PROFILE_EDITABLE = {'phone', 'function'}

    @http.route(
        '/dashboard/project/<int:project_id>/my-profile',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def my_profile(self, project_id, partner_id=None, **kw):
        """Return contact details for the caller's own partner, or — if the
        caller is an editor — for a given team-member partner.

        Payload: {partner_id?: int}. Without partner_id, returns the caller's
        own partner. With partner_id, the caller must be an editor and the
        partner must be on the project team.
        """
        try:
            project = _project_or_403(project_id)
        except AccessError as e:
            return _err(str(e), 403)

        me = request.env.user
        own_pid = me.partner_id.id if me.partner_id else 0

        if partner_id and int(partner_id) != own_pid:
            # Editor reading a team member's details
            if not _can_edit(project):
                return _err("Geen rechten om andermans gegevens te bekijken.", 403)
            team_pids = _team_partner_ids(project_id)
            if int(partner_id) not in team_pids:
                return _err("Persoon staat niet in het projectteam.", 403)
            target_pid = int(partner_id)
        else:
            target_pid = own_pid

        if not target_pid:
            return _err("Geen contactpersoon gekoppeld aan dit account.", 404)

        partner = request.env['res.partner'].sudo().browse(target_pid).exists()
        if not partner:
            return _err("Contactpersoon niet gevonden.", 404)

        return _ok({
            'partner_id': partner.id,
            'name': partner.name or '',
            'email': partner.email or '',
            'phone': partner.phone or '',
            'function': partner.function or '',
            'is_self': target_pid == own_pid,
        })

    @http.route(
        '/dashboard/project/<int:project_id>/update-profile',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def update_profile(self, project_id, partner_id=None, values=None, **kw):
        """Write phone / mobile / function to the caller's own partner, or —
        for editors — to a team-member partner.

        Payload: {partner_id?: int, values: {phone?, mobile?, function?}}.
        Authorization:
          * Self-edit (partner_id omitted or == own partner): any authorized
            project user may edit their own phone/mobile/function.
          * Editor-edit (partner_id of another team member): caller must pass
            _can_edit() AND the target must be on the project team.
        Fields are whitelisted to phone/mobile/function — everything else is
        dropped before the sudo write.
        """
        try:
            project = _project_or_403(project_id)
        except AccessError as e:
            return _err(str(e), 403)

        me = request.env.user
        own_pid = me.partner_id.id if me.partner_id else 0

        if partner_id and int(partner_id) != own_pid:
            if not _can_edit(project):
                return _err("Geen rechten om andermans gegevens te wijzigen.", 403)
            team_pids = _team_partner_ids(project_id)
            if int(partner_id) not in team_pids:
                return _err("Persoon staat niet in het projectteam.", 403)
            target_pid = int(partner_id)
        else:
            target_pid = own_pid

        if not target_pid:
            return _err("Geen contactpersoon gekoppeld aan dit account.", 404)

        if not isinstance(values, dict):
            return _err("values moet een object zijn.", 400)

        clean = {k: v for k, v in values.items() if k in self._PROFILE_EDITABLE}
        if not clean:
            return _err("Geen toegestane velden om bij te werken "
                        "(alleen phone, function).", 400)

        # Normalize: empty string clears the field; coerce to str/false.
        norm = {}
        for k, v in clean.items():
            if v is None or v is False:
                norm[k] = False
            else:
                norm[k] = str(v).strip() or False

        partner = request.env['res.partner'].sudo().browse(target_pid).exists()
        if not partner:
            return _err("Contactpersoon niet gevonden.", 404)
        try:
            partner.write(norm)
        except Exception as e:
            _logger.warning("update-profile %s failed: %s", target_pid, e)
            return _err("%s: %s" % (type(e).__name__, e), 500)

        return _ok({
            'partner_id': partner.id,
            'phone': partner.phone or '',
            'function': partner.function or '',
            'is_self': target_pid == own_pid,
        })

    @http.route(
        '/dashboard/project/<int:project_id>/invite-portal',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def invite_portal(self, project_id, partner_id=None, **kw):
        """Grant portal access to a project-related contact and send them the
        invitation e-mail (same as Odoo's "Grant portal access"). Internal
        users only; the target must be a contact on this project's team /
        collaborators, must have an e-mail, and must not already have a login.
        """
        if request.env.user.share:
            return _err("Alleen interne gebruikers kunnen uitnodigen.", 403)
        try:
            project = _project_or_403(project_id)
        except AccessError as e:
            return _err(str(e), 403)
        if not _can_edit(project):
            return _err("Geen rechten om uit te nodigen in dit project.", 403)

        pid = int(partner_id or 0)
        if not pid:
            return _err("Geen contactpersoon opgegeven.", 400)

        # The partner must belong to this project (team member or collaborator).
        team_pids = _team_partner_ids(project.id)
        collab = request.env['project.collaborator'].sudo().search([
            ('project_id', '=', project.id), ('partner_id', '=', pid),
        ], limit=1)
        if pid not in team_pids and not collab:
            # also allow if the partner is a child/commercial of a team partner
            partner_pre = request.env['res.partner'].sudo().browse(pid).exists()
            commercial = partner_pre.commercial_partner_id.id if partner_pre else 0
            if not (commercial and commercial in team_pids):
                return _err("Deze contactpersoon hoort niet bij dit project.", 403)

        partner = request.env['res.partner'].sudo().browse(pid).exists()
        if not partner:
            return _err("Contactpersoon niet gevonden.", 404)
        if partner.user_ids:
            return _err("Deze persoon heeft al een inlog-account.", 400)
        if not partner.email:
            return _err("Deze contactpersoon heeft geen e-mailadres. Vul eerst een e-mail in.", 400)

        # Grant portal access via the standard portal.wizard flow (creates the
        # portal user if needed and e-mails the invitation/login link).
        try:
            Wizard = request.env['portal.wizard'].sudo().with_context(
                active_model='res.partner', active_ids=[pid],
            )
            wizard = Wizard.create({})
            line = wizard.user_ids.filtered(
                lambda entry: entry.partner_id.id == pid)
            if not line:
                return _err(
                    "Kon de uitnodiging niet voorbereiden voor deze "
                    "contactpersoon.", 500)
            line = line[0]
            try:
                line.in_portal = True
            except Exception:
                pass
            line.action_grant_access()
        except Exception as e:
            _logger.warning("invite-portal failed for partner %s: %s", pid, e)
            return _err("Uitnodigen mislukt: %s" % e, 500)

        return _ok({'invited': pid, 'email': partner.email, 'name': partner.name})

    # =================================================================
    # Task documents (ir.attachment) — upload / list / delete / download
    # =================================================================
    # Gives BOTH internal and portal users the same "add a document to a
    # task" capability as the Odoo backend. Portal users have no direct
    # ir.attachment write-ACL, so these endpoints do it with sudo() after
    # strict project-scoping. Delete is limited to the document's owner
    # (create_uid) or a project editor.

    _MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MB per file

    def _task_in_project(self, project, task_id):
        task = request.env['project.task'].sudo().browse(int(task_id)).exists()
        if not task or task.project_id.id != project.id:
            return None
        return task

    @http.route(
        '/dashboard/project/<int:project_id>/task/<int:task_id>/attachments',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def task_attachments(self, project_id, task_id, **kw):
        """List documents attached to a task in this project."""
        try:
            project = _project_or_403(project_id)
        except AccessError as e:
            return _err(str(e), 403)
        task = self._task_in_project(project, task_id)
        if not task:
            return _err("Taak niet gevonden in dit project.", 404)

        atts = request.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'project.task'),
            ('res_id', '=', task.id),
        ], order='id desc')
        me = request.env.user
        editor = _can_edit(project)
        out = []
        for a in atts:
            owner_id = a.create_uid.id if a.create_uid else 0
            out.append({
                'id': a.id,
                'name': a.name or 'document',
                'mimetype': a.mimetype or '',
                'file_size': a.file_size or 0,
                'create_date': a.create_date.isoformat() if a.create_date else None,
                'create_uid': owner_id,
                'create_name': a.create_uid.name if a.create_uid else '',
                'can_delete': bool(editor or (owner_id and owner_id == me.id)),
            })
        return _ok({'attachments': out})

    @http.route(
        '/dashboard/project/<int:project_id>/task/<int:task_id>/attachment/upload',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def task_attachment_upload(self, project_id, task_id, name=None,
                               datas=None, mimetype=None, **kw):
        """Upload a document to a task. Any project-authorized user may add
        (internal or portal team member). The file is stored as an
        ir.attachment linked to the task — visible in the Odoo backend too.

        Payload: {name: str, datas: base64 str, mimetype?: str}.
        """
        try:
            project = _project_or_403(project_id)
        except AccessError as e:
            return _err(str(e), 403)
        task = self._task_in_project(project, task_id)
        if not task:
            return _err("Taak niet gevonden in dit project.", 404)
        if not name or not datas:
            return _err("Bestandsnaam en inhoud zijn vereist.", 400)

        # Strip a possible data-URL prefix ("data:...;base64,")
        if isinstance(datas, str) and ',' in datas[:64] and datas[:5] == 'data:':
            datas = datas.split(',', 1)[1]
        try:
            raw = base64.b64decode(datas)
        except (binascii.Error, ValueError):
            return _err("Ongeldige bestandsinhoud.", 400)
        if not raw:
            return _err("Leeg bestand.", 400)
        if len(raw) > self._MAX_ATTACHMENT_BYTES:
            return _err("Bestand te groot (max 25 MB).", 400)

        safe_name = re.sub(r'[\r\n\t]', ' ', str(name)).strip()[:200] or 'document'
        try:
            att = request.env['ir.attachment'].sudo().create({
                'name': safe_name,
                'datas': base64.b64encode(raw).decode('ascii'),
                'res_model': 'project.task',
                'res_id': task.id,
                'mimetype': (mimetype or '')[:128] or False,
            })
            # Note in the task chatter for traceability (no attachment re-link,
            # so the document stays bound to the task itself).
            try:
                task.sudo().with_context(mail_create_nosubscribe=True).message_post(
                    body="📎 Document toegevoegd via dashboard: %s" % safe_name,
                    message_type='comment',
                )
            except Exception:
                pass
        except Exception as e:
            _logger.warning("attachment upload failed (task %s): %s", task.id, e)
            return _err("%s: %s" % (type(e).__name__, e), 500)

        return _ok({
            'id': att.id,
            'name': att.name,
            'file_size': att.file_size or len(raw),
            'mimetype': att.mimetype or '',
        })

    @http.route(
        '/dashboard/project/<int:project_id>/attachment/<int:attachment_id>/delete',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def task_attachment_delete(self, project_id, attachment_id, **kw):
        """Delete a task document. Allowed for the document's owner
        (create_uid) or a project editor only."""
        try:
            project = _project_or_403(project_id)
        except AccessError as e:
            return _err(str(e), 403)
        att = request.env['ir.attachment'].sudo().browse(int(attachment_id)).exists()
        if not att or att.res_model != 'project.task':
            return _err("Document niet gevonden.", 404)
        task = self._task_in_project(project, att.res_id)
        if not task:
            return _err("Document hoort niet bij dit project.", 403)
        me = request.env.user
        is_owner = bool(att.create_uid and att.create_uid.id == me.id)
        if not (is_owner or _can_edit(project)):
            return _err("Alleen de eigenaar of een teamlid mag dit document verwijderen.", 403)
        try:
            att.unlink()
        except Exception as e:
            _logger.warning("attachment delete failed (%s): %s", attachment_id, e)
            return _err("%s: %s" % (type(e).__name__, e), 500)
        return _ok({'deleted': int(attachment_id)})

    @http.route(
        '/dashboard/project/<int:project_id>/attachment/<int:attachment_id>/download',
        type='http', auth='user', methods=['GET'], csrf=False,
    )
    def task_attachment_download(self, project_id, attachment_id, **kw):
        """Serve a task document's binary, project-scoped (so portal users
        can download docs on tasks they're entitled to see)."""
        try:
            project = _project_or_403(project_id)
        except AccessError:
            return werkzeug.exceptions.Forbidden()
        att = request.env['ir.attachment'].sudo().browse(int(attachment_id)).exists()
        if not att or att.res_model != 'project.task':
            return werkzeug.exceptions.NotFound()
        task = self._task_in_project(project, att.res_id)
        if not task:
            return werkzeug.exceptions.NotFound()

        raw = att.raw or b''
        if isinstance(raw, str):
            try:
                raw = base64.b64decode(raw)
            except (binascii.Error, ValueError):
                raw = b''
        disposition = 'attachment'
        # Inline-display images/pdf; force-download everything else.
        mt = att.mimetype or 'application/octet-stream'
        if mt.startswith('image/') or mt == 'application/pdf':
            disposition = 'inline'
        return request.make_response(bytes(raw), [
            ('Content-Type', mt),
            ('Content-Length', str(len(raw))),
            ('Content-Disposition', http.content_disposition(
                att.name or 'document').replace(
                    'attachment', disposition, 1)),
            ('Cache-Control', 'private, max-age=60'),
        ])

    # =================================================================
    # Simulate-as-user (internal-only) — see the project as that user sees it
    # =================================================================

    def _verify_simulation_allowed(self, project_id, target_uid):
        """Returns (target_user, error). target_user is the recordset on success."""
        if request.env.user.share:
            return None, _err("Internal users only", 403)
        target = request.env['res.users'].sudo().browse(target_uid).exists()
        if not target:
            return None, _err("Target user not found", 404)
        # Only allow simulating users who themselves have access to the project
        # (internal, or portal team-member). This prevents using simulate as
        # a generic "log in as X" exploit.
        if not target.share:
            return target, None  # internal user — always allowed to simulate
        team_pids = _team_partner_ids(project_id)
        if target.partner_id.id in team_pids:
            return target, None
        commercial = target.partner_id.commercial_partner_id.id
        if commercial and commercial in team_pids:
            return target, None
        return None, _err("Target user is not on the project team — cannot simulate", 403)

    @http.route(
        '/dashboard/project/<int:project_id>/preview-as/<int:uid>/whoami',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def preview_as_whoami(self, project_id, uid, **kw):
        """Returns whoami as if the caller were `uid`."""
        target, err = self._verify_simulation_allowed(project_id, uid)
        if err:
            return err
        caller = request.env.user
        # Temporarily switch identity and re-invoke whoami logic
        try:
            request.update_env(user=target.id)
            try:
                project = _project_or_403(project_id)
                access = 'allowed'
            except AccessError:
                project = None
                access = 'denied'
            payload = {
                'uid': target.id,
                'name': target.name,
                'login': target.login,
                'partner_id': target.partner_id.id,
                'is_internal': not target.share,
                'is_portal': bool(target.share),
                'can_edit': bool(project and _can_edit(project)),
                'project_access': access,
                'simulated_by': caller.id,
                'simulated_by_name': caller.name,
            }
        finally:
            request.update_env(user=caller.id)
        return _ok(payload)

    @http.route(
        '/dashboard/project/<int:project_id>/preview-as/<int:uid>/snapshot',
        type='jsonrpc', auth='user', methods=['POST'], csrf=False,
    )
    def preview_as_snapshot(self, project_id, uid, **kw):
        """Returns the project snapshot AS IF the caller were `uid`.

        Lets an internal user see exactly what a specific portal user sees,
        for debugging permission / visibility issues. Read-only — does NOT
        affect any data, just runs the same snapshot read with a different
        user identity.
        """
        target, err = self._verify_simulation_allowed(project_id, uid)
        if err:
            return err
        caller = request.env.user
        try:
            request.update_env(user=target.id)
            # Delegate to the regular snapshot endpoint logic
            result = self.snapshot(project_id, **kw)
            # Tag the response so the JS knows it's a simulated payload
            if (
                isinstance(result, dict) and result.get('ok')
                and isinstance(result.get('data'), dict)
            ):
                result['data']['_simulated'] = {
                    'as_uid': target.id,
                    'as_name': target.name,
                    'by_uid': caller.id,
                    'by_name': caller.name,
                }
            return result
        finally:
            request.update_env(user=caller.id)
