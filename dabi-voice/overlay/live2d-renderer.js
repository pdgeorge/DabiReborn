/*
 * Live2D renderer plug for the Dabi voice overlay.
 *
 * makeLive2DRenderer(opts) -> Promise<renderer|null>
 *   opts.modelUrl   .model3.json URL
 *   opts.container  element to mount the canvas in (#avatar)
 *   opts.report     debug beacon fn (optional)
 *
 * Implements the same three methods as pngRenderer (setTalking /
 * setMouthShape / setLevel) so overlay.js can swap it in blind.
 * Resolves null / rejects if WebGL or the model is unavailable —
 * caller keeps the PNG flap in that case.
 *
 * Runtime: live2dcubismcore + PixiJS 6 + pixi-live2d-display 0.4
 * (all vendored in assets/vendor/ — the OBS browser source has no CDN).
 *
 * How the parameters are driven: we replace the (unused) motionManager
 * update hook, which the internal model calls at the exact point in each
 * frame where a Live2D motion would be applied: after loadParameters,
 * before saveParameters / breathing / physics. Mouth, eyes and body-bob
 * written there behave exactly like an authored motion, and the library's
 * built-in breathing (gentle head sway ±15°/8°/10°) plus the model's own
 * physics (hx/hy/bbx/bby wobble, tail) layer on top for free.
 */

"use strict";

async function makeLive2DRenderer(opts) {
  const report = opts.report || function () {};

  if (!(window.PIXI && PIXI.live2d && window.Live2DCubismCore)) {
    report({ event: "live2d-missing-libs" });
    return null;
  }

  const container = opts.container;
  const canvas = document.createElement("canvas");
  canvas.id = "live2d-canvas";
  container.appendChild(canvas);

  let app;
  try {
    app = new PIXI.Application({
      view: canvas,
      backgroundAlpha: 0,
      autoStart: true,
      width: container.clientWidth || 400,
      height: container.clientHeight || 400,
      antialias: true,
    });
  } catch (err) {
    // No WebGL (e.g. OBS with GPU disabled) — bail to PNG.
    canvas.remove();
    report({ event: "live2d-no-webgl", err: String(err) });
    return null;
  }

  let model;
  try {
    model = await PIXI.live2d.Live2DModel.from(opts.modelUrl, {
      autoInteract: false,
      motionPreload: "NONE",
    });
  } catch (err) {
    app.destroy(true, { children: true });
    report({ event: "live2d-model-failed", err: String(err) });
    throw err;
  }

  app.stage.addChild(model);

  // Fit: feet at the bottom of the box, centered, like object-fit: contain.
  const iw = model.internalModel.originalWidth;
  const ih = model.internalModel.originalHeight;
  const w = app.renderer.width;
  const h = app.renderer.height;
  model.anchor.set(0.5, 1);
  model.scale.set(Math.min(w / iw, h / ih));
  model.position.set(w / 2, h);

  // ------------------------------------------------------------------
  // Animation state (all applied per-frame in the motion hook below)
  // ------------------------------------------------------------------
  let talking = false;

  // Mouth: smoothed approach toward a target per Rhubarb cue.
  // shape -> [ParamMouthOpenY target, ParamMouthForm target]
  // Targets are exaggerated past realistic on purpose — cartoon mouths
  // commit. Subtlety here reads as mumbling from stream distance.
  const SHAPES = {
    X: [0.0, 0.0],   // idle
    A: [0.0, 0.0],   // p/b/m — closed
    B: [0.5, 0.0],   // slightly open
    C: [0.85, 0.0],  // open "eh"
    D: [1.0, 0.15],  // wide "ah"
    E: [0.7, -0.35], // rounded "oh"
    F: [0.5, -0.7],  // "oo"
    G: [0.35, 0.0],  // f/v
    H: [0.65, 0.0],  // l
  };
  let mouthTarget = 0.0;
  let formTarget = 0.0;
  let mouthNow = 0.0;
  let formNow = 0.0;

  // Blink: quick 240 ms close-open every 2–6 s.
  const BLINK_MS = 240;
  let nextBlink = performance.now() + 2000;

  function eyeOpenness(now) {
    if (now < nextBlink) return 1.0;
    const t = now - nextBlink;
    if (t >= BLINK_MS) {
      nextBlink = now + 2000 + Math.random() * 4000;
      return 1.0;
    }
    // triangle: open -> closed -> open
    return Math.abs(t / (BLINK_MS / 2) - 1);
  }

  // Body bob: same curve the OpenVT idle used — 1.5 s period, livelier
  // while talking. Physics doesn't touch ParamBodyAngleY, so it's ours.
  const BOB_PERIOD_MS = 1500;

  const coreIds = {
    mouthOpen: "ParamMouthOpenY",
    mouthForm: "ParamMouthForm",
    eyeL: "ParamEyeLOpen",
    eyeR: "ParamEyeROpen",
    bodyY: "ParamBodyAngleY",
    headY: "ParamAngleY",
  };

  let lastNow = performance.now();

  const motionManager = model.internalModel.motionManager;
  motionManager.update = function (coreModel, _now) {
    const now = performance.now();
    const dt = Math.min((now - lastNow) / 1000, 0.1);
    lastNow = now;

    // restore the motion-layer base, exactly like a real motion would
    coreModel.loadParameters();

    // mouth: asymmetric attack — snap open (~1 frame), relax closed.
    // The old symmetric 25/s smoothing read as mumbling; the PNG flap
    // was punchy because it was instant. This keeps the snap and only
    // smooths the release, like a jaw actually closing.
    const openRate = 1 - Math.exp(-70 * dt);
    const closeRate = 1 - Math.exp(-18 * dt);
    mouthNow += (mouthTarget - mouthNow) * (mouthTarget > mouthNow ? openRate : closeRate);
    formNow += (formTarget - formNow) * openRate;
    coreModel.setParameterValueById(coreIds.mouthOpen, mouthNow);
    coreModel.setParameterValueById(coreIds.mouthForm, formNow);

    // jaw→head coupling: the whole head tips up as the mouth opens —
    // the classic 90s yap. Gain is pre-doubled because the library's
    // breathing pass blends angle params at 50% weight afterward.
    coreModel.setParameterValueById(coreIds.headY, mouthNow * 7.0);

    // blink
    const open = eyeOpenness(now);
    coreModel.setParameterValueById(coreIds.eyeL, open);
    coreModel.setParameterValueById(coreIds.eyeR, open);

    // body bob
    const amp = talking ? 4.0 : 2.0;
    const bob = amp * 0.5 * (1 - Math.cos((2 * Math.PI * now) / BOB_PERIOD_MS));
    coreModel.setParameterValueById(coreIds.bodyY, bob);

    return true; // "a motion is active" — keeps the update cycle honest
  };

  report({ event: "live2d-loaded", w: iw, h: ih });

  return {
    setTalking(isTalking) {
      talking = isTalking;
      if (!isTalking) {
        mouthTarget = 0.0;
        formTarget = 0.0;
      }
    },

    // Cue-driven path (Rhubarb)
    setMouthShape(shape) {
      const s = SHAPES[shape] || SHAPES.X;
      mouthTarget = s[0];
      formTarget = s[1];
    },

    // Amplitude-driven fallback path: level in [0, 1]
    setLevel(level) {
      mouthTarget = Math.min(1, level * 3);
      formTarget = 0.0;
    },
  };
}
