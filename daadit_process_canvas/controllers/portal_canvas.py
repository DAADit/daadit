# -*- coding: utf-8 -*-
"""Het procescanvas op de portal.

De toegangscontrole komt uit daadit_process_tooling en wordt hier bewust
hergebruikt in plaats van nagebouwd: twee plekken waar je rechten fout kunt
doen is er één te veel.

De gegevensroutes zijn ``type="json2"``. Dat is de route-soort van Odoo 19:
gewone JSON erin, gewone JSON eruit, met echte HTTP-statuscodes bij een fout.
``type="json"`` is sinds 19.0 een verouderde alias voor ``jsonrpc`` en levert
een JSON-RPC-envelop op die je aan de clientkant weer moet uitpakken.
"""
import werkzeug.exceptions

from odoo import _, http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.daadit_process_tooling.controllers.portal import (
    _portal_process_or_404,
)

MAX_NAME = 120


def _canvas_domain(domain_id):
    """Het domein, of een 404.

    Zelfde regel als bij een proces: intern mag alles, een klant uitsluitend
    zijn eigen domein, en alleen als het op de portal gezet is.
    """
    Domain = request.env["daadit.process.domain"].sudo()
    domain = Domain.browse(domain_id).exists()
    if not domain:
        raise werkzeug.exceptions.NotFound()
    if not request.env.user.share:
        return domain
    partner = request.env.user.partner_id
    company = (partner.commercial_partner_id or partner).sudo()
    if (not domain.portal_visible
            or not domain.partner_id
            or domain.partner_id.commercial_partner_id.id != company.id):
        raise werkzeug.exceptions.NotFound()
    return domain


def _canvas_process(process_id):
    """Het proces, of een 404.

    Interne gebruikers mogen elk proces bekijken (dat is de voorvertoning);
    een klant uitsluitend zijn eigen, via de bestaande portal-controle.
    """
    if not request.env.user.share:
        proc = request.env["daadit.process"].sudo().browse(process_id).exists()
        if not proc:
            raise werkzeug.exceptions.NotFound()
        return proc
    return _portal_process_or_404(process_id)


def _editable_process(process_id):
    proc = _canvas_process(process_id)
    if not proc.allow_validation:
        raise werkzeug.exceptions.Forbidden(
            "Meedenken staat uit voor dit proces.")
    return proc


def _step_in(proc, step_id):
    step = request.env["daadit.process.step"].sudo().browse(int(step_id)).exists()
    if not step or step.process_id.id != proc.id:
        raise werkzeug.exceptions.NotFound()
    return step


def _phase_in(proc, phase_id):
    phase = request.env["daadit.process.phase"].sudo().browse(int(phase_id)).exists()
    if not phase or phase.process_id.id != proc.id:
        raise werkzeug.exceptions.NotFound()
    return phase


def _editable_domain(domain_id):
    domain = _canvas_domain(domain_id)
    if not domain.allow_validation:
        raise werkzeug.exceptions.Forbidden(
            "Meedenken staat uit voor dit domein.")
    return domain


def _step_in_domain(domain, step_id):
    step = request.env["daadit.process.step"].sudo().browse(int(step_id)).exists()
    if not step or step.process_id.domain_id != domain:
        raise werkzeug.exceptions.NotFound()
    return step


def _phase_in_domain(domain, phase_id):
    phase = request.env["daadit.process.phase"].sudo().browse(int(phase_id)).exists()
    if not phase or phase.process_id.domain_id != domain:
        raise werkzeug.exceptions.NotFound()
    return phase


