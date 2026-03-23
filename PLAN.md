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
- pynput → hotkeys (local machine) → RabbitMQ → dabi-stream-brain
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