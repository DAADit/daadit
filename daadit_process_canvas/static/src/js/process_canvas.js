/* DAADit procescanvas -- drie niveaus.
 *
 *   1. Domein      'Order to Cash'        -> welke processen, en welke apps die raken
 *   2. Proces      'Lead tot offerte'     -> welke Odoo-applicaties dat proces draagt
 *   3. Applicatie  Sales, Invoicing, ...  -> de handelingen erin
 *
 * Opzet zoals React Flow die intern gebruikt -- HTML-knooppunten in een
 * pan/zoom-laag met een SVG-laag eronder voor de lijnen -- maar zonder React.
 * react@19 levert alleen CommonJS en Odoo-addons hebben geen bundler, dus
 * React zou een tweede bouwketen in de repo betekenen.
 *
 * Gegevens gaan over type="json2"-routes: gewone JSON heen en terug.
 */
(function () {
    "use strict";

    var root = document.querySelector(".o_daadit_canvas");
    if (!root) { return; }

    var MODE = root.dataset.level === "domain" ? "domain" : "process";
    var BACK = root.dataset.backUrl || "/my/processen";
    // De endpoints hangen onder de huidige pagina. Bewust afgeleid van
    // location.pathname en niet hard op "/my/...": met meertalige website staat
    // er een taalvoorvoegsel voor (/en/my/...), en dan zou Odoo de POST
    // omleiden -- waarbij een omleiding hem in een GET verandert en de aanroep
    // stil mislukt. Meteen ook de reden dat dezelfde code onder /my/domeinen/
    // en onder /my/processen/ werkt.
    var BASE = window.location.pathname.replace(/\/+$/, "");

    var STATES = [
        { key: "akkoord", title: "Ja, zo werkt het", sub: "Geen wijziging nodig" },
        { key: "aandacht", title: "Let hier even op", sub: "Levert een aandachtspunt op" },
        { key: "afkeur", title: "Nee, wij willen dit anders", sub: "Levert een taak op in het project" }
    ];
    var LEGEND = [
        { cls: "", label: "Nog te bespreken" },
        { cls: "is-ok", label: "Werkt zo goed" },
        { cls: "is-warn", label: "Aandachtspunt" },
        { cls: "is-dev", label: "Wij willen dit anders" }
    ];

    // ---------------------------------------------------------------- helpers
    function esc(value) {
        // Procesinhoud is door mensen ingetypt; die gaat nooit ongefilterd de
        // pagina in.
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

    function rpc(path, params) {
        return fetch(BASE + path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(params || {})
        }).then(function (resp) {
            return resp.json().then(function (data) {
                if (!resp.ok) {
                    throw new Error((data && data.message) || "Er ging iets mis.");
                }
                return data;
            });
        });
    }

    function el(tag, cls, parent) {
        var node = document.createElement(tag);
        if (cls) { node.className = cls; }
        if (parent) { parent.appendChild(node); }
        return node;
    }

    // ------------------------------------------------------------- opbouw DOM
    root.innerHTML =
        '<div class="dc-top">' +
          '<nav class="dc-crumbs"></nav>' +
          '<div class="dc-legend">' +
            LEGEND.map(function (l) {
                return '<div class="dc-lg ' + l.cls + '"><i></i>' + esc(l.label) + "</div>";
            }).join("") +
          "</div>" +
        "</div>" +
        '<div class="dc-stage">' +
          '<div class="dc-world"><svg class="dc-wires"></svg></div>' +
          '<div class="dc-tools">' +
            '<div class="dc-pill">' +
              '<button type="button" data-zoom="out" title="Uitzoomen">&#x2212;</button>' +
              '<button type="button" data-zoom="in" title="Inzoomen">+</button>' +
              '<button type="button" data-zoom="fit" title="Passend maken">&#x2922;</button>' +
              '<div class="dc-zoom">100%</div>' +
            "</div>" +
            '<button type="button" class="dc-add" hidden="hidden">+ Handeling</button>' +
          "</div>" +
          '<div class="dc-msg"></div>' +
          '<aside class="dc-insp">' +
            "<header><div><h2></h2><p></p></div>" +
            '<button type="button" class="dc-x" aria-label="Sluiten">&#xD7;</button></header>' +
            '<div class="dc-body"></div>' +
          "</aside>" +
        "</div>";

    var stage = root.querySelector(".dc-stage");
    var world = root.querySelector(".dc-world");
    var wires = root.querySelector(".dc-wires");
    var crumbs = root.querySelector(".dc-crumbs");
    var insp = root.querySelector(".dc-insp");
    var inspBody = root.querySelector(".dc-body");
    var addBtn = root.querySelector(".dc-add");
    var zoomLabel = root.querySelector(".dc-zoom");
    var msgBox = root.querySelector(".dc-msg");

    var SVGNS = "http://www.w3.org/2000/svg";
    var view = { x: 0, y: 0, k: 1 };
    var DATA = null;          // {domain, processes, process_flows, graphs}
    var level = null;         // "domain" | "apps" | "steps"
    var procId = null;
    var phaseId = null;
    var sel = null;

    function canEdit() {
        if (!DATA) { return false; }
        if (MODE === "domain") { return !!(DATA.domain && DATA.domain.can_edit); }
        var g = DATA.graphs[procId];
        return !!(g && g.process.can_edit);
    }
    function graph() { return DATA.graphs[procId]; }

    function say(text, isError) {
        msgBox.textContent = text || "";
        msgBox.classList.toggle("is-error", !!isError);
        msgBox.style.display = text ? "" : "none";
    }
    function fail(err) {
        say(err && err.message ? err.message : "Er ging iets mis.", true);
    }

    // -------------------------------------------------------------- pan/zoom
    function applyView() {
        world.style.transform =
            "translate(" + view.x + "px," + view.y + "px) scale(" + view.k + ")";
        zoomLabel.textContent = Math.round(view.k * 100) + "%";
    }

    function zoomAt(cx, cy, factor) {
        var k = Math.min(2.2, Math.max(0.35, view.k * factor));
        var r = stage.getBoundingClientRect();
        var px = (cx - r.left - view.x) / view.k;
        var py = (cy - r.top - view.y) / view.k;
        view.k = k;
        view.x = cx - r.left - px * k;
        view.y = cy - r.top - py * k;
        applyView();
    }

    stage.addEventListener("wheel", function (ev) {
        ev.preventDefault();
        zoomAt(ev.clientX, ev.clientY, ev.deltaY < 0 ? 1.12 : 1 / 1.12);
    }, { passive: false });

    root.querySelector(".dc-pill").addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-zoom]");
        if (!btn) { return; }
        var r = stage.getBoundingClientRect();
        var kind = btn.dataset.zoom;
        if (kind === "fit") { fit(); }
        else { zoomAt(r.left + r.width / 2, r.top + r.height / 2, kind === "in" ? 1.2 : 1 / 1.2); }
    });

    stage.addEventListener("pointerdown", function (ev) {
        if (ev.target.closest(".dc-node, .dc-insp, .dc-tools, .dc-ghost")) { return; }
        stage.classList.add("is-panning");
        var sx = ev.clientX - view.x;
        var sy = ev.clientY - view.y;
        function move(e) { view.x = e.clientX - sx; view.y = e.clientY - sy; applyView(); }
        function up() {
            stage.classList.remove("is-panning");
            stage.removeEventListener("pointermove", move);
            stage.removeEventListener("pointerup", up);
            select(null);
        }
        stage.addEventListener("pointermove", move);
        stage.addEventListener("pointerup", up);
    });

    function fit() {
        var nodes = world.querySelectorAll(".dc-node, .dc-ghost");
        if (!nodes.length) { return; }
        var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        Array.prototype.forEach.call(nodes, function (n) {
            var x = +n.dataset.x, y = +n.dataset.y;
            x0 = Math.min(x0, x); y0 = Math.min(y0, y);
            x1 = Math.max(x1, x + n.offsetWidth); y1 = Math.max(y1, y + n.offsetHeight);
        });
        var r = stage.getBoundingClientRect();
        var pad = 70;
        var iw = insp.classList.contains("is-open") ? 326 : 0;
        var k = Math.min(1.15, (r.width - iw - pad * 2) / (x1 - x0),
                               (r.height - pad * 2) / (y1 - y0));
        view.k = Math.max(0.35, k);
        view.x = (r.width - iw - (x1 - x0) * view.k) / 2 - x0 * view.k;
        view.y = (r.height - (y1 - y0) * view.k) / 2 - y0 * view.k;
        applyView();
    }

    // --------------------------------------------------------------- slepen
    function place(node, x, y) {
        node.dataset.x = x; node.dataset.y = y;
        node.style.left = x + "px"; node.style.top = y + "px";
    }

    function draggable(node, onDrop) {
        node.addEventListener("pointerdown", function (ev) {
            if (ev.target.closest("button")) { return; }
            ev.stopPropagation();
            node.setPointerCapture(ev.pointerId);
            var ox = ev.clientX - (+node.dataset.x) * view.k;
            var oy = ev.clientY - (+node.dataset.y) * view.k;
            var moved = false;
            function move(e) {
                moved = true;
                node.classList.add("is-dragging");
                place(node, Math.round((e.clientX - ox) / view.k),
                            Math.round((e.clientY - oy) / view.k));
                wire();
            }
            function up() {
                node.classList.remove("is-dragging");
                node.removeEventListener("pointermove", move);
                node.removeEventListener("pointerup", up);
                if (moved) { onDrop(+node.dataset.x, +node.dataset.y); }
                else { node.dispatchEvent(new CustomEvent("dc-tap")); }
            }
            node.addEventListener("pointermove", move);
            node.addEventListener("pointerup", up);
        });
    }

    var saveTimers = {};
    function savePosition(path, params, key) {
        if (!canEdit()) { return; }
        clearTimeout(saveTimers[key]);
        saveTimers[key] = setTimeout(function () {
            rpc(path, params).catch(fail);
        }, 400);
    }

    // -------------------------------------------------------------- tekenen
    function statesBar(states) {
        if (!states || !states.length) { return "<b></b><b></b><b></b>"; }
        return states.map(function (s) {
            return '<b class="is-' + esc(s) + '"></b>';
        }).join("");
    }

    function render() {
        Array.prototype.forEach.call(
            world.querySelectorAll(".dc-node, .dc-ghost, .dc-tag"),
            function (n) { n.remove(); });
        if (level === "domain") { renderProcesses(); }
        else if (level === "apps") { renderApps(); }
        else { renderSteps(); }
        renderCrumbs();
        wire();
        requestAnimationFrame(fit);
    }

    // Niveau 1: de processen van het domein, elk met de apps die het raakt.
    // Dit is de kern van de vertaling: "Order to Cash is CRM, Sales, Invoicing".
    function renderProcesses() {
        addBtn.hidden = true;
        DATA.processes.forEach(function (proc) {
            var node = el("div", "dc-node dc-proc", world);
            node.dataset.id = proc.id;
            var apps = proc.apps.map(function (app) {
                return '<span class="dc-chip">' +
                    (app.icon ? '<img src="' + esc(app.icon) + '" alt=""/>' : "") +
                    "<b>" + esc(app.label) + "</b></span>";
            }).join("");
            node.innerHTML =
                "<h3>" + esc(proc.name) + "</h3>" +
                '<div class="dc-apps">' +
                    (apps || '<span class="dc-chip is-empty"><b>nog geen app gekoppeld</b></span>') +
                "</div>" +
                '<div class="dc-bar">' + statesBar(proc.states) + "</div>" +
                '<div class="dc-foot"><span>' +
                    (proc.steps ? plural(proc.steps, "handeling", "handelingen")
                                : "nog niet uitgewerkt") +
                '</span><span class="dc-dive">inzoomen &#x2192;</span></div>';
            place(node, proc.x, proc.y);
            draggable(node, function (x, y) {
                proc.x = x; proc.y = y;
                savePosition("/process/position",
                             { process_id: proc.id, x: x, y: y }, "r" + proc.id);
            });
            node.addEventListener("dc-tap", function () {
                if (proc.apps.length) { diveProcess(proc.id); }
                else { select({ kind: "proc", proc: proc }); }
            });
        });
    }

    // Niveau 2: de Odoo-applicaties binnen één proces.
    function renderApps() {
        addBtn.hidden = true;
        var g = graph();
        g.apps.forEach(function (app) {
            var node = el("div", "dc-node dc-app", world);
            node.dataset.id = app.id;
            node.innerHTML =
                (app.icon ? '<img src="' + esc(app.icon) + '" alt=""/>' : "") +
                "<h3>" + esc(app.label) + "</h3>" +
                '<div class="dc-tech">' + esc(app.subtitle) + "</div>" +
                '<div class="dc-bar">' + statesBar(app.states) + "</div>" +
                '<div class="dc-foot"><span>' +
                    (app.states.length ? plural(app.states.length, "handeling", "handelingen")
                                       : "nog niet uitgewerkt") +
                '</span><span class="dc-dive">inzoomen &#x2192;</span></div>';
            place(node, app.x, app.y);
            draggable(node, function (x, y) {
                app.x = x; app.y = y;
                savePosition("/phase/position", { phase_id: app.id, x: x, y: y }, "p" + app.id);
            });
            node.addEventListener("dc-tap", function () {
                if ((g.steps[app.id] || []).length) { diveApp(app.id); }
                else { select({ kind: "app", app: app }); }
            });
        });
    }

    // Niveau 3: de handelingen binnen één applicatie.
    function renderSteps() {
        addBtn.hidden = !canEdit();
        var g = graph();
        var steps = g.steps[phaseId] || [];
        steps.forEach(function (step, index) {
            var node = el("div", "dc-node dc-step is-" + step.cls, world);
            node.dataset.id = step.id;
            node.innerHTML =
                '<div class="dc-hd"><span class="dc-seq">' +
                    String(index + 1).padStart(2, "0") + "</span><h4>" + esc(step.name) +
                    (step.origin === "customer" ? '<span class="dc-new">nieuw</span>' : "") +
                "</h4></div>" +
                '<div class="dc-role">Rol &#xB7; <b>' + esc(step.role || "nog te bepalen") + "</b></div>" +
                // Let op: het streepje is al HTML, dus dat gaat NIET door esc().
                '<div class="dc-io"><div><span>In</span><p>' +
                    (step.input ? esc(step.input) : "&mdash;") + "</p></div>" +
                    "<div><span>Uit</span><p>" +
                    (step.output ? esc(step.output) : "&mdash;") + "</p></div></div>" +
                '<div class="dc-port is-in"></div><div class="dc-port is-out"></div>';
            place(node, step.x, step.y);
            draggable(node, function (x, y) {
                step.x = x; step.y = y;
                savePosition("/step/position", { step_id: step.id, x: x, y: y }, "s" + step.id);
            });
            node.addEventListener("dc-tap", function () { select({ kind: "step", step: step }); });
        });

        (g.external[phaseId] || []).forEach(function (link) {
            var other = g.apps.filter(function (a) { return a.id === link.phase; })[0];
            var anchor = steps.filter(function (s) { return s.id === link.step; })[0];
            if (!other || !anchor) { return; }
            var ghost = el("div", "dc-ghost", world);
            ghost.dataset.dir = link.dir;
            ghost.dataset.anchor = anchor.id;
            ghost.innerHTML =
                (other.icon ? '<img src="' + esc(other.icon) + '" alt=""/>' : "") +
                "<div><b>" + esc(other.label) + "</b><span>" + esc(link.label) + "</span></div>";
            place(ghost, anchor.x + (link.dir === "in" ? -250 : 300), anchor.y + 18);
        });
    }

    // ---------------------------------------------------------------- lijnen
    function box(id) {
        var node = world.querySelector('[data-id="' + id + '"]');
        return node && { x: +node.dataset.x, y: +node.dataset.y,
                         w: node.offsetWidth, h: node.offsetHeight };
    }

    function curve(a, b) {
        var x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
        var d = Math.max(48, Math.abs(x2 - x1) * 0.55);
        return { d: "M" + x1 + "," + y1 + "C" + (x1 + d) + "," + y1 + " " +
                    (x2 - d) + "," + y2 + " " + x2 + "," + y2, ex: x2, ey: y2 };
    }

    function path(d, cls) {
        var p = document.createElementNS(SVGNS, "path");
        p.setAttribute("d", d);
        p.setAttribute("class", cls);
        wires.appendChild(p);
        return p;
    }

    function currentLinks() {
        if (level === "domain") { return DATA.process_flows; }
        var g = graph();
        return level === "apps" ? g.flows : (g.links[phaseId] || []);
    }

    function selectedId() {
        if (!sel) { return null; }
        if (sel.kind === "proc") { return sel.proc.id; }
        if (sel.kind === "app") { return sel.app.id; }
        return sel.step.id;
    }

    function wire() {
        wires.innerHTML = "";
        Array.prototype.forEach.call(world.querySelectorAll(".dc-tag"),
                                     function (t) { t.remove(); });
        if (!DATA) { return; }
        var active = selectedId();
        currentLinks().forEach(function (link) {
            var A = box(link.from), B = box(link.to);
            if (!A || !B) { return; }
            var c = curve(A, B);
            var hot = active !== null && (active === link.from || active === link.to);
            var p = path(c.d, "dc-wire" + (hot ? " is-hot" : ""));
            path(c.d, "dc-flow" + (hot ? " is-hot" : ""));
            var cap = document.createElementNS(SVGNS, "circle");
            cap.setAttribute("cx", c.ex); cap.setAttribute("cy", c.ey);
            cap.setAttribute("r", 3.5);
            cap.setAttribute("class", "dc-cap" + (hot ? " is-hot" : ""));
            wires.appendChild(cap);
            if (link.label) {
                // Het label op de kromme zelf: het midden tussen de eindpunten
                // valt bij een S-bocht vaak bovenop een knooppunt.
                var mid;
                try { mid = p.getPointAtLength(p.getTotalLength() / 2); } catch (e) { mid = null; }
                var tag = el("div", "dc-tag" + (hot ? " is-hot" : ""), world);
                tag.textContent = link.label;
                tag.style.left = (mid ? mid.x : (A.x + B.x) / 2) + "px";
                tag.style.top = (mid ? mid.y : (A.y + B.y) / 2) + "px";
            }
        });
        Array.prototype.forEach.call(world.querySelectorAll(".dc-ghost"), function (ghost) {
            var A = box(ghost.dataset.anchor);
            if (!A) { return; }
            var G = { x: +ghost.dataset.x, y: +ghost.dataset.y,
                      w: ghost.offsetWidth, h: ghost.offsetHeight };
            var c = ghost.dataset.dir === "in" ? curve(G, A) : curve(A, G);
            path(c.d, "dc-wire is-ghost");
        });
    }

    // ------------------------------------------------------------- navigatie
    function diveProcess(id) { level = "apps"; procId = id; phaseId = null; select(null); render(); }
    function diveApp(id) { level = "steps"; phaseId = id; select(null); render(); }
    function upToDomain() { level = "domain"; procId = null; phaseId = null; select(null); render(); }
    function upToProcess() { level = "apps"; phaseId = null; select(null); render(); }

    function crumb(text, current, onClick) {
        var b = el("button", "dc-crumb", crumbs);
        b.type = "button";
        b.textContent = text;
        if (current) { b.setAttribute("aria-current", "page"); }
        else if (onClick) { b.addEventListener("click", onClick); }
        return b;
    }

    function renderCrumbs() {
        crumbs.innerHTML = "";
        if (MODE === "domain") {
            crumb((DATA.domain.client ? DATA.domain.client + " · " : "") + DATA.domain.name,
                  level === "domain", upToDomain);
        }
        if (level === "domain") { return; }
        var g = graph();
        if (MODE === "domain") { el("span", "dc-sep", crumbs).textContent = "›"; }
        crumb(g.process.name, level === "apps", upToProcess);
        if (level === "steps") {
            el("span", "dc-sep", crumbs).textContent = "›";
            var app = g.apps.filter(function (a) { return a.id === phaseId; })[0];
            crumb(app ? app.label : "", true);
        }
    }

    // ------------------------------------------------------------- inspector
    function select(next) {
        sel = next;
        Array.prototype.forEach.call(world.querySelectorAll(".dc-node"),
            function (n) { n.classList.remove("is-sel"); });
        if (!next) { insp.classList.remove("is-open"); wire(); return; }
        var node = world.querySelector('[data-id="' + selectedId() + '"]');
        if (node) { node.classList.add("is-sel"); }
        if (next.kind === "proc") { inspectProcess(next.proc); }
        else if (next.kind === "app") { inspectApp(next.app); }
        else { inspectStep(next.step); }
        insp.classList.add("is-open");
        wire();
    }

    root.querySelector(".dc-x").addEventListener("click", function () { select(null); });
    document.addEventListener("keydown", function (ev) {
        if (ev.key !== "Escape" || !root.isConnected) { return; }
        if (sel) { select(null); }
        else if (level === "steps") { upToProcess(); }
        else if (level === "apps" && MODE === "domain") { upToDomain(); }
    });

    function inspectProcess(proc) {
        insp.querySelector("h2").textContent = proc.name;
        insp.querySelector("header p").textContent = "Proces";
        inspBody.innerHTML = "<p class='dc-ro'>Aan dit proces is nog geen " +
            "Odoo-applicatie gekoppeld. Zodra dat gebeurd is, kunt u erop " +
            "inzoomen om de handelingen te zien.</p>";
    }

    function inspectApp(app) {
        insp.querySelector("h2").textContent = app.label;
        insp.querySelector("header p").textContent = app.subtitle;
        inspBody.innerHTML = "<p class='dc-ro'>Voor deze applicatie zijn nog " +
            "geen handelingen vastgelegd.</p>";
    }

    function inspectStep(step) {
        var g = graph();
        var app = g.apps.filter(function (a) { return a.id === phaseId; })[0];
        insp.querySelector("h2").textContent = step.name;
        insp.querySelector("header p").textContent =
            (app ? app.label : "") + (step.role ? " · " + step.role : "");

        var readOnly = !canEdit();
        inspBody.innerHTML =
            '<div class="dc-fld"><span>Wie doet dit</span>' +
                "<p class='dc-ro'>" + esc(step.role || "nog te bepalen") + "</p></div>" +
            '<div class="dc-fld"><span>Wat gaat erin</span>' +
                "<p class='dc-ro'>" + esc(step.input || "—") + "</p></div>" +
            '<div class="dc-fld"><span>Wat komt eruit</span>' +
                "<p class='dc-ro'>" + esc(step.output || "—") + "</p></div>" +
            (readOnly ? "" :
                '<div class="dc-fld"><span>Klopt dit voor u?</span><div class="dc-mark">' +
                STATES.map(function (s) {
                    return '<button type="button" class="dc-v-' + s.key +
                        (step.state === s.key ? " is-on" : "") + '" data-state="' + s.key + '">' +
                        "<i></i><span><strong>" + esc(s.title) + "</strong>" +
                        "<small>" + esc(s.sub) + "</small></span></button>";
                }).join("") + "</div></div>" +
                '<label class="dc-fld"><span>Toelichting</span>' +
                '<textarea rows="3" placeholder="Wat werkt er bij u anders?">' +
                esc(step.note) + "</textarea></label>");

        if (readOnly) { return; }
        var note = inspBody.querySelector("textarea");
        Array.prototype.forEach.call(inspBody.querySelectorAll(".dc-mark button"),
            function (btn) {
                btn.addEventListener("click", function () {
                    var next = btn.dataset.state === step.state ? "open" : btn.dataset.state;
                    say("Opslaan…");
                    rpc("/step/state", { step_id: step.id, state: next, note: note.value })
                        .then(function (data) {
                            absorb(data);
                            render();
                            var fresh = (graph().steps[phaseId] || []).filter(
                                function (s) { return s.id === step.id; })[0];
                            if (fresh) { select({ kind: "step", step: fresh }); }
                            say(next === "open" ? "Teruggezet."
                                : "Bedankt — dit is doorgegeven aan DAADit.");
                        }).catch(fail);
                });
            });
    }

    // ----------------------------------------------------------- toevoegen
    addBtn.addEventListener("click", function () {
        var name = window.prompt("Welke handeling ontbreekt hier?");
        if (!name || !name.trim()) { return; }
        var steps = graph().steps[phaseId] || [];
        var last = steps[steps.length - 1];
        say("Toevoegen…");
        rpc("/step/add", {
            phase_id: phaseId,
            name: name.trim(),
            after_step_id: last ? last.id : null
        }).then(function (data) {
            absorb(data.graph);
            render();
            var fresh = (graph().steps[phaseId] || []).filter(
                function (s) { return s.id === data.step_id; })[0];
            if (fresh) { select({ kind: "step", step: fresh }); }
            say("Toegevoegd — hier maakt DAADit een taak van.");
        }).catch(fail);
    });

    // ------------------------------------------------------------------ start
    function absorb(payload) {
        // De procesroute levert één procesgrafiek, de domeinroute levert het
        // hele domein. Binnen het canvas is dat dezelfde vorm, zodat alle drie
        // de niveaus dezelfde code delen.
        if (MODE === "domain") { DATA = payload; return; }
        DATA = { domain: null, processes: [], process_flows: [],
                 graphs: {} };
        DATA.graphs[payload.process.id] = payload;
        procId = payload.process.id;
    }

    say("Laden…");
    rpc("/data", {}).then(function (payload) {
        absorb(payload);
        level = MODE === "domain" ? "domain" : "apps";
        render();
        say(MODE === "domain"
            ? "Klik een proces om te zien welke Odoo-applicaties het draagt."
            : "Klik een applicatie om de handelingen te zien.");
        setTimeout(function () { say(""); }, 8000);
    }).catch(function (err) {
        fail(err);
        root.innerHTML = '<div class="alert alert-warning m-3">Het canvas kon niet ' +
            'geladen worden. <a href="' + esc(BACK) + '">Terug naar het overzicht.</a></div>';
    });
})();
