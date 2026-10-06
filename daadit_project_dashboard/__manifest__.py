{
    'name': 'DAADit Project Dashboard — portal-safe data layer',
    'version': '19.0.1.1.1',
    'summary': 'Generic JSON endpoint that exposes project-dashboard data to internal and portal users with strict per-role field whitelisting.',
    'description': """
DAADit Project Dashboard — portal-safe data layer
==================================================

Generic (project-agnostic) HTTP endpoint that serves project dashboard
data as JSON. Runs reads with sudo() but applies strict allow-listing
per role:

* Internal users (share=False): full data, identical to direct RPC.
* Portal users (share=True): only data they are entitled to see by
  virtue of being a project.collaborator (limited_access=False), a
  follower, or the project's customer.
* Anyone else: 403.

Use this module as the data-layer for any DAADit-built project
dashboard (Avontuur, future implementations, etc.) — the dashboard JS
calls these endpoints instead of /web/dataset/call_kw for portal users,
or uniformly for both roles.

Endpoints
---------

POST /dashboard/project/<int:project_id>/whoami
    Rol + capabilities van de huidige user voor dit project.

POST /dashboard/project/<int:project_id>/snapshot
    Volledige dashboard-data in één call (role-filtered).

POST /dashboard/project/<int:project_id>/task/<int:task_id>
    Detail van één taak (role-filtered fields).

POST /dashboard/project/<int:project_id>/task/<int:task_id>/stage
    Move een taak naar een nieuwe stage (kanban drag-and-drop).

Security model
--------------

* Field-whitelists per rol staan bovenaan controllers/dashboard_data.py.
* `EMPLOYEE_FIELDS_PORTAL = None` → portal ziet überhaupt geen
  hr.employee data.
* `PLANNING_SLOT_FIELDS_PORTAL = None` → portal ziet geen capaciteit.
* Schrijfacties (stage move) vereisen collaborator zonder limited_access.
""",
    'category': 'Project',
    'author': 'DAADit',
    'website': 'https://www.daadit.group',
    'license': 'LGPL-3',
    'depends': [
        # Core models the controller reads from
        'project',           # project.project, project.task, project.task.type, project.milestone, project.collaborator
        'calendar',          # calendar.event linked to project.project via res_model/res_id
        'mail',              # mail.message (used by dashboard notifications poll)

        # Adds the bridge fields the dashboard JS expects
        'project_forecast',  # adds planning.slot.project_id (capaciteit-tab); transitively pulls in `planning` + `hr`

        # The dashboard page (/project) is a website.page; the auth='user'
        # page route + website.page lookup need the website module.
        'website',
    ],
    'data': [],
    'installable': True,
    'application': False,
    'auto_install': False,
}
