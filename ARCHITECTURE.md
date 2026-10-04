# Dabi — Architecture Overview

Dabi is a unicorn mascot/AI companion that exists in two independent forms:
- **Stream Dabi** — a live stream companion: answers channel-point questions, optionally chats with Twitch chat, talks in Discord, and speaks on stream through an animated Live2D avatar
- **Website Dabi** — a persistent chatroom presence on pdgeorge.com.au/chat

These two forms do not share memory, history, or state.

---

## External Services (out of scope for this repo)

| Service | What it is |
|---------|------------|
| `twitch-broadcaster` | Listens to Twitch EventSub and publishes every event to the `twitch_events` RabbitMQ exchange. Also hosts RabbitMQ itself. Runs on Pi. |
| `dabidotcom` (pdgeorge.com.au) | Main website. Hosts the chatroom frontend and talks to Website Dabi over the `website_events` exchange. Separate repo. |
| RabbitMQ | Message broker. Runs on Pi as part of `twitch-broadcaster`. |

---

## This Repository — `DabiReborn/`

```
DabiReborn/
  shared/              ← Shared classes + personality files, baked into every image
  dabi-stream-brain/   ← Pi Docker image: Stream Dabi's brain (app.py) and the Discord bot (discord_bot.py)
  dabi-chatroom-brain/ ← Pi Docker container: Website Dabi's brain
  dabi-voice/          ← Pi Docker container: Dabi's mouth (TTS + Rhubarb lip sync +
                          Live2D avatar overlay served as an OBS browser source on :8090)
  stream_client/       ← Local end-to-end test client (superseded by dabi-voice for playback)
```

### docker-compose.yml (Pi, this repo)
```yaml
services:
  dabi-stream-brain:    # dabi-stream-brain/app.py
  dabi-discord:         # same image, runs discord_bot.py
  dabi-chatroom-brain:
  dabi-voice:
  ollama:               # local LLM (gemma4:e2b) used by both brains
```
RabbitMQ is not here — it runs in `twitch-broadcaster`. Every service reaches it over the external `twitchbroadcaster_net` Docker network; nothing in this repo is called over HTTP except `dabi-voice`.

---

## shared/

Classes and data consumed across services.

| Piece | Responsibility |
|-------|---------------|
| `LLMService` (`llm_service.py`) | All LLM calls. Text in, text out. Personality (system prompt) from a JSON file. History kept in memory, rolled back on API failure, wiped by `reset_history()`. Backends via `LLM_BACKEND`: `anthropic` (claude-haiku-4-5, supports images), `ollama` (trimmed history, no images), `mock`. |
| `TTSService` (`tts_service.py`) | All TTS engines: `edge` (free, via `edge_tts_engine.py`) and `tiktok` (via `tiktok_tts.py`). Text in, `(mp3 path, duration)` out. |
| `dabi.json` / `website_dabi.json` | Stream Dabi / Website Dabi personality (system prompt) and voice settings. |

Planned but not built: `AudioPlayer`, `DiscordService` as a shared class, `AvatarService`, `OBSWebsocketManager`, tool calls, and saving/loading history across crashes. The avatar logic ended up in the dabi-voice overlay's JS instead of `AvatarService`/OBS websocket.

---

## Stream Dabi

See `dabi-stream-brain/` and `dabi-voice/` for full detail.

**Summary:** The brain runs on the Pi (`dabi-stream-brain`); the body is `dabi-voice`, also on the Pi. `router.py` maps each event type to one handler. When Dabi needs to speak, `dabi-stream-brain` publishes a `dabi.tts.ready` event carrying the **text** to the `dabi_events` exchange. `dabi-voice` consumes it, generates audio via `TTSService` (edge), runs Rhubarb Lip Sync for mouth cues, and pushes audio + cues + caption over WebSocket to an overlay page loaded as an **OBS browser source** — the audio plays and the Live2D model lip-syncs inside OBS on the streaming PC. Discord replies go back to `dabi-discord` as `dabi.discord.response` instead.

**Event sources:**
- Twitch events → `twitch-broadcaster` → RabbitMQ (`twitch_events`) → `dabi-stream-brain`:
  - "Ask Dabi a question" redeem → Dabi answers it
  - regular chat → batched and answered as a group, only while chat replies are on ("dabichat" redeem or `!dabichat` toggles them)
  - `!dabireset` (mods) → wipe Dabi's memory
  - `stream.online` → wipe memory, turn chat replies off, say good morning
- Discord messages in the listen channel → `dabi-discord` → RabbitMQ (`dabi_events`) → `dabi-stream-brain`
- `!dabi <x>, <y>` / `!dabi reset` (broadcaster) → `dabi-voice` directly, to move the avatar
- Future: website react (`pdgeorge.com.au/react`, password protected) and hotkeys (pynput on the local machine)

---

## From brain to "Dabi speaks"

The full path a line takes, from a Twitch event to Dabi talking on stream. Only **text** crosses RabbitMQ. Audio and mouth cues are made in `dabi-voice`, and playback happens in the OBS browser source.

```
Twitch event ─► twitch_events ─► dabi-stream-brain ─► LLMService ─► reply text
                                                                       │
                                     dabi_events  ◄── dabi.tts.ready {"text"}
                                          │
                                     dabi-voice ─► TTSService (mp3) ─► ffmpeg (wav) ─► Rhubarb (mouth cues)
                                          │
                                     WS /ws/voice ◄── dabi.speak {text, audio_url, duration, mouth_cues}
                                          │
                              OBS browser source (overlay.js) ─► plays /audio/<id>.mp3 + caption + Live2D mouth
```

**1. The brain decides what to say** (`dabi-stream-brain/app.py`, `router.py`, `handlers/`)
- `app.py` consumes the `twitch_events` and `dabi_events` exchanges (both fanout) and passes each message to `route(event_type, payload, services)`.
- `router.py` looks up the handler and its default response type in `HANDLERS`. Most Twitch events default to `dabi.tts.ready`, and Discord messages default to `dabi.discord.response`. A handler can return a `(text, event_type)` tuple to use a different type (for example `!goinglive` → `dabi.discord.announce`).
- The handler builds a prompt and calls `services.llm.chat(prompt)` (`shared/llm_service.py`), which returns plain text. Example: the "Ask Dabi a question" redeem becomes `"<user> paid channel points to ask: <question>"`.
- Batched chat is the exception. `chat_message.enqueue` only buffers messages. The `_chat_flusher` task in `app.py` asks the LLM once a batch is due, off-thread via `asyncio.to_thread`. If a redeem landed during that LLM call (`chat_batch.generation` changed), the reply is discarded.

**2. The brain publishes text** (`app.py: _publish`)
- The message published to `dabi_events` has body `{"text": "<reply>"}` and AMQP `type` = `dabi.tts.ready`. The brain stops here. It never touches audio.
- If a handler throws on an image, a canned fallback line is published instead.

**3. The mouth turns text into audio + lip-sync** (`dabi-voice/app.py: speak`)
- `dabi-voice` binds the durable queue `dabi_voice_inbound` to `dabi_events` with `prefetch_count=1`, so lines are spoken one at a time and in order. It ignores every message type except `dabi.tts.ready`.
- `TTSService.generate(text, engine, voice)` writes an mp3. Engine and voice come from `shared/dabi.json` (`edge` / `en-GB-RyanNeural`).
- `generate_mouth_cues`: ffmpeg converts the mp3 to a 16 kHz mono wav, then `rhubarb --dialogFile <text>` produces timed mouth shapes (`A`–`H`, `X`). Passing the known text makes the sync more accurate. If ffmpeg or Rhubarb fails or is missing, `mouth_cues` is `null`. That isn't fatal.
- The mp3 is moved into `tmp/serve/` and served at `/audio/<id>.mp3`. Files older than 15 min are cleaned up on each new line.
- `broadcast()` sends `{"type": "dabi.speak", id, text, audio_url, duration, mouth_cues}` to every connected `/ws/voice` client. **If no overlay is connected, the line is dropped.** It is not queued for later.
- `POST /say {"text": ...}` runs the same `speak()` without RabbitMQ, for testing.

**4. The overlay performs it** (`dabi-voice/overlay/overlay.js`, `live2d-renderer.js`)
- The page at `http://<pi>:8090/` is loaded as an OBS browser source. It holds a WebSocket to `/ws/voice` and reconnects every 3 s if the connection drops.
- `dabi.speak` payloads go into a client-side queue and play one at a time: `new Audio(audio_url)`, the caption bubble shows `text`, and `renderer.setTalking(true)`.
- Each animation frame, the current mouth shape is chosen in one of two ways:
  - **Cue path** (Rhubarb cues present): the cue matching `audio.currentTime` goes to `renderer.setMouthShape(shape)`.
  - **Amplitude fallback** (no cues): the audio is routed through a Web Audio analyser, and the peak level goes to `renderer.setLevel(level)`.
- The renderer is pluggable. The Live2D renderer replaces the PNG mouth-flap renderer once the model loads, and the PNG version stays as the fallback. The Live2D renderer maps each Rhubarb shape to `ParamMouthOpenY`/`ParamMouthForm` targets (the mouth snaps open and eases closed), tilts the head up as the mouth opens, and adds blinking and a body bob that is stronger while talking.
- When the audio ends: `setTalking(false)`, the caption hides after 1.2 s, and the next queued line plays. If autoplay is blocked, the overlay retries 5 times, 3 s apart, then skips the line so the queue doesn't get stuck. Client state is sent to `POST /debug` so problems in the OBS browser source show up in the `dabi-voice` logs.

**Ordering / backpressure:** RabbitMQ (prefetch 1) makes `dabi-voice` generate lines in order, and the overlay's own queue makes them play in order. `dabi-voice` doesn't wait for playback to finish, so a long line can still be playing while the next one is being generated.

---

## Website Dabi

See `dabi-chatroom-brain/` for full detail.

**Summary:** A persistent, always-on chatroom at `pdgeorge.com.au/chat`. Anyone can visit and chat. Dabi participates as one of the chatters. Single shared global conversation. Text only — no audio, no OBS, no Discord. Runs on the local Ollama model (compose sets `LLM_BACKEND=ollama`); switching back to Claude is an `LLMService` config change.

**Event sources:**
- Browser → WebSocket `/chat/ws` on the website → batched every 30 s → RabbitMQ (`website_events`: `website.chat.to_dabi`) → `dabi-chatroom-brain` → reply as `website.chat.from_dabi` → website broadcasts it to everyone

---

## What runs where

| Component | Where |
|-----------|-------|
| RabbitMQ | Pi (`twitch-broadcaster`) |
| `dabi-stream-brain` | Pi (Docker) |
| Discord bot (`dabi-discord`) | Pi (Docker) |
| `dabi-chatroom-brain` | Pi (Docker) |
| `dabi-voice` (TTS + lip sync + overlay server) | Pi (Docker, :8090) |
| `ollama` (LLM for both brains) | Pi (Docker, :11434) |
| Avatar rendering + audio playback | OBS browser source (streaming PC) pointed at `http://<pi>:8090/` |
| Hotkey listener | Local machine (future) |
