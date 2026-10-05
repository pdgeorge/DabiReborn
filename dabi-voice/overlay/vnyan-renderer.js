/*
 * VNyan renderer plug for the Dabi voice overlay.
 *
 * makeVNyanRenderer(opts) -> renderer
 *   opts.url     VNyan WebSocket receiver (default ws://127.0.0.1:8000/vnyan)
 *   opts.mode    "binary" (default) or "shapes"
 *   opts.blink   false to leave the eyes alone (default true)
 *   opts.blinkGap  [min, max] seconds between blinks (default [30, 60])
 *   opts.report  debug beacon fn (optional)
 *
 * Implements the same three methods as the PNG / Live2D renderers
 * (setTalking / setMouthShape / setLevel), but draws nothing: it sends
 * plain-text frames to VNyan, where WebSocket Command nodes pick them up.
 *
 * The overlay runs in the OBS browser source on the streaming PC, so
 * 127.0.0.1 is that PC — VNyan must run on the same machine as OBS.
 *
 * Messages (only sent when the mouth changes):
 *   binary:  DabiOpen / DabiClose — plain triggers, no value to convert,
 *            so the VNyan side is two WebSocket Command nodes each feeding
 *            a Set Parameter with a typed-in 1 / 0.
 *   shapes:  DabiMouth <0-100> — openness per Rhubarb shape (Live2D table).
 *            Whole numbers: the WebSocket Command node's Value output is an
 *            integer "number", not a decimal.
 *   DabiTalkStart   a line started playing
 *   DabiTalkStop    a line finished (always followed by a closed mouth)
 *   DabiEyeL <0-100> / DabiEyeR <0-100>
 *                   idle blinking, every 30-60 s whether talking or not,
 *                   eased ~60 fps (0 = open, 100 = shut). Deliberately
 *                   derpy: one eye leads, the other lags behind by a
 *                   beat, and now and then he double-blinks.
 *
 * If VNyan isn't running the renderer keeps retrying quietly every 3 s;
 * audio and captions are unaffected.
 */

"use strict";