class DomainCanvasPortal(http.Controller):
    """Niveau 1: het domein, met de processen en de apps die ze raken."""

    @http.route(["/my/domeinen/<int:domain_id>/canvas"],
                type="http", auth="user", website=True, sitemap=False)
    def portal_domain_canvas(self, domain_id, **kw):
        domain = _canvas_domain(domain_id)
        return request.render("daadit_process_canvas.portal_domain_canvas", {
            "page_name": "process_tooling",
            "domain": domain,
        })

    @http.route(["/my/domeinen/<int:domain_id>/canvas/data"],
                type="json2", auth="user", methods=["POST"], website=True)
    def domain_data(self, domain_id, **kw):
        return _canvas_domain(domain_id)._canvas_graph()

    @http.route(["/my/domeinen/<int:domain_id>/canvas/step/state"],
                type="json2", auth="user", methods=["POST"], website=True)
    def domain_step_state(self, domain_id, step_id, state, note=None, **kw):
        domain = _editable_domain(domain_id)
        _step_in_domain(domain, step_id).set_validation(state, note)
        return domain._canvas_graph()

    @http.route(["/my/domeinen/<int:domain_id>/canvas/step/position"],
                type="json2", auth="user", methods=["POST"], website=True)
    def domain_step_position(self, domain_id, step_id, x, y, **kw):
        domain = _editable_domain(domain_id)
        _step_in_domain(domain, step_id).write(
            {"canvas_x": int(x), "canvas_y": int(y)})
        return {"ok": True}

    @http.route(["/my/domeinen/<int:domain_id>/canvas/phase/position"],
                type="json2", auth="user", methods=["POST"], website=True)
    def domain_phase_position(self, domain_id, phase_id, x, y, **kw):
        domain = _editable_domain(domain_id)
        _phase_in_domain(domain, phase_id).write(
            {"canvas_x": int(x), "canvas_y": int(y)})
        return {"ok": True}

    @http.route(["/my/domeinen/<int:domain_id>/canvas/process/position"],
                type="json2", auth="user", methods=["POST"], website=True)
    def domain_process_position(self, domain_id, process_id, x, y, **kw):
        domain = _editable_domain(domain_id)
        process = request.env["daadit.process"].sudo().browse(
            int(process_id)).exists()
        if not process or process.domain_id != domain:
            raise werkzeug.exceptions.NotFound()
        process.write({"canvas_x": int(x), "canvas_y": int(y)})
        return {"ok": True}

    @http.route(["/my/domeinen/<int:domain_id>/canvas/step/add"],
                type="json2", auth="user", methods=["POST"], website=True)
    def domain_step_add(self, domain_id, phase_id, name=None,
                        after_step_id=None, **kw):
        domain = _editable_domain(domain_id)
        phase = _phase_in_domain(domain, phase_id)
        name = (name or "").strip()[:MAX_NAME]
        if not name:
            raise UserError(_("Geef de handeling een naam."))
        after = _step_in_domain(domain, after_step_id) if after_step_id else None
        step = request.env["daadit.process.step"].sudo().create({
            "phase_id": phase.id,
            "name": name,
            "sequence": (after.sequence + 1) if after else 10,
            "canvas_x": (after.canvas_x if after else 120),
            "canvas_y": (after.canvas_y + 190) if after else 60,
        })
        if after:
            request.env["daadit.process.link"].sudo().create({
                "source_step_id": after.id,
                "target_step_id": step.id,
            })
        return {"graph": domain._canvas_graph(), "step_id": step.id}


class ProcessCanvasPortal(http.Controller):

    # ------------------------------------------------------------------ pagina
    @http.route(["/my/processen/<int:process_id>/canvas"],
                type="http", auth="user", website=True, sitemap=False)
    def portal_process_canvas(self, process_id, **kw):
        proc = _canvas_process(process_id)
        return request.render("daadit_process_canvas.portal_process_canvas", {
            "page_name": "process_tooling",
            "process": proc,
        })

    # ----------------------------------------------------------------- gegevens
    @http.route(["/my/processen/<int:process_id>/canvas/data"],
                type="json2", auth="user", methods=["POST"], website=True)
    def canvas_data(self, process_id, **kw):
        return _canvas_process(process_id)._canvas_graph()

    @http.route(["/my/processen/<int:process_id>/canvas/step/state"],
                type="json2", auth="user", methods=["POST"], website=True)
    def canvas_step_state(self, process_id, step_id, state, note=None, **kw):
        proc = _editable_process(process_id)
        step = _step_in(proc, step_id)
        # set_validation() is de bestaande ingang en stempelt zelf wie en
        # wanneer. sudo laat de uid ongemoeid, dus dat blijft de echte klant.
        step.set_validation(state, note)
        return proc._canvas_graph()

    @http.route(["/my/processen/<int:process_id>/canvas/step/position"],
                type="json2", auth="user", methods=["POST"], website=True)
    def canvas_step_position(self, process_id, step_id, x, y, **kw):
        proc = _editable_process(process_id)
        _step_in(proc, step_id).write({"canvas_x": int(x), "canvas_y": int(y)})
        return {"ok": True}

    @http.route(["/my/processen/<int:process_id>/canvas/phase/position"],
                type="json2", auth="user", methods=["POST"], website=True)
    def canvas_phase_position(self, process_id, phase_id, x, y, **kw):
        proc = _editable_process(process_id)
        _phase_in(proc, phase_id).write({"canvas_x": int(x), "canvas_y": int(y)})
        return {"ok": True}

    @http.route(["/my/processen/<int:process_id>/canvas/step/add"],
                type="json2", auth="user", methods=["POST"], website=True)
    def canvas_step_add(self, process_id, phase_id, name=None,
                        after_step_id=None, **kw):
        proc = _editable_process(process_id)
        phase = _phase_in(proc, phase_id)
        name = (name or "").strip()[:MAX_NAME]
        if not name:
            raise UserError(_("Geef de handeling een naam."))

        after = _step_in(proc, after_step_id) if after_step_id else None
        step = request.env["daadit.process.step"].sudo().create({
            "phase_id": phase.id,
            "name": name,
            "sequence": (after.sequence + 1) if after else 10,
            # origin volgt uit de standaard op het veld: wie via de portal werkt
            # is een share-gebruiker, en dan is de herkomst 'customer'. Dat is
            # meteen de aanjager die er een taak van maakt.
            "canvas_x": (after.canvas_x if after else 120),
            "canvas_y": (after.canvas_y + 190) if after else 60,
        })
        if after:
            request.env["daadit.process.link"].sudo().create({
                "source_step_id": after.id,
                "target_step_id": step.id,
            })
        return {"graph": proc._canvas_graph(), "step_id": step.id}
