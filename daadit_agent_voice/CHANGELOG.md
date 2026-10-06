# Changelog — daadit_agent_voice

## 19.0.12.0.0 — 2026-09-01 — Scribe v2 Realtime

Een gesproken beurt werd pas na het stoppen van de opname uitgeschreven,
waardoor de tekst onnodig laat beschikbaar kwam. Scribe v2 Realtime is nu
als omzetbare keuze toegevoegd onder Instellingen → Praten met AI-agents.
De single-use-token wordt server-side opgehaald, audio wordt alleen tijdens
spraak gestreamd en de batch-route blijft de automatische terugval.

## 19.0.11.0.0 — 2026-09-01 — Vloeiender handsfree gesprek

In een handsfree gesprek werden lange geschreven rapporten voorgelezen en
bleef het stil terwijl een concierge-agent een specialist inschakelde. De
voice-stand wordt nu tijdelijk op het kanaal gemarkeerd, zodat beide
providerlagen korte spreektaal kunnen kiezen. De eerste tussenstappen worden
met de browserstem hoorbaar gemaakt en ElevenLabs levert de lichtere
spraakstream direct door. Ook is de dubbele versie-sleutel in het manifest
hersteld.

## 19.0.9.2.0 — 2026-08-07 — Spraak soepeler: PTT, mic tijdens TTS, races

Push-to-talk en handsfree voelden regelmatig "dood" of namen het antwoord
van de agent mee als volgende vraag. Concrete fixes:

- **PTT stuurde geen audio** — `beginPush`/`endPush` keken naar
  `this.recording` (bestaat niet) i.p.v. `this.recorder`.
- **Mic bleef opnemen tijdens TTS** — `pause()` stopte het segment alleen
  als er al VAD-spraak was; de stem van de agent belandde in de volgende
  transcriptie. Nu altijd stoppen; Analyser blijft aan voor barge-in.
- **PTT werd na een antwoord handsfree** — `scheduleRestart` hervatte de
  recorder ook in hold-to-talk-modus.
- **Key-race** — keyup tijdens getUserMedia/calibratie (~700 ms) startte
  alsnog een push; nu alleen als de toets nog ingedrukt is.
- **Handsfree-toets onderbreekt** de agent (`stopSpeaking`).
- **`no-input` houdt het gesprek open** i.p.v. de service te stoppen
  terwijl de UI nog "live" bleef.
- **Late STT** na kanaalwissel wordt genegeerd (conversation epoch).
- **Snelle VAD-beurten** gaan door een send-queue; overlapping
  `sendMessage` droop zinnen.
- **Speak-epoch** voorkomt dat een nieuw antwoord een oud nog aan het
  uitspreken is.
- Manifesttekst bijgewerkt: MediaRecorder + ElevenLabs, niet pure Web Speech.
