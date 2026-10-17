# Who is Dabi?
Dabi is a derpy unicorn that exists in two independent forms:

**Stream Dabi** — a live stream companion, active while streaming:
- STT via Discord -> Inference -> TTS
- Twitch event (channel point redemption, chat message, subscription, etc.) -> Inference -> TTS
- Avatar which reacts as he talks
- A password-protected endpoint for the streamer to upload images/text for Dabi to react to

**Website Dabi** — a persistent, always-on chatroom at pdgeorge.com.au/dabi:
- Anyone can visit and chat
- Dabi participates as one of the chatters in a single shared global conversation
- Text only — no audio, no avatar

These two forms do not share memory, history, or state.

---

# How Dabi works

## Twitch Events
Twitch events are handled by `twitch-broadcaster` (separate repo, already exists). It listens to Twitch EventSub and re-broadcasts all events via RabbitMQ. `dabi-stream-brain` consumes from RabbitMQ.

## The App (dabi-stream-brain)
There is a central App which is responsible for coordinating everything. While multiple actions are all within the App, those actions are all handled within handlers.

Example: After receiving an "Ask Dabi a question" redeem, the App routes to the ask_dabi handler, which calls LLMService and receives the response, then publishes a `dabi.tts.ready` event (carrying text) to RabbitMQ. `stream_client` on the local machine picks this up, generates audio via TTSService, and plays it via AudioPlayer while simultaneously sending the audio to DiscordService.

**Note: App "decides" based on what it receives. Handlers "do".**

## Classes

**LLMService** — all LLM activities. Allows fast swapping between models. Responsible for: sending messages to whichever LLM is configured, returning only the response, tool calls, memory management (full history or single shot), personality (system prompt), saving and loading history on crash/failure.

**TTSService** — all TTS engines (gTTS, AI TTS, TikTok TTS). Text in, audio file path out. Handles chunking and engine selection internally.

**AudioPlayer** — local audio playback. Owns playback state and enforces one-at-a-time queue.

**DiscordService** — spins up a Discord bot. Responsible for: sending Discord messages, joining voice channels, playing TTS audio into voice. Eventually: listening to voice calls and converting speech to text.

**AvatarService** — all avatar visual logic. Initial version: one image, rotates 0–25 degrees (0.436332 radians) based on amplitude of audio being spoken. Calls OBSWebsocketManager to execute. Long term: pipe to a 3D avatar program — at that point AvatarService becomes mostly a pipe, animation logic lives in the 3D program.

**OBSWebsocketManager** — raw OBS websocket calls. The *how* behind AvatarService. No animation logic lives here.

**WebsiteService (dabi-stream-brain)** — FastAPI application. Password-protected. Receives events from pdgeorge.com.au and triggers the App. Example: POST /react accepts image files (.png, .jpg, etc.) + text, puts it on the work queue for the App to handle.

**WebsiteService (dabi-chatroom-brain)** — FastAPI application. Public. Receives chat messages from pdgeorge.com.au/dabi, passes to LLMService, returns response. Single shared global conversation context.

---

# Event Sources

- `twitch-broadcaster` → RabbitMQ (Twitch events, Pi, separate repo)
- FastAPI POST /react (pdgeorge.com.au → dabi-stream-brain, password protected)
- Push-to-talk (local machine) → RabbitMQ → dabi-stream-brain: see "Push-to-talk" below. pynput is out, because global hotkeys don't work on KDE Wayland.
- FastAPI POST /chat (pdgeorge.com.au → dabi-chatroom-brain, public)

---

# What runs where

| Component | Where |
|-----------|-------|
| RabbitMQ | Pi (twitch-broadcaster) |
| dabi-stream-brain | Pi (Docker) |
| dabi-chatroom-brain | Pi (Docker) |
| DiscordService | Pi (inside dabi-stream-brain) |
| stream_client | Local machine (only while streaming) |
| AudioPlayer | Local machine |
| AvatarService + OBS | Local machine |
| Hotkey listener | Local machine |
---

# Push-to-talk (planned, 2026-10-17)

How much Dabi does on stream works in three levels: **mostly redeem-triggered** (already works) → **push-to-talk** when George wants to talk to him directly (this section) → **hand him the stream** while George steps away (the existing `!dabichat` toggle).

```
PC (cachyowo)                                   Pi
hotkey ─► record mic ─► faster-whisper ─► text ─► RabbitMQ :5672 ─► dabi-stream-brain ─► dabi.tts.ready ─► dabi-voice ─► OBS
```

Speech-to-text runs on the PC, not the Pi: the mic is there, the 4070 SUPER transcribes a short clip in well under a second, and the Pi is already busy with Ollama. Only text crosses to the Pi, like everything else in the brain.

**1. PC client (new, small script).** Press the hotkey to start recording the mic, press it again to stop. Transcribe with faster-whisper, reusing the settings from `bob-twitch-chatter/services/transcriber.py` (pinned language, `vad_filter`, `condition_on_previous_text=False`, `no_repeat_ngram_size=3`). Publish `{"text": ...}` with AMQP type `dabi.ptt.message` to the `dabi_events` exchange at `192.168.20.14:5672` (already exposed by `twitch-broadcaster`; pd-streamdeck publishes there the same way). Play a short sound on start and stop so it's obvious when Dabi is listening.

**2. Hotkey.** KDE Wayland blocks global key grabs, so pynput won't work. Plan: a KDE global shortcut runs something like `dabi-ptt toggle`, which signals the running client. KDE shortcuts fire on key press only, so it's a toggle, not hold-to-talk. If hold-to-talk is ever wanted: read the key with `evdev`, which needs the user in the `input` group. A Stream Deck button would also work, but the deck controller runs on the Pi and would need a way to signal the PC client.

**3. Brain handler (`dabi-stream-brain/handlers/ptt.py`).** Modelled on `discord_message.py`: prompt like "George, the streamer, is talking to you directly: <text>", return the reply. Register it in `router.py` as `"dabi.ptt.message": (ptt.handle, "dabi.tts.ready")`. Nothing changes in `dabi-voice`.

**4. Priority.** Bump the chat batch the same way `channel_point.py` does, so a pending batched chat reply is discarded and George gets answered first.

## Speaking, for reference

- `dabi-voice` serves the overlay from the Pi on `:8090`, and OBS loads it as a browser source on the PC, so the audio plays in OBS. With `?renderer=vnyan` the overlay draws nothing and drives the VNyan prop instead (`DabiOpen` / `DabiClose`, blinks).
- Any TTS works: `dabi-voice` only needs an mp3 from `TTSService.generate()`. Rhubarb makes mouth cues from any audio, and the overlay falls back to volume-driven mouth movement without them. A new voice = a new engine in `shared/tts_service.py` + `shared/dabi.json`.
- Two ways to make him talk: publish `dabi.tts.ready {"text"}` to `dabi_events` (normal path, after the LLM), or `POST http://192.168.20.14:8090/say {"text"}` (skips the brain; he says exactly that, which suits scripted redebut lines).
- If the OBS browser source isn't connected, a line is dropped, not queued.
- Voice: still undecided. Rules: only George's own voice or voices he performs; likely a cartoon voice George performs, then clones. A cloning model would be slow on the Pi, so run it on the PC's GPU and have the new TTS engine call it over the LAN.
