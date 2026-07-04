/*
 * Dabi voice overlay.
 *
 * Receives {"type": "dabi.speak", text, audio_url, duration, mouth_cues}
 * over /ws/voice, queues them, and plays one at a time. Also receives
 * {"type": "dabi.position", x, y} (numbers pin the avatar's top-left to
 * that page pixel; nulls restore the default centered layout):
 *   - audio plays inside the OBS browser source
 *   - mouth animates from Rhubarb cues when present,
 *     otherwise from live amplitude (Web Audio analyser)
 *   - caption bubble shows the spoken text
 *
 * The RENDERER is a plug. pngRenderer drives the two-image mouth flap.
 * When the Live2D model arrives, implement the same three methods
 * (setTalking / setMouthShape / setLevel) against the Cubism runtime
 * and swap it in — nothing upstream changes.
 */

(function () {
  "use strict";

  // ------------------------------------------------------------------
  // Debug beacons: POSTed to the server log so headless clients (the
  // OBS browser source) can be diagnosed remotely.
  // ------------------------------------------------------------------
  const IS_OBS = /OBS\//.test(navigator.userAgent);

  function report(info) {
    try {
      fetch("/debug", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({ obs: IS_OBS }, info)),
      }).catch(() => {});
    } catch {
      /* ignore */
    }
  }

  report({ event: "page-load", ua: navigator.userAgent.slice(-60) });

  // ------------------------------------------------------------------
  // Renderer: PNG mouth flap
  // ------------------------------------------------------------------
  // Rhubarb shapes: A (closed, p/b/m), B (slightly open), C (open),
  // D (wide), E (rounded), F ("oo"), G (f/v), H (l), X (idle).
  // Two-image mapping: closed for A/X, open for the rest.
  const CLOSED_SHAPES = new Set(["A", "X"]);

  const stageEl = document.getElementById("stage");
  const avatarEl = document.getElementById("avatar");
  const closedImg = document.getElementById("mouth-closed");
  const openImg = document.getElementById("mouth-open");
  const placeholderEl = document.getElementById("placeholder");

  function makePngRenderer() {
    let artLoaded = false;

    function show(img) {
      closedImg.classList.toggle("visible", img === closedImg);
      openImg.classList.toggle("visible", img === openImg);
    }

    // Fall back to the placeholder derpicorn until real art is dropped in.
    const loaded = new Set();
    function checkArt(img) {
      loaded.add(img);
      if (loaded.size < 2) return;
      artLoaded = true;
      placeholderEl.classList.add("hidden");
      show(closedImg);
      report({ event: "art-loaded" });
    }
    for (const img of [closedImg, openImg]) {
      img.addEventListener("load", () => checkArt(img));
      img.addEventListener("error", () => {
        placeholderEl.classList.remove("hidden");
        report({ event: "art-error", img: img.id });
      });
      if (img.complete && img.naturalWidth > 0) checkArt(img);
    }

    return {
      setTalking(talking) {
        avatarEl.classList.toggle("talking", talking);
        avatarEl.classList.toggle("idle", !talking);
        if (!talking) {
          avatarEl.style.rotate = "0deg";
          if (artLoaded) show(closedImg);
          else placeholderEl.style.transform = "";
        }
      },

      // Cue-driven path (Rhubarb)
      setMouthShape(shape) {
        const open = !CLOSED_SHAPES.has(shape);
        if (artLoaded) {
          show(open ? openImg : closedImg);
        } else {
          placeholderEl.style.transform = open ? "scale(1.08)" : "";
        }
      },

      // Amplitude-driven fallback path: level in [0, 1]
      setLevel(level) {
        this.setMouthShape(level > 0.06 ? "C" : "X");
        // A touch of loudness-based lean, capped at ~10deg
        avatarEl.style.rotate = `${Math.min(level * 40, 10)}deg`;
      },
    };
  }

  const renderer = makePngRenderer();

  // ------------------------------------------------------------------
  // Position
  // ------------------------------------------------------------------
  function applyPosition(payload) {
    if (typeof payload.x === "number" && typeof payload.y === "number") {
      stageEl.style.setProperty("--dabi-x", `${payload.x}px`);
      stageEl.style.setProperty("--dabi-y", `${payload.y}px`);
      stageEl.classList.add("positioned");
    } else {
      stageEl.classList.remove("positioned");
    }
  }

  // ------------------------------------------------------------------
  // Caption
  // ------------------------------------------------------------------
  const captionEl = document.getElementById("caption");
  let captionTimer = null;

  function showCaption(text) {
    clearTimeout(captionTimer);
    captionEl.textContent = text;
    captionEl.classList.remove("hidden");
  }

  function hideCaptionSoon() {
    clearTimeout(captionTimer);
    captionTimer = setTimeout(() => captionEl.classList.add("hidden"), 1200);
  }

  // ------------------------------------------------------------------
  // Playback queue
  // ------------------------------------------------------------------
  const queue = [];
  let playing = false;

  const audioGate = document.getElementById("audio-gate");
  let audioCtx = null;

  function ensureAudioCtx() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    return audioCtx;
  }

  function enqueue(payload) {
    queue.push(payload);
    if (!playing) playNext();
  }

  function playNext() {
    const payload = queue.shift();
    if (!payload) {
      playing = false;
      return;
    }
    playing = true;
    play(payload).catch((err) => {
      console.error("playback failed:", err);
      renderer.setTalking(false);
      hideCaptionSoon();
      playNext();
    });
  }

  async function play(payload) {
    const audio = new Audio(payload.audio_url);
    audio.crossOrigin = "anonymous";

    const cues = Array.isArray(payload.mouth_cues) && payload.mouth_cues.length
      ? payload.mouth_cues
      : null;

    // Amplitude fallback needs the audio routed through an analyser.
    let analyser = null;
    let levelBuf = null;
    if (!cues) {
      const ctx = ensureAudioCtx();
      const src = ctx.createMediaElementSource(audio);
      analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      levelBuf = new Uint8Array(analyser.frequencyBinCount);
      src.connect(analyser);
      analyser.connect(ctx.destination);
    }

    showCaption(payload.text);
    renderer.setTalking(true);

    let cueIdx = 0;
    let rafId = 0;

    function tick() {
      const t = audio.currentTime;
      if (cues) {
        while (cueIdx < cues.length && cues[cueIdx].end <= t) cueIdx += 1;
        const cue = cues[cueIdx];
        renderer.setMouthShape(cue && cue.start <= t ? cue.value : "X");
      } else if (analyser) {
        analyser.getByteTimeDomainData(levelBuf);
        let peak = 0;
        for (let i = 0; i < levelBuf.length; i++) {
          peak = Math.max(peak, Math.abs(levelBuf[i] - 128) / 128);
        }
        renderer.setLevel(peak);
      }
      rafId = requestAnimationFrame(tick);
    }

    await new Promise((resolve) => {
      audio.addEventListener("ended", resolve, { once: true });
      audio.addEventListener("error", () => {
        report({ event: "audio-error", id: payload.id,
                 code: audio.error && audio.error.code });
        resolve();
      }, { once: true });

      function started() {
        if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
        audioGate.classList.add("hidden");
        rafId = requestAnimationFrame(tick);
      }

      audio.play().then(() => {
        report({ event: "play-ok", id: payload.id });
        started();
      }).catch((err) => {
        // Autoplay blocked. In a normal browser one click fixes it; in OBS
        // nobody can click, so retry a few times then skip rather than
        // wedging the queue forever.
        report({ event: "play-blocked", id: payload.id, err: String(err) });
        audioGate.classList.remove("hidden");

        let tries = 0;
        const retry = setInterval(() => {
          tries += 1;
          audio.play().then(() => {
            clearInterval(retry);
            report({ event: "play-ok-after-retry", id: payload.id, tries });
            started();
          }).catch(() => {
            if (tries >= 5) {
              clearInterval(retry);
              report({ event: "play-gave-up", id: payload.id });
              resolve();
            }
          });
        }, 3000);

        document.addEventListener("click", () => {
          if (audioCtx) audioCtx.resume();
          audio.play().then(() => {
            clearInterval(retry);
            report({ event: "play-ok-after-click", id: payload.id });
            started();
          }).catch(resolve);
        }, { once: true });
      });
    });

    cancelAnimationFrame(rafId);
    renderer.setTalking(false);
    hideCaptionSoon();
    playNext();
  }

  // ------------------------------------------------------------------
  // WebSocket with auto-reconnect
  // ------------------------------------------------------------------
  function wsURL() {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${window.location.host}/ws/voice`;
  }

  function connect() {
    const ws = new WebSocket(wsURL());

    ws.addEventListener("open", () => {
      console.log("dabi-voice: connected");
    });

    ws.addEventListener("message", (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      if (payload.type === "dabi.speak" && payload.audio_url) {
        enqueue(payload);
      } else if (payload.type === "dabi.position") {
        applyPosition(payload);
      }
    });

    ws.addEventListener("close", () => {
      console.log("dabi-voice: disconnected, retrying in 3s");
      setTimeout(connect, 3000);
    });

    ws.addEventListener("error", () => ws.close());
  }

  connect();
})();
