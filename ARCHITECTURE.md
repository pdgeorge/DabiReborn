# Dabi — Architecture Overview

Dabi is a unicorn mascot/AI companion that exists in two independent forms:
- **Stream Dabi** — a live stream companion, active while streaming
- **Website Dabi** — a persistent chatroom presence on pdgeorge.com.au/dabi

These two forms do not share memory, history, or state.

---

## External Services (out of scope for this repo)

| Service | What it is |
|---------|------------|
| `twitch-broadcaster` | Overlay service. Listens to Twitch EventSub, publishes events to RabbitMQ. Runs on Pi. Already exists. |
| `pdgeorge.com.au` | Main website. Calls Dabi endpoints. Separate fullstack repo. |
| RabbitMQ | Message broker. Already running on Pi via `twitch-broadcaster`. |

---

## This Repository — `dabi/`

```
dabi/
  shared/              ← Shared classes used across all services
  stream_client/       ← Runs on local machine while streaming
  dabi-stream-brain/   ← Pi Docker container (Pi App + FastAPI /react + Discord)
  dabi-chatroom-brain/ ← Pi Docker container (Website Dabi brain + FastAPI /chat)
```

### docker-compose.yml (Pi, this repo)
```yaml
services:
  dabi-stream-brain:
  dabi-chatroom-brain:
```
RabbitMQ is not here — it is already running in `twitch-broadcaster`.

---

## shared/

Classes consumed across all services.

| Class | Responsibility |
|-------|---------------|
| `LLMService` | All LLM calls. Model swapping, tool calls, memory management, personality (system prompt), history save/load on crash. Text in, text out. |
| `TTSService` | All TTS engines (gTTS, TikTok, AI TTS). Text in, audio file path out. |
| `AudioPlayer` | Local audio playback. Owns playback state, one-at-a-time queue. |
| `DiscordService` | Discord bot. Messages, voice channel, TTS playback. Eventually: voice receive + STT. |
| `AvatarService` | Avatar visual logic. Amplitude → rotation. Calls `OBSWebsocketManager`. Long term: pipe to 3D avatar program. |
| `OBSWebsocketManager` | Raw OBS websocket calls. The *how* behind AvatarService. |

---

## Stream Dabi

See `dabi-stream-brain/` and `stream_client/` for full detail.

**Summary:** The brain runs on the Pi (`dabi-stream-brain`), the body runs locally (`stream_client`). They communicate via RabbitMQ. When Dabi needs to speak, `dabi-stream-brain` publishes a `dabi.tts.ready` event carrying the **text**. `stream_client` receives it, generates audio locally via `TTSService`, and plays it via `AudioPlayer` while driving `AvatarService`/OBS.

**Event sources:**
- Twitch events → `twitch-broadcaster` → RabbitMQ → `dabi-stream-brain`
- Website react → `pdgeorge.com.au/react` (password protected) → `dabi-stream-brain`
- Hotkeys → pynput on local machine → RabbitMQ → `dabi-stream-brain`

---

## Website Dabi

See `dabi-chatroom-brain/` for full detail.

**Summary:** A persistent, always-on chatroom at `pdgeorge.com.au/dabi`. Anyone can visit and chat. Dabi participates as one of the chatters. Single shared global conversation. Text only — no audio, no OBS, no Discord. Claude for MVP, swap to a local model later via `LLMService` config.

**Event sources:**
- `pdgeorge.com.au/dabi` → POST /chat → `dabi-chatroom-brain`

---

## What runs where

| Component | Where |
|-----------|-------|
| RabbitMQ | Pi (`twitch-broadcaster`) |
| `dabi-stream-brain` | Pi (Docker) |
| `dabi-chatroom-brain` | Pi (Docker) |
| DiscordService | Pi (inside `dabi-stream-brain`) |
| `stream_client` | Local machine (only while streaming) |
| AudioPlayer | Local machine |
| AvatarService + OBS | Local machine |
| Hotkey listener | Local machine |