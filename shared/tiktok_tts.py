"""
tiktok_tts.py
-------------
TikTok TTS — handles text of any length by splitting into chunks,
making multiple API calls, and concatenating the results with ffmpeg.

Requires:
  - A TikTok session_id (grab from browser cookies on tiktok.com)
  - pip install requests
  - ffmpeg installed system-wide

Usage:
    from tiktok_tts import tiktok_tts

    path, duration = tiktok_tts(
        session_id="your_session_id_here",
        req_text="Any length of text you want spoken",
        text_speaker="en_us_ghostface",
        filename="voice.mp3"
    )
"""

import base64
import math
import os
import re
import subprocess
import tempfile
import shutil

import requests

# ---------------------------------------------------------------------------
# Available voices
# ---------------------------------------------------------------------------
TIKTOK_VOICES = {
    # English US
    "en_us_001":           "US Female (Jessie)",
    "en_us_002":           "US Female",
    "en_us_006":           "US Male",
    "en_us_007":           "US Male",
    "en_us_009":           "US Male",
    "en_us_010":           "US Male",
    "en_us_ghostface":     "Ghostface (Scream)",
    "en_us_stormtrooper":  "Stormtrooper",
    "en_us_rocket":        "Rocket (Guardians)",
    "en_us_c3po":          "C-3PO",
    "en_us_chewbacca":     "Chewbacca",
    "en_us_deadpool":      "Deadpool",
    # English UK
    "en_uk_001":           "UK Female",
    "en_uk_003":           "UK Male",
    # English AU
    "en_au_001":           "AU Female",
    "en_au_002":           "AU Male",
    # Narrator / character voices
    "en_male_narration":   "Narrator",
    "en_male_funny":       "Funny/Pirate-ish",
    "en_female_emotional": "Emotional Female",
    "en_male_cody":        "Cody",
    "en_female_samc":      "Samantha",
}

TIKTOK_CHAR_LIMIT = 280  # 20 char headroom under TikTok's 300 hard limit


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _split_text(text: str) -> list[str]:
    """
    Split text into chunks that each fit within TIKTOK_CHAR_LIMIT.
    Splits on sentence boundaries (.!?) and builds chunks greedily.
    Falls back to comma splitting for sentences that are themselves too long.
    """
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks = []
    current = ""

    for sentence in sentences:
        # Sentence itself is over limit — split on commas as fallback
        if len(sentence) > TIKTOK_CHAR_LIMIT:
            if current:
                chunks.append(current)
                current = ""
            parts = sentence.split(", ")
            sub = ""
            for part in parts:
                if len(sub) + len(part) + 2 <= TIKTOK_CHAR_LIMIT:
                    sub = f"{sub}, {part}".strip(", ")
                else:
                    if sub:
                        chunks.append(sub)
                    sub = part
            if sub:
                chunks.append(sub)
        elif len(current) + len(sentence) + 1 <= TIKTOK_CHAR_LIMIT:
            current = f"{current} {sentence}".strip()
        else:
            if current:
                chunks.append(current)
            current = sentence

    if current:
        chunks.append(current)

    return chunks


def _call_api(session_id: str, text: str, text_speaker: str, filename: str) -> tuple[str, int] | tuple[None, None]:
    """Make a single TikTok TTS API call for a chunk of text under the char limit."""
    sanitised = text.replace("+", "plus").replace("&", "and").replace(" ", "+")

    headers = {
        "User-Agent": (
            "com.zhiliaoapp.musically/2022600030 "
            "(Linux; U; Android 7.1.2; es_ES; SM-G988N; Build/NRD90M;tt-ok/3.12.13.1)"
        ),
        "Cookie": f"sessionid={session_id}",
    }
    url = (
        f"https://api16-normal-v6.tiktokv.com/media/api/text/speech/invoke/"
        f"?text_speaker={text_speaker}"
        f"&req_text={sanitised}"
        f"&speaker_map_type=0"
        f"&aid=1233"
    )

    try:
        r = requests.post(url, headers=headers)
        data = r.json()
    except Exception as e:
        print(f"[TikTokTTS] Request failed: {e}")
        return None, None

    if data.get("message") == "Couldn't load speech. Try again.":
        print("[TikTokTTS] Session ID is invalid or expired.")
        return None, None

    if data.get("status_code") != 0:
        print(f"[TikTokTTS] API error: {data.get('message')} (code {data.get('status_code')})")
        return None, None

    try:
        vstr    = data["data"]["v_str"]
        dur     = data["data"]["duration"]
        speaker = data["data"]["speaker"]
        log     = data["extra"]["log_id"]
    except KeyError as e:
        print(f"[TikTokTTS] Unexpected response structure: {e}")
        return None, None

    b64d = base64.b64decode(vstr)
    with open(filename, "wb") as out:
        out.write(b64d)

    duration_seconds = math.ceil(float(dur))
    print(f"[TikTokTTS] Chunk OK — speaker={speaker}, duration={duration_seconds}s, log={log}")

    return filename, duration_seconds


def _concat_mp3s(paths: list[str], output_path: str) -> bool:
    """Concatenate multiple mp3 files into one using ffmpeg concat demuxer."""
    list_path = output_path + ".txt"
    try:
        with open(list_path, "w") as f:
            for p in paths:
                f.write(f"file '{p}'\n")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"[TikTokTTS] ffmpeg concat failed: {e}")
        return False
    finally:
        if os.path.exists(list_path):
            os.unlink(list_path)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------
def tiktok_tts(
    session_id: str,
    req_text: str = "TikTok Text To Speech",
    text_speaker: str = "en_us_ghostface",
    filename: str = "voice.mp3",
) -> tuple[str, int] | tuple[None, None]:
    """
    Convert any length of text to speech using TikTok's internal TTS API.
    Handles chunking and concatenation internally — caller just gets a single mp3.

    Returns:
        (filename, total_duration_seconds) on success
        (None, None) on failure
    """
    chunks = _split_text(req_text)
    print(f"[TikTokTTS] {len(chunks)} chunk(s) for {len(req_text)} chars")

    chunk_paths = []
    total_duration = 0

    try:
        for i, chunk in enumerate(chunks):
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                chunk_path = f.name
            result, dur = _call_api(session_id, chunk, text_speaker, chunk_path)
            if result is None:
                return None, None
            chunk_paths.append(chunk_path)
            total_duration += dur
            print(f"[TikTokTTS] Chunk {i+1}/{len(chunks)} done ({len(chunk)} chars)")

        # Single chunk — just move to final destination
        if len(chunk_paths) == 1:
            shutil.move(chunk_paths[0], filename)
            chunk_paths = []
        else:
            if not _concat_mp3s(chunk_paths, filename):
                return None, None

        return filename, total_duration

    finally:
        for p in chunk_paths:
            if os.path.exists(p):
                os.unlink(p)