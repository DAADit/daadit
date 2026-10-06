# -*- coding: utf-8 -*-
"""Server-side bridge to the speech providers.

The browser never sees an API key: it posts audio or text here, we call
the provider, and we hand the result back. That also gives us one place
to enforce the limits that keep a chat window from quietly burning
through credits.

Listening (speech → text) runs on ElevenLabs Scribe or on Wispr Flow,
switchable in the settings. Speaking (text → speech) runs on
ElevenLabs.
"""

import base64
import json
import logging

import requests

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

API_ROOT = "https://api.elevenlabs.io/v1"
REALTIME_WS_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
REALTIME_STT_MODEL = "scribe_v2_realtime"
# ElevenLabs' low-latency speech format: 32 kbps/22 kHz is effectively
# indistinguishable through a phone speaker and roughly a quarter of the
# 44.1 kHz/128 kbps default. Change this if the voice ever sounds thin.
TTS_OUTPUT_FORMAT = "mp3_22050_32"
# Wispr Flow's dictation endpoint. Its editing layer — filler words
# dropped, punctuation added, names spelled right from the dictionary we
# send along — is what makes it read better than a raw transcript.
WISPR_ENDPOINT = "https://platform-api.wisprflow.ai/api/v1/dash/api"
# A chat reply that runs past this is almost certainly a wall of text or
# a paste; speaking it would cost real money and nobody listens that
# long anyway. We speak the opening and stop.
MAX_CHARS = 1200
TIMEOUT = 30
# One spoken turn. Well above a long sentence, well below someone
# leaving the microphone open during a meeting.
MAX_AUDIO_BYTES = 8 * 1024 * 1024
STT_MODEL = "scribe_v1"


def _api_key():
    return (
        request.env["ir.config_parameter"]
        .sudo()
        .get_param("daadit_agent_voice.elevenlabs_api_key")
        or ""
    ).strip()


def _model_id():
    return (
        request.env["ir.config_parameter"]
        .sudo()
        .get_param("daadit_agent_voice.elevenlabs_model")
        or "eleven_flash_v2_5"
    ).strip()


def _wispr_key():
    return (
        request.env["ir.config_parameter"]
        .sudo()
        .get_param("daadit_agent_voice.wispr_api_key")
        or ""
    ).strip()


def _stt_provider():
    """Which service transcribes: Wispr or one of the ElevenLabs modes."""
    value = (
        request.env["ir.config_parameter"]
        .sudo()
        .get_param("daadit_agent_voice.stt_provider")
        or "elevenlabs"
    ).strip()
    allowed = ("wispr", "elevenlabs", "elevenlabs_realtime")
    return value if value in allowed else "elevenlabs"


def _dictation_dictionary(agent):
    """Proper nouns to hand Wispr so it spells our world correctly.

    Every colleague's name is an ordinary Dutch word away from being
    mis-transcribed ("Sem" → "zem", "Lux" → "lucks"), and the same goes
    for the product vocabulary we use all day. Wispr weighs these when
    it resolves what it heard.
    """
    words = ["DAADit", "Odoo", "MCP", "helpdesk", "Knowledge", "chatter"]
    try:
        agents = request.env["ai.agent"].sudo().search([])
        words += [a.name for a in agents if a.name]
        if agent and agent.name and agent.name not in words:
            words.append(agent.name)
    except Exception:  # noqa: BLE001
        # A dictionary is an accuracy nicety, never a reason to fail.
        _logger.debug("daadit_agent_voice: could not build dictionary")
    # De-duplicate but keep order stable so requests stay comparable.
    seen, unique = set(), []
    for word in words:
        if word and word.lower() not in seen:
            seen.add(word.lower())
            unique.append(word)
    return unique


