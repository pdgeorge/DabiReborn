"""
dabi-stream-brain/chat_batch.py
-------------------------------
Buffers regular Twitch chat so Dabi can reply to the room instead of to
every single line. Two triggers, one flush path:

  - size:   BATCH_SIZE messages collected  -> due immediately
  - time:   BATCH_WINDOW seconds since the first message in the batch

Both triggers just move the deadline; the only thing that actually
drains the buffer is the flusher task in app.py. Keeping a single
drain point means the two triggers can never fire concurrently and
double-answer the same chat.

State is deliberately plain and synchronous — no asyncio in here — so
handlers (which are sync) can push into it without ceremony.

Off by default. Toggled by the 'dabichat' channel point redeem, by
!dabichat from the broadcaster/mods, and reset to off at stream start.
"""

import logging
import os
import time

LOGGER = logging.getLogger(__name__)

BATCH_SIZE = int(os.getenv("DABI_CHAT_BATCH_SIZE", "10"))
BATCH_WINDOW = float(os.getenv("DABI_CHAT_BATCH_WINDOW", "30"))

# Per-message and whole-batch caps. gemma4:e2b runs at num_ctx 2048 with a
# ~410 token system prompt, so an unbounded batch would push the rest of the
# conversation straight out of the window.
MAX_MESSAGE_CHARS = int(os.getenv("DABI_CHAT_MAX_MESSAGE_CHARS", "200"))
MAX_BATCH_CHARS = int(os.getenv("DABI_CHAT_MAX_BATCH_CHARS", "1200"))

# Comma-separated chatter logins to never batch (bots, other overlays).
_IGNORE_RAW = os.getenv("DABI_CHAT_IGNORE", "")
IGNORED_LOGINS = {n.strip().lower() for n in _IGNORE_RAW.split(",") if n.strip()}


class ChatBatch:
    """Pending chat lines plus the flags that decide when Dabi answers them."""

    def __init__(self, size: int = BATCH_SIZE, window: float = BATCH_WINDOW):
        self.enabled = False
        self.size = size
        self.window = window
        self.in_flight = False  # a flush is mid-LLM-call; skip further ticks
        # Bumped by clear(). The flusher captures it before its (slow) LLM
        # call and re-checks after: if a redeem preempted in the meantime,
        # the now-stale chat reply is dropped instead of being spoken on
        # top of the paid answer.
        self.generation = 0
        self._messages: list[tuple[str, str]] = []
        self._deadline: float | None = None

    # -- writing ---------------------------------------------------------

    def add(self, username: str, text: str) -> None:
        """
        Buffer one chat line. Sets the deadline to now once the batch is
        full so the next flusher tick picks it up.
        """
        if not self.enabled:
            return

        text = text.strip()[:MAX_MESSAGE_CHARS]
        if not text:
            return

        self._messages.append((username, text))

        if self._deadline is None:
            self._deadline = time.monotonic() + self.window

        if len(self._messages) >= self.size:
            self._deadline = time.monotonic()  # due right now

    # -- reading ---------------------------------------------------------

    def due(self, now: float | None = None) -> bool:
        """True when there is something to say and it is time to say it."""
        if not self._messages or self._deadline is None or self.in_flight:
            return False
        return (now if now is not None else time.monotonic()) >= self._deadline

    def take(self) -> list[tuple[str, str]]:
        """Drain the buffer and reset the timer. Returns the drained lines."""
        messages, self._messages = self._messages, []
        self._deadline = None
        return messages

    def clear(self) -> int:
        """
        Drop everything pending and reset the timer. Used when a channel
        point redeem preempts — the paid question goes first, and chat
        doesn't get answered on top of it a moment later.

        Returns how many messages were dropped (for logging).
        """
        dropped = len(self._messages)
        self._messages = []
        self._deadline = None
        self.generation += 1
        return dropped

    # -- misc ------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        """Flip the toggle. Turning off also drops anything pending."""
        self.enabled = enabled
        if not enabled:
            self.clear()

    @property
    def pending(self) -> int:
        return len(self._messages)


def should_batch(event: dict) -> bool:
    """
    Whether a channel.chat.message event belongs in the batch.

    Excludes commands (any '!' prefix — those are for admin_command and
    dabi-voice's !dabi positioning, not conversation) and any login in
    DABI_CHAT_IGNORE. The broadcaster is deliberately *included*: Dabi
    talking with the streamer is the point.
    """
    text = str(event.get("message", {}).get("text") or "").strip()
    if not text or text.startswith("!"):
        return False

    login = str(event.get("chatter_user_login") or "").lower()
    if login and login in IGNORED_LOGINS:
        return False

    return True


def build_prompt(messages: list[tuple[str, str]]) -> str:
    """
    Turn drained chat lines into one prompt. Framed as ambient room
    chatter so the model weights it below a paid 'X asks:' redeem.
    """
    lines: list[str] = []
    used = 0
    for username, text in messages:
        line = f"{username}: {text}"
        if used + len(line) > MAX_BATCH_CHARS:
            LOGGER.info("Batch prompt truncated at %d/%d messages", len(lines), len(messages))
            break
        lines.append(line)
        used += len(line)

    body = "\n".join(lines)
    return (
        "Chat has been talking while you were quiet. Here's what they said:\n\n"
        f"{body}\n\n"
        "Reply to the room in one or two sentences. You don't have to address "
        "everyone — pick out whatever's most interesting and run with it."
    )