function makeVNyanRenderer(opts) {
  const url = opts.url || "ws://127.0.0.1:8000/vnyan";
  const mode = opts.mode === "shapes" ? "shapes" : "binary";
  const report = opts.report || function () {};
  const blink = opts.blink !== false;
  const blinkGap = opts.blinkGap || [30, 60];

  // Same closed set as the PNG flap.
  const CLOSED_SHAPES = new Set(["A", "X"]);

  // Openness per Rhubarb shape (0-100) — mirrors ParamMouthOpenY in
  // live2d-renderer.js.
  const OPENNESS = {
    X: 0, A: 0, B: 50, C: 85, D: 100, E: 70, F: 50, G: 35, H: 65,
  };

  let ws = null;
  let connected = false;
  let warnedUnreachable = false;
  let mouth = 0;        // current value VNyan should show
  let lastSent = null;  // last mouth value actually sent

  function send(text) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(text);
      return true;
    }
    return false;
  }

  function sendMouth(force) {
    if (!force && mouth === lastSent) return;
    const text = mode === "binary"
      ? (mouth ? "DabiOpen" : "DabiClose")
      : `DabiMouth ${mouth}`;
    if (send(text)) lastSent = mouth;
  }

  function setMouth(value) {
    mouth = value;
    sendMouth(false);
  }

  function connect() {
    try {
      ws = new WebSocket(url);
    } catch (err) {
      report({ event: "vnyan-bad-url", url, err: String(err) });
      return;
    }

    ws.addEventListener("open", () => {
      connected = true;
      warnedUnreachable = false;
      report({ event: "vnyan-connected", url, mode });
      sendMouth(true); // resync after (re)connect
      if (blink) {
        sendEye("L", true);
        sendEye("R", true);
      }
    });

    ws.addEventListener("close", () => {
      // Only beacon the transition, not every 3 s retry.
      if (connected) {
        report({ event: "vnyan-disconnected", url });
      } else if (!warnedUnreachable) {
        warnedUnreachable = true;
        report({ event: "vnyan-unreachable", url });
      }
      connected = false;
      lastSent = null;
      setTimeout(connect, 3000);
    });

    ws.addEventListener("error", () => ws.close());
  }

  // ------------------------------------------------------------------
  // Derpy blink, animated: the blendshape is a gradient (0.5 = sleepy
  // squint), so each eye is eased shut and back open frame by frame
  // instead of snapped. A random lead eye shuts first, waits shut for
  // the lagging eye (~200 ms behind) to catch up, then still opens first,
  // with the other 100 ms behind. 15% chance of an immediate second blink.
  // Tuned by eye in VNyan: a 200 ms closing lag reads as derpy (300 looks
  // broken, 100 is too subtle); a 100 ms reopening lag beat 200.
  //
  // Values go out as whole numbers 0-100 (the WebSocket Command node's
  // Value is an integer); the graph divides by 100 for the 0-1 param.
  // ------------------------------------------------------------------
  const rand = (lo, hi) => lo + Math.random() * (hi - lo);
  const BLINK_FPS = 60;
  const CLOSE_MS = 150;        // each eye, open -> shut
  const OPEN_MS = 230;         // each eye, shut -> open (slower than shutting)
  const LAG_MS = [180, 220];   // second eye starts closing this far behind
  const SETTLE_MS = 20;        // both shut for a beat before the lead opens
  const REOPEN_LAG_MS = 100;   // second eye opens this far behind the lead
  // Lead eye total: lag + CLOSE + SETTLE + OPEN, about 600 ms.

  const eyes = {
    L: { value: 0, sent: null, anim: null },
    R: { value: 0, sent: null, anim: null },
  };
  let blinkTicker = null;

  // Smoothstep: eases in and out, so the lid accelerates then settles.
  const ease = (x) => x * x * (3 - 2 * x);

  // anim: { start, openAt } in performance.now() ms
  function eyeAt(anim, now) {
    if (now < anim.start) return 0;
    if (now < anim.start + CLOSE_MS) return ease((now - anim.start) / CLOSE_MS);
    if (now < anim.openAt) return 1;
    if (now < anim.openAt + OPEN_MS) return 1 - ease((now - anim.openAt) / OPEN_MS);
    return null; // finished
  }

  function sendEye(side, force) {
    const eye = eyes[side];
    const v = Math.round(eye.value * 100);
    if (!force && v === eye.sent) return;
    if (send(`DabiEye${side} ${v}`)) eye.sent = v;
  }

  function tickBlink() {
    const now = performance.now();
    let active = false;
    for (const side of ["L", "R"]) {
      const eye = eyes[side];
      if (!eye.anim) continue;
      const v = eyeAt(eye.anim, now);
      if (v === null) {
        eye.anim = null;
        eye.value = 0;
      } else {
        eye.value = v;
        active = true;
      }
      sendEye(side, false);
    }
    if (!active) {
      clearInterval(blinkTicker);
      blinkTicker = null;
    }
  }

  function blinkOnce() {
    const [lead, lag] = Math.random() < 0.5 ? ["L", "R"] : ["R", "L"];
    const now = performance.now();
    const delay = rand(...LAG_MS);
    // The lead waits shut until the lagging eye is shut too.
    const leadOpen = now + delay + CLOSE_MS + SETTLE_MS;
    eyes[lead].anim = { start: now, openAt: leadOpen };
    eyes[lag].anim = { start: now + delay, openAt: leadOpen + REOPEN_LAG_MS };
    const end = leadOpen + REOPEN_LAG_MS + OPEN_MS - now;
    if (!blinkTicker) blinkTicker = setInterval(tickBlink, 1000 / BLINK_FPS);
    return end; // ms until both eyes are open again
  }

  function scheduleBlink() {
    setTimeout(() => {
      const done = blinkOnce();
      if (Math.random() < 0.15) setTimeout(blinkOnce, done + 120);
      scheduleBlink();
    }, rand(blinkGap[0], blinkGap[1]) * 1000);
  }

  // Page closing or reloading (e.g. OBS refreshing the source) mid-blink
  // or mid-word would otherwise leave VNyan holding a half-shut eye or an
  // open mouth until the next connect.
  window.addEventListener("pagehide", () => {
    mouth = 0;
    sendMouth(true);
    if (blink) {
      for (const side of ["L", "R"]) {
        eyes[side].value = 0;
        sendEye(side, true);
      }
    }
  });

  connect();
  if (blink) scheduleBlink();

  return {
    setTalking(talking) {
      send(talking ? "DabiTalkStart" : "DabiTalkStop");
      if (!talking) {
        mouth = 0;
        sendMouth(true); // never leave the mouth stuck open
      }
    },

    // Cue-driven path (Rhubarb)
    setMouthShape(shape) {
      if (mode === "binary") {
        setMouth(CLOSED_SHAPES.has(shape) ? 0 : 1);
      } else {
        setMouth(OPENNESS[shape] ?? 0);
      }
    },

    // Amplitude-driven fallback path: level in [0, 1]
    setLevel(level) {
      if (mode === "binary") {
        setMouth(level > 0.06 ? 1 : 0);
      } else {
        // Steps of 10, so the stream of messages stays small.
        setMouth(Math.round(Math.min(1, level * 3) * 10) * 10);
      }
    },
  };
}