def _transcribe_wispr(blob, language, agent, user):
    """POST one turn to Wispr Flow. Returns text, or raises."""
    payload = {
        "audio": base64.b64encode(blob).decode("ascii"),
        # A single language skips detection, which is both faster and
        # more accurate than letting it choose from 100+.
        "language": [language],
        "context": {
            # 'ai' tells Flow the text is a prompt for an assistant, so
            # it formats it as an instruction rather than as prose.
            "app": {"type": "ai", "name": "Odoo"},
            "dictionary_context": _dictation_dictionary(agent),
            "user_identifier": user.email or user.login or "",
            "user_first_name": (user.name or "").split(" ")[0],
        },
    }
    response = requests.post(
        WISPR_ENDPOINT,
        headers={
            "Authorization": "Bearer %s" % _wispr_key(),
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return (response.json().get("text") or "").strip()


def _transcribe_elevenlabs(blob, filename, mimetype, language):
    """POST one turn to ElevenLabs Scribe. Returns text, or raises."""
    response = requests.post(
        f"{API_ROOT}/speech-to-text",
        headers={"xi-api-key": _api_key()},
        files={"file": (filename or "speech.wav", blob, mimetype or "audio/wav")},
        data={"model_id": STT_MODEL, "language_code": language},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return (response.json().get("text") or "").strip()


class DaaditVoiceController(http.Controller):

    @http.route(
        "/daadit_voice/voices",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def list_voices(self):
        """Voices available in the configured ElevenLabs account.

        Used by the agent form so an operator picks from a real list
        instead of pasting an opaque id.
        """
        if not request.env.user.has_group("base.group_system"):
            return {"ok": False, "error": "no_access"}
        key = _api_key()
        if not key:
            return {"ok": False, "error": "no_key"}
        try:
            response = requests.get(
                f"{API_ROOT}/voices",
                headers={"xi-api-key": key},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("daadit_agent_voice: voices lookup failed: %s", exc)
            return {"ok": False, "error": "request_failed", "detail": str(exc)[:200]}
        voices = [
            {
                "voice_id": v.get("voice_id"),
                "name": v.get("name"),
                "labels": v.get("labels") or {},
                "preview_url": v.get("preview_url"),
            }
            for v in (payload.get("voices") or [])
        ]
        return {"ok": True, "voices": voices}

    @http.route(
        "/daadit_voice/realtime_token",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def realtime_token(self, channel_id=None):
        """Issue a single-use Scribe Realtime token to the active browser.

        The token is safe to send to the browser because it is single-use,
        valid for fifteen minutes, and limited to realtime Scribe. The API
        key remains server-side and is never returned.
        """
        try:
            channel = request.env["discuss.channel"].browse(int(channel_id)).exists()
        except (TypeError, ValueError):
            channel = request.env["discuss.channel"]
        if not channel:
            return {"ok": False, "error": "no_channel"}
        try:
            channel.check_access("read")
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "no_access"}
        if _stt_provider() != "elevenlabs_realtime":
            return {"ok": False, "error": "disabled"}
        key = _api_key()
        if not key:
            return {"ok": False, "error": "no_key"}

        agent = channel.sudo().ai_agent_id
        language = (agent.daadit_voice_lang or "nl-NL").split("-")[0]
        try:
            response = requests.post(
                f"{API_ROOT}/single-use-token/realtime_scribe",
                headers={"xi-api-key": key},
                timeout=TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "daadit_agent_voice: realtime token unreachable for channel %s: %s",
                channel_id,
                exc,
            )
            return {
                "ok": False,
                "error": "unreachable",
                "detail": str(exc)[:200],
            }
        if response.status_code >= 400:
            _logger.warning(
                "daadit_agent_voice: realtime token request failed for channel "
                "%s with status %s",
                channel_id,
                response.status_code,
            )
            return {
                "ok": False,
                "error": (
                    "bad_key"
                    if response.status_code in (401, 403)
                    else "request_failed"
                ),
                "detail": response.text[:200],
            }
        try:
            token = response.json().get("token")
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "daadit_agent_voice: invalid realtime token response for channel "
                "%s: %s",
                channel_id,
                exc,
            )
            return {
                "ok": False,
                "error": "request_failed",
                "detail": response.text[:200],
            }
        if not token:
            _logger.warning(
                "daadit_agent_voice: realtime token missing for channel %s",
                channel_id,
            )
            return {
                "ok": False,
                "error": "request_failed",
                "detail": response.text[:200],
            }
        return {
            "ok": True,
            "token": token,
            "ws_url": REALTIME_WS_URL,
            "params": {
                "model_id": REALTIME_STT_MODEL,
                "language_code": language,
                "commit_strategy": "manual",
                "no_verbatim": "true",
                "filter_background_audio": "true",
            },
        }

    @http.route(
        "/daadit_voice/transcribe",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=True,
    )
    def transcribe(self, **post):
        """Turn a recorded snippet into text via ElevenLabs Scribe.

        Recording happens with MediaRecorder in the browser, which every
        modern browser supports — unlike the Web Speech API, which
        Firefox lacks entirely. That is the whole point of routing this
        through the server: one behaviour everywhere, and the audio goes
        where we decide rather than to whatever the browser vendor
        picked.
        """
        upload = post.get("audio")
        channel_id = post.get("channel_id")
        if not upload or not channel_id:
            return request.make_json_response({"ok": False, "error": "no_audio"})

        channel = request.env["discuss.channel"].browse(int(channel_id)).exists()
        if not channel:
            return request.make_json_response({"ok": False, "error": "no_channel"})
        try:
            channel.check_access("read")
        except Exception:  # noqa: BLE001
            return request.make_json_response({"ok": False, "error": "no_access"})

        key = _api_key()
        wispr_selected = _stt_provider() == "wispr" and _wispr_key()
        if not key and not wispr_selected:
            # No key at all: the caller falls back to the browser's own
            # recognition where that exists.
            return request.make_json_response({"ok": False, "error": "no_key"})

        blob = upload.read(MAX_AUDIO_BYTES + 1)
        if not blob:
            return request.make_json_response({"ok": False, "error": "empty"})
        if len(blob) > MAX_AUDIO_BYTES:
            return request.make_json_response({"ok": False, "error": "too_large"})

        agent = channel.sudo().ai_agent_id
        language = (agent.daadit_voice_lang or "nl-NL").split("-")[0]

        # --- Wispr Flow, when selected ---------------------------------
        # Its editing layer is the reason to prefer it: filler words
        # dropped, punctuation added, our colleagues' names spelled right
        # because we send them along as a dictionary.
        if wispr_selected:
            try:
                text = _transcribe_wispr(
                    blob, language, agent, request.env.user,
                )
                return request.make_json_response(
                    {"ok": True, "text": text, "provider": "wispr"}
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "daadit_agent_voice: Wispr Flow failed for channel %s "
                    "(%s)%s", channel_id, exc,
                    " — falling back to Scribe" if key else "",
                )
                if not key:
                    return request.make_json_response({
                        "ok": False,
                        "error": "request_failed",
                        "detail": str(exc)[:200],
                    })
                # A speaking colleague should not lose their sentence
                # because one provider hiccuped; Scribe finishes the turn.

        try:
            response = requests.post(
                f"{API_ROOT}/speech-to-text",
                headers={"xi-api-key": key},
                files={
                    "file": (
                        upload.filename or "speech.wav",
                        blob,
                        upload.mimetype or "audio/wav",
                    ),
                },
                data={"model_id": STT_MODEL, "language_code": language},
                timeout=TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            # Never reached the service at all: DNS, TLS, timeout.
            _logger.warning(
                "daadit_agent_voice: transcription unreachable for "
                "channel %s: %s", channel_id, exc,
            )
            return request.make_json_response(
                {"ok": False, "error": "unreachable", "detail": str(exc)[:200]}
            )

        if response.status_code >= 400:
            # The service answered and said no. Its reason is the only
            # thing that tells a spent quota apart from a bad key, so we
            # read it out and pass it on instead of collapsing every
            # failure into one opaque code — which is what sent the user
            # a bare "request_failed" while the real message ("You have 0
            # credits remaining") sat unread in the response body.
            code, detail = "", ""
            try:
                body = response.json()
                node = body.get("detail")
                if isinstance(node, dict):
                    code = str(node.get("code") or node.get("status") or "")
                    detail = str(node.get("message") or "")
                elif node:
                    detail = str(node)
            except Exception:  # noqa: BLE001
                detail = (response.text or "")[:200]
            _logger.warning(
                "daadit_agent_voice: transcription refused for channel %s "
                "(HTTP %s, code=%s): %s",
                channel_id, response.status_code, code or "?", detail,
            )
            if code == "quota_exceeded":
                # Recoverable: the caller switches to the browser's own
                # recognition rather than leaving the user with a dead
                # microphone until the quota resets.
                err = "quota_exceeded"
            elif response.status_code in (401, 403):
                err = "bad_key"
            else:
                err = "request_failed"
            return request.make_json_response(
                {"ok": False, "error": err, "detail": detail[:200]}
            )

        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "daadit_agent_voice: transcription returned no JSON for "
                "channel %s: %s", channel_id, exc,
            )
            return request.make_json_response(
                {"ok": False, "error": "request_failed", "detail": str(exc)[:200]}
            )

        return request.make_json_response(
            {"ok": True, "text": (payload.get("text") or "").strip()}
        )

    @http.route(
        "/daadit_voice/speak",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=True,
    )
    def speak(self, **post):
        """Return spoken audio for ``text`` in the agent's voice.

        Answers with an empty 204 when ElevenLabs is not configured or
        not selected for this agent — the caller then falls back to the
        browser's own voice rather than showing an error.
        """
        text = (post.get("text") or "").strip()
        channel_id = post.get("channel_id")
        if not text or not channel_id:
            return request.make_response("", status=204)

        # Access check via the channel: the record rules there already
        # decide who may be in this conversation.
        channel = request.env["discuss.channel"].browse(int(channel_id)).exists()
        if not channel:
            return request.make_response("", status=204)
        try:
            channel.check_access("read")
        except Exception:  # noqa: BLE001
            return request.make_response("", status=403)

        agent = channel.sudo().ai_agent_id
        if (
            not agent
            or not agent.daadit_voice_enabled
            or agent.daadit_voice_provider != "elevenlabs"
            or not agent.daadit_elevenlabs_voice_id
        ):
            return request.make_response("", status=204)

        key = _api_key()
        if not key:
            return request.make_response("", status=204)

        try:
            response = requests.post(
                f"{API_ROOT}/text-to-speech/{agent.daadit_elevenlabs_voice_id}/stream",
                params={"output_format": TTS_OUTPUT_FORMAT},
                headers={
                    "xi-api-key": key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                data=json.dumps(
                    {
                        "text": text[:MAX_CHARS],
                        "model_id": _model_id(),
                        "voice_settings": {
                            "stability": agent.daadit_elevenlabs_stability or 0.5,
                            "similarity_boost": (
                                agent.daadit_elevenlabs_similarity or 0.75
                            ),
                        },
                    }
                ),
                timeout=TIMEOUT,
                stream=True,
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            # Out of credits, key revoked, service down — all end the
            # same way: stay quiet here and let the browser voice speak.
            _logger.warning(
                "daadit_agent_voice: ElevenLabs speak failed for agent %s: %s",
                agent.id, exc,
            )
            return request.make_response("", status=204)

        return request.make_response(
            response.iter_content(chunk_size=8192),
            headers=[
                ("Content-Type", "audio/mpeg"),
                ("Cache-Control", "no-store"),
            ],
        )
