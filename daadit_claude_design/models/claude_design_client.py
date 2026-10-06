# -*- coding: utf-8 -*-
"""HTTP client for Anthropic Claude Design (live design system).

Auth is OAuth Bearer with scopes ``user:design:read`` /
``user:design:write`` — same flow as Claude Code ``/design-login``.
Tokens live in ``ir.config_parameter`` and are never returned to agents.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from urllib.parse import quote

import requests

_logger = logging.getLogger(__name__)

API_BASE = "https://api.anthropic.com/v1/design"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
# Public OAuth client id used by Claude Design / Claude Code design-login.
DEFAULT_CLIENT_ID = "59637612-477b-4836-a601-b0589eda7704"
SCOPES = ("user:design:read", "user:design:write")
REFRESH_SKEW_SECONDS = 60

PARAM_PROJECT_URL = "daadit_claude_design.project_url"
PARAM_ACCESS = "daadit_claude_design.access_token"
PARAM_REFRESH = "daadit_claude_design.refresh_token"
PARAM_EXPIRES = "daadit_claude_design.expires_at"
PARAM_CLIENT_ID = "daadit_claude_design.client_id"

DEFAULT_PROJECT_URL = (
    "https://claude.ai/design/p/be67f866-bfed-4399-aff2-04f86cf9d736"
)

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)

# Prefer brand/system artefacts over full mockup HTML dumps.
_PRIORITY_FILE_RE = re.compile(
    r"(token|design|brand|theme|style|huisstijl|guideline|prompt|"
    r"readme|manifest|component|color|typograph|font)",
    re.I,
)
_CODE_FILE_RE = re.compile(
    r"\.(css|scss|less|md|json|txt|html|js|jsx|ts|tsx|svg)$",
    re.I,
)
_MAX_FILES = 12
_MAX_CHARS_PER_FILE = 12000
_MAX_TOTAL_CHARS = 45000


def parse_project_id(url_or_id):
    """Extract a Claude Design project UUID from a URL or bare id."""
    text = (url_or_id or "").strip()
    match = _UUID_RE.search(text)
    return match.group(0) if match else ""


def _decode_project_data(raw):
    if not raw:
        return None
    try:
        padded = raw + "=="[: (4 - len(raw) % 4) % 4]
        return json.loads(base64.b64decode(padded).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


class ClaudeDesignClient:
    """Thin wrapper around the Claude Design REST API."""

    def __init__(self, env):
        self.env = env
        self.icp = env["ir.config_parameter"].sudo()

    def project_url(self):
        return (
            self.icp.get_param(PARAM_PROJECT_URL) or DEFAULT_PROJECT_URL
        ).strip()

    def _client_id(self):
        return (
            self.icp.get_param(PARAM_CLIENT_ID) or DEFAULT_CLIENT_ID
        ).strip()

    def _store_tokens(self, access, refresh, expires_at):
        self.icp.set_param(PARAM_ACCESS, access or "")
        if refresh:
            self.icp.set_param(PARAM_REFRESH, refresh)
        self.icp.set_param(PARAM_EXPIRES, str(int(expires_at or 0)))

    def _ensure_access_token(self):
        access = (self.icp.get_param(PARAM_ACCESS) or "").strip()
        refresh = (self.icp.get_param(PARAM_REFRESH) or "").strip()
        try:
            expires_at = int(float(self.icp.get_param(PARAM_EXPIRES) or 0))
        except (TypeError, ValueError):
            expires_at = 0

        now = int(time.time())
        if access and expires_at and now < (expires_at - REFRESH_SKEW_SECONDS):
            return access
        if access and not refresh and not expires_at:
            # Operator pasted a long-lived/manual token without expiry.
            return access
        if not refresh:
            return ""
        return self._refresh(refresh) or ""

    def _refresh(self, refresh_token):
        body = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._client_id(),
            "scope": " ".join(SCOPES),
        }
        try:
            resp = requests.post(
                TOKEN_URL,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            data = resp.json() if resp.content else {}
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Claude Design token refresh failed: %s", exc)
            return ""
        if resp.status_code >= 400 or not data.get("access_token"):
            _logger.warning(
                "Claude Design token refresh HTTP %s: %s",
                resp.status_code, str(data)[:200],
            )
            return ""
        expires_at = int(time.time()) + int(data.get("expires_in") or 3600)
        self._store_tokens(
            data["access_token"],
            data.get("refresh_token") or refresh_token,
            expires_at,
        )
        return data["access_token"]

    def _get(self, path, raw=False):
        token = self._ensure_access_token()
        if not token:
            return None, {
                "ok": False,
                "error": (
                    "Claude Design OAuth ontbreekt. Zet access/refresh "
                    "token in Instellingen → Claude Design (zelfde tokens "
                    "als Claude Code /design-login)."
                ),
            }
        try:
            resp = requests.get(
                API_BASE + path,
                headers={"Authorization": "Bearer %s" % token},
                timeout=60,
            )
        except Exception as exc:  # noqa: BLE001
            return None, {
                "ok": False,
                "error": "Claude Design API onbereikbaar: %s" % exc,
            }
        if resp.status_code == 401:
            # One forced refresh then retry.
            refresh = (self.icp.get_param(PARAM_REFRESH) or "").strip()
            if refresh:
                token = self._refresh(refresh)
                if token:
                    try:
                        resp = requests.get(
                            API_BASE + path,
                            headers={"Authorization": "Bearer %s" % token},
                            timeout=60,
                        )
                    except Exception as exc:  # noqa: BLE001
                        return None, {
                            "ok": False,
                            "error": (
                                "Claude Design API onbereikbaar: %s" % exc
                            ),
                        }
        if resp.status_code >= 400:
            return None, {
                "ok": False,
                "error": "Claude Design API HTTP %s: %s" % (
                    resp.status_code, (resp.text or "")[:200],
                ),
            }
        if raw:
            return resp.text, None
        try:
            return resp.json(), None
        except ValueError:
            return resp.text, None

    def fetch_design(self, project_url=None):
        """Fetch live design artefacts for marketing agents."""
        url = (project_url or self.project_url() or "").strip()
        project_id = parse_project_id(url)
        if not project_id:
            return {
                "ok": False,
                "error": (
                    "Geen geldig Claude Design project-id. Geef een URL "
                    "zoals https://claude.ai/design/p/<uuid>."
                ),
            }

        meta, err = self._get("/projects/%s" % project_id)
        if err:
            return err
        files_payload, err = self._get("/projects/%s/files" % project_id)
        if err:
            return err

        entries = []
        if isinstance(files_payload, dict):
            entries = [
                e for e in (files_payload.get("entries") or [])
                if isinstance(e, dict) and e.get("type") == "file"
            ]

        chosen = self._pick_files(entries)
        contents = {}
        total = 0
        for entry in chosen:
            path = entry.get("path") or ""
            if not path:
                continue
            raw, err = self._get(
                "/projects/%s/download?path=%s" % (
                    project_id, quote(path, safe=""),
                ),
                raw=True,
            )
            if err or raw is None:
                contents[path] = "[niet opgehaald]"
                continue
            text = raw if isinstance(raw, str) else json.dumps(raw)
            if len(text) > _MAX_CHARS_PER_FILE:
                text = (
                    text[:_MAX_CHARS_PER_FILE]
                    + "\n\n[AFGEKAPT — bestand langer dan %s tekens]"
                    % _MAX_CHARS_PER_FILE
                )
            if total + len(text) > _MAX_TOTAL_CHARS:
                remain = max(0, _MAX_TOTAL_CHARS - total)
                if remain < 500:
                    break
                text = text[:remain] + "\n\n[AFGEKAPT — totale limiet]"
            contents[path] = text
            total += len(text)

        chat_notes = self._chat_brand_notes(meta if isinstance(meta, dict) else {})
        tokens = self._extract_tokens(contents)

        return {
            "ok": True,
            "source": "claude_design_live",
            "project_id": project_id,
            "project_url": (
                "https://claude.ai/design/p/%s" % project_id
            ),
            "project_name": (
                (meta or {}).get("name")
                if isinstance(meta, dict) else None
            ),
            "updated_at": (
                (meta or {}).get("updated_at")
                if isinstance(meta, dict) else None
            ),
            "files": [
                {
                    "path": e.get("path"),
                    "size": e.get("size"),
                    "updated_at": e.get("updated_at"),
                }
                for e in entries
            ],
            "design_files": contents,
            "tokens": tokens,
            "brand_notes": chat_notes,
            "instruction": (
                "Dit is het LIVE Claude Design-systeem. Gebruik deze "
                "tokens, typografie, kleuren en tone-of-voice voor alle "
                "website- en social drafts. Verzin geen eigen huisstijl "
                "en cache niets: haal dit opnieuw op bij elke contentrun."
            ),
        }

    def _pick_files(self, entries):
        scored = []
        for e in entries:
            path = e.get("path") or ""
            if not _CODE_FILE_RE.search(path):
                continue
            score = 0
            if _PRIORITY_FILE_RE.search(path):
                score += 100
            if path.lower().endswith((".css", ".scss", ".json", ".md")):
                score += 20
            if path.lower().endswith((".html", ".jsx", ".tsx")):
                score += 5
            scored.append((score, path, e))
        scored.sort(key=lambda t: (-t[0], t[1]))
        if not scored:
            # Fall back to any code-ish files.
            scored = [
                (0, e.get("path") or "", e)
                for e in entries
                if _CODE_FILE_RE.search(e.get("path") or "")
            ]
        return [e for _s, _p, e in scored[:_MAX_FILES]]

    def _chat_brand_notes(self, meta):
        data = _decode_project_data(meta.get("data") or "")
        if not data or not data.get("chats"):
            return []
        notes = []
        chats = data.get("chats") or {}
        values = chats.values() if isinstance(chats, dict) else chats
        for chat in list(values)[:3]:
            if not isinstance(chat, dict):
                continue
            msgs = chat.get("messages") or []
            if isinstance(msgs, dict):
                msgs = list(msgs.values())
            for msg in msgs[-6:]:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role") or ""
                content = msg.get("content")
                if isinstance(content, list):
                    text = "".join(
                        (c.get("text") if isinstance(c, dict) else str(c))
                        for c in content
                    )
                else:
                    text = str(content or "")
                text = " ".join(text.split())
                if not text:
                    continue
                notes.append({
                    "role": "user" if role == "human" else "assistant",
                    "text": text[:800],
                })
        return notes[:12]

    def _extract_tokens(self, contents):
        """Best-effort colour/font extraction from fetched files."""
        colors = []
        fonts = []
        css_vars = []
        seen_c, seen_f, seen_v = set(), set(), set()
        for text in contents.values():
            if not isinstance(text, str):
                continue
            for color in re.findall(r"#[0-9A-Fa-f]{3,8}\b", text):
                key = color.lower()
                if key not in seen_c:
                    seen_c.add(key)
                    colors.append(color)
            for font in re.findall(
                r"font-family\s*:\s*([^;}\n]+)", text, re.I,
            ):
                font = font.strip().strip("'\"")
                if font and font not in seen_f:
                    seen_f.add(font)
                    fonts.append(font)
            for var, val in re.findall(
                r"(--[a-zA-Z0-9_-]+)\s*:\s*([^;]+);", text,
            ):
                item = "%s: %s" % (var, val.strip())
                if item not in seen_v and any(
                    k in var.lower()
                    for k in ("color", "primary", "font", "brand", "bg")
                ):
                    seen_v.add(item)
                    css_vars.append(item)
        return {
            "colors": colors[:40],
            "fonts": fonts[:20],
            "css_variables": css_vars[:40],
        }

    # ------------------------------------------------------------------
    # Templates (subfolders — not covered by fetch_design)
    # ------------------------------------------------------------------
    def list_templates(self, project_url=None):
        """The template entries declared in ``_ds_manifest.json``.

        ``fetch_design`` only ever returns the root files, so the actual
        post templates — which live in ``templates/<slug>/`` — were
        invisible to the agents. They kept inventing a layout instead of
        using the one that exists.
        """
        url = (project_url or self.project_url() or "").strip()
        project_id = parse_project_id(url)
        if not project_id:
            return {"ok": False, "error": "Geen geldig Claude Design project-id."}
        raw, err = self._get(
            "/projects/%s/download?path=%s" % (
                project_id, quote("_ds_manifest.json", safe=""),
            ),
            raw=True,
        )
        if err:
            return err
        try:
            manifest = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            return {"ok": False, "error": "Manifest van Claude Design is geen geldige JSON."}
        items = []
        for entry in (manifest.get("templates") or []):
            if not isinstance(entry, dict):
                continue
            items.append({
                "name": entry.get("name") or "",
                "description": (entry.get("description") or "")[:400],
                "folder": entry.get("folder") or "",
                "entry_path": entry.get("entryPath") or "",
            })
        return {
            "ok": True,
            "project_id": project_id,
            "templates": items,
            "global_css": manifest.get("globalCssPaths") or [],
        }

    def fetch_template(self, entry_path, project_url=None, with_css=True):
        """Return one template's HTML plus the global stylesheets.

        The templates reference ``colors_and_type.css`` / ``styles.css``
        by relative path; a renderer that only gets the HTML produces an
        unstyled page, so the CSS comes along and is inlined by the
        caller.
        """
        url = (project_url or self.project_url() or "").strip()
        project_id = parse_project_id(url)
        if not project_id or not entry_path:
            return {"ok": False, "error": "Geef een geldig template-pad op."}
        html, err = self._get(
            "/projects/%s/download?path=%s" % (
                project_id, quote(entry_path, safe=""),
            ),
            raw=True,
        )
        if err:
            return err
        if not isinstance(html, str):
            return {"ok": False, "error": "Template kon niet als tekst worden gelezen."}
        css = {}
        if with_css:
            for name in ("colors_and_type.css", "styles.css"):
                text, css_err = self._get(
                    "/projects/%s/download?path=%s" % (
                        project_id, quote(name, safe=""),
                    ),
                    raw=True,
                )
                if not css_err and isinstance(text, str):
                    css[name] = text
        return {
            "ok": True,
            "entry_path": entry_path,
            "html": html,
            "css": css,
            "length": len(html),
        }
