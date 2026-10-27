/*
 * 3D renderer plug for the Dabi voice overlay: ?renderer=3d.
 *
 * Draws Dabi's 3D body (the Blender model from vtuber-george, exported to
 * assets/dabi3d/dabi.glb by its blender/dabi_export_glb.py) with three.js,
 * on a see-through canvas the size of the page. The OBS browser source stays
 * one fullscreen source, so he can walk anywhere on screen and the same
 * source can be shared by every scene.
 *
 * The look copies his Poiyomi materials in Unity (unity/dabi_prop/Assets/
 * Dabi/MaterialsPoiyomi): flat colours with a soft two-tone shadow, edges
 * that darken as they turn away, black ink outlines; the face is flat ink.
 * His ears, horn, mane and tail flop with the same settings as Unity's
 * VRMSpringBones, and his walk is the Unity walker's: turn on the spot,
 * waddle over, ease to a stop, turn back to face the camera, squeaky shoes.
 *
 * makeDabi3DRenderer(opts) -> Promise<renderer | null>
 *   opts.container  element the canvas goes in (#stage)
 *   opts.params     the page's URLSearchParams:
 *                     size=360     his height standing, in px
 *                     tilt=15      degrees the camera looks down at him
 *                     speed=1.75   walking speed (1 = the Walk clip's own pace)
 *                     ink=<px>     outline width (default: Unity's, never under 1.25 px)
 *                     squeak=0.5   squeaky shoe volume (0 = off)
 *                     blinkgap=a-b seconds between blinks (default 30-60, like VNyan)
 *                   and for reference pictures, still=1 with clip, t, yaw,
 *                   tilt, mouth, blink, blinkL, blinkR, center, view (see still()).
 *   opts.report     debug beacon fn (optional)
 *   opts.onAnchor   (x, y) => void: the page point just above his horn, so the
 *                   caption bubble can follow him (optional)
 * Resolves null if WebGL or the model is unavailable.
 *
 * The renderer has the usual setTalking / setMouthShape / setLevel, plus
 *   moveTo({x, y, seat, walk}) - sit with his rump on page point (x, y). seat is
 *   how far that is above the floor in px (0 = on the floor; Dabi's shop sends
 *   210 for its stool): he walks on the floor to beside it and hops up. walk
 *   false puts him there at once (a fresh page). x/y null: his default spot.
 */

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const MODEL_URL = "/assets/dabi3d/dabi.glb";
const SQUEAKS = ["1a", "1b", "2a", "2b", "5a", "5b"].map((n) => `/assets/dabi3d/squeaks/squeak_${n}.wav`);

// Poiyomi materials: colour (sRGB, like Unity's), shadow tone, darkening rim, ink outline. null shade = flat.
const MATERIALS = {
  Dabi_Body: { color: [1, 0.97, 0.93], shade: 0.86, rim: true, ink: true },
  Dabi_Pink: { color: [1, 0.72, 0.84], shade: 0.86, rim: true, ink: true },
  Dabi_Hair: { color: [0.035, 0.027, 0.027], shade: 0.8, rim: true, ink: true },
  Dabi_HairRed: { color: [0.85, 0.04, 0.03], shade: 0.8, rim: true, ink: true },
  Dabi_Horn: { color: [1, 0.68, 0.08], shade: 0.82, rim: true, ink: true },
  Dabi_Belly: { color: [1, 0.72, 0.84], shade: 0.86, rim: false, ink: false },
  Dabi_Line: { color: [0.02, 0.015, 0.015], shade: null },
  Dabi_EyeWhite: { color: [1, 1, 1], shade: null },
};
// Poiyomi Toon 10's Multilayer Math lighting and Poiyomi-style rim, with Dabi's values:
const SHADOW_EDGE = [0.3, 0.6];          // _ShadowBorder 0.45 -+ _ShadowBlur 0.3 / 2, on the half-Lambert term
const SHADOW_BORDER_EDGE = 0.22;         // ... minus _ShadowBorderRange 0.08: where the warm border tint starts
const SHADOW_BORDER_TINT = [1, 0.1, 0];  // _ShadowBorderColor: lifts red (and a little green) where shadow meets light
const RIM_COLOR = [0.35, 0.32, 0.36];    // _RimLightColor, multiplied into the colour (_RimPoiBlendMode 2)
const RIM_WIDTH = -0.05 + 1.05 * 0.3;    // lerp(-0.05, 1, _RimWidth 0.3); with _RimBlur 0.35 the ramp runs from N.V 0 to here
const INK_WORLD = 0.0035;                // _LineWidth 0.35 cm
// Unity's walker light, Euler(40, 160, 0), as the direction toward it: above, in front, from his left (+X here)
const LIGHT = new THREE.Vector3(0.262, 0.643, 0.72).normalize();

// spring chains: roots -> stiffness, gravity, drag (DabiSetup.Springs; gravity points down)
const SPRINGS = [
  { roots: ["ear.L.1", "ear.R.1"], stiffness: 0.9, gravity: 0.05, drag: 0.35 },
  { roots: ["mane.1"], stiffness: 0.6, gravity: 0.1, drag: 0.4 },
  { roots: ["tail.1"], stiffness: 0.5, gravity: 0.15, drag: 0.3 },
  { roots: ["horn.1"], stiffness: 3.0, gravity: 0.0, drag: 0.6 },
];
const STEP = 1 / 60;                     // he's simulated in fixed steps, so a slow OBS frame rate doesn't fling his hair
const INK_PUSH = 0.008;                  // m the ink hull sits back, so it never shows through his face or thin ears

const WALK_SPEED = 0.404;                // m/s at clip speed 1 (dabi_anims.py WALK_SPEED)
const TURN_SPEED = THREE.MathUtils.degToRad(300);
const SIT_TIME = 0.55;                   // s to sit down or get up
const HOP = 0.18;                        // m a hop rises above the straight line
const SEAT_SIDE = 0.42;                  // m beside a raised seat where he stands before hopping on

// openness per Rhubarb shape, like the Live2D and VNyan renderers
const OPENNESS = { X: 0, A: 0, B: 0.5, C: 0.85, D: 1, E: 0.7, F: 0.5, G: 0.35, H: 0.65 };

const VERTEX = /* glsl */ `
#include <common>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
uniform float uInk;
uniform float uInkPush;
varying vec3 vNormal;
void main() {
  #include <beginnormal_vertex>
  #include <morphnormal_vertex>
  #include <skinbase_vertex>
  #include <skinnormal_vertex>
  #include <defaultnormal_vertex>
  #include <begin_vertex>
  #include <morphtarget_vertex>
  #include <skinning_vertex>
  #include <project_vertex>
  vNormal = normalize(transformedNormal);
  if (uInk > 0.0) {                      // ink: the back of a slightly fatter copy (inverted hull, like Poiyomi)
    vec3 n = vNormal;
    #ifdef FLIP_SIDED
    n = -n;
    #endif
    mvPosition.xyz += n * uInk;
    mvPosition.z -= uInkPush;
    gl_Position = projectionMatrix * mvPosition;
  }
}`;

const TOON_FRAGMENT = /* glsl */ `
uniform vec3 uColor;
uniform vec3 uShadow;
uniform vec3 uLight;
uniform float uShaded;
uniform float uRim;
uniform vec3 uRimColor;
uniform vec3 uShadowTint;
uniform vec3 uShadowEdge;                // lit from, lit to, border tint from
uniform float uRimWidth;
varying vec3 vNormal;
void main() {
  vec3 col = uColor;
  if (uShaded > 0.5) {
    vec3 n = normalize(vNormal);
    if (uRim > 0.5) {                    // orthographic: the view direction is +z in view space
      float vdn = abs(n.z);
      float rim = 1.0 - clamp(vdn / (uRimWidth + min(fwidth(vdn), uRimWidth)), 0.0, 1.0);
      col *= mix(vec3(1.0), uRimColor, rim);
    }
    float hl = clamp(dot(n, uLight) * 0.5 + 0.5, 0.0, 1.0);
    float fw = fwidth(hl);
    float lit = smoothstep(uShadowEdge.x - fw, uShadowEdge.y + fw, hl);
    float border = smoothstep(uShadowEdge.z - fw, uShadowEdge.y + fw, hl);
    vec3 shade = mix(uShadow, vec3(1.0), border * uShadowTint);
    col *= mix(shade, vec3(1.0), lit);
  }
  gl_FragColor = vec4(col, 1.0);
  #include <colorspace_fragment>
}`;

const INK_FRAGMENT = /* glsl */ `
void main() {
  gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);
}`;

const srgb = (c) => new THREE.Color().setRGB(c[0], c[1], c[2], THREE.SRGBColorSpace);
const ease = (x) => x * x * (3 - 2 * x);
const clamp01 = (x) => Math.min(1, Math.max(0, x));
const rand = (lo, hi) => lo + Math.random() * (hi - lo);
const boneName = (n) => THREE.PropertyBinding.sanitizeNodeName(n);

function angleTo(from, to) {                 // signed shortest turn, radians
  return Math.atan2(Math.sin(to - from), Math.cos(to - from));
}

function parseRange(text) {
  const m = /^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$/.exec(text || "");
  if (!m) return null;
  const lo = Number(m[1]), hi = Number(m[2]);
  return hi >= lo ? [lo, hi] : null;
}

// ---------------------------------------------------------------------------
// Materials: toon copies of the Poiyomi ones, plus an ink hull per inked mesh
// ---------------------------------------------------------------------------
function dress(model, light, inkUniform) {
  const meshes = [];
  model.traverse((o) => { if (o.isMesh) meshes.push(o); });
  for (const mesh of meshes) {
    mesh.frustumCulled = false;            // skinned bounds go stale as he moves
    const spec = MATERIALS[mesh.material.name] || MATERIALS.Dabi_Body;
    const shaded = spec.shade !== null;
    const color = srgb(spec.color);
    const shadow = shaded ? srgb(spec.color.map((v) => v * spec.shade)) : color;   // _ShadowColor: multiplies the light
    mesh.material = new THREE.ShaderMaterial({
      vertexShader: VERTEX,
      fragmentShader: TOON_FRAGMENT,
      uniforms: {
        uInk: { value: 0 },
        uInkPush: { value: 0 },
        uColor: { value: color },
        uShadow: { value: shadow },
        uLight: light,
        uShaded: { value: shaded ? 1 : 0 },
        uRim: { value: spec.rim ? 1 : 0 },
        uRimColor: { value: srgb(RIM_COLOR) },
        uShadowTint: { value: srgb(SHADOW_BORDER_TINT) },
        uShadowEdge: { value: new THREE.Vector3(...SHADOW_EDGE, SHADOW_BORDER_EDGE) },
        uRimWidth: { value: RIM_WIDTH },
      },
      // the face sits a hair above the head: keep it in front however far away he is
      polygonOffset: !shaded,
      polygonOffsetFactor: shaded ? 0 : -1,
      polygonOffsetUnits: shaded ? 0 : -4,
    });
    if (spec.ink && mesh.isSkinnedMesh) {
      const ink = new THREE.SkinnedMesh(mesh.geometry, new THREE.ShaderMaterial({
        vertexShader: VERTEX,
        fragmentShader: INK_FRAGMENT,
        uniforms: { uInk: inkUniform, uInkPush: { value: INK_PUSH } },
        side: THREE.BackSide,
      }));
      ink.name = mesh.name + "_ink";
      ink.frustumCulled = false;
      ink.position.copy(mesh.position);
      ink.quaternion.copy(mesh.quaternion);
      ink.scale.copy(mesh.scale);
      ink.bind(mesh.skeleton, mesh.bindMatrix);
      mesh.parent.add(ink);
    }
  }
}

// ---------------------------------------------------------------------------
// Spring bones: UniVRM 0.x's VRMSpringBone, chain by chain, root to tip
// ---------------------------------------------------------------------------
function makeSprings(model) {
  const joints = [];
  const _v = new THREE.Vector3(), _p = new THREE.Vector3();
  model.updateMatrixWorld(true);
  for (const chain of SPRINGS) {
    const visit = (bone) => {
      const child = bone.children.find((c) => c.isBone);
      let local;
      if (child) {
        local = child.position.clone();
      } else {                             // a tip: a pretend child 7 cm further on, like UniVRM
        bone.getWorldPosition(_v);
        bone.parent.getWorldPosition(_p);
        local = bone.worldToLocal(_v.clone().add(_v.clone().sub(_p).normalize().multiplyScalar(0.07)));
      }
      joints.push({ bone, chain, axis: local.clone().normalize(), length: local.length(), local,
                    rest: new THREE.Quaternion(), prev: new THREE.Vector3(), curr: new THREE.Vector3() });
      bone.children.filter((c) => c.isBone).forEach(visit);
    };
    for (const name of chain.roots) {
      const root = model.getObjectByName(boneName(name));
      if (root) visit(root);
    }
  }

  const pq = new THREE.Quaternion(), rw = new THREE.Quaternion(), turn = new THREE.Quaternion();
  const pos = new THREE.Vector3(), next = new THREE.Vector3(), from = new THREE.Vector3(), to = new THREE.Vector3();
  const gravity = new THREE.Vector3();

  return {
    bones: joints.map((j) => j.bone),
    // tails back where the pose puts them (a fresh page, or a jump)
    reset() {
      for (const j of joints) {
        j.bone.localToWorld(j.curr.copy(j.local));
        j.prev.copy(j.curr);
      }
    },
    // one step, after the animation has posed the bones: that pose is what the springs pull toward.
    // floorY: tips stay above it (his tail lies on the floor or seat instead of sinking through).
    step(floorY) {
      for (const j of joints) {
        const { bone, chain } = j;
        j.rest.copy(bone.quaternion);
        bone.parent.getWorldQuaternion(pq);
        rw.copy(pq).multiply(j.rest);
        bone.updateMatrixWorld(true);
        bone.getWorldPosition(pos);
        next.copy(j.curr).sub(j.prev).multiplyScalar(1 - chain.drag).add(j.curr)
          .add(from.copy(j.axis).applyQuaternion(rw).multiplyScalar(chain.stiffness * STEP))
          .add(gravity.set(0, -chain.gravity * STEP, 0));
        next.sub(pos).normalize().multiplyScalar(j.length).add(pos);
        if (next.y < floorY) {
          next.y = floorY;
          next.sub(pos).normalize().multiplyScalar(j.length).add(pos);
        }
        j.prev.copy(j.curr);
        j.curr.copy(next);
        this.aim(j);
      }
    },
    // degrees each floppy bone hangs away from where the animation puts it (for a test or the console)
    sag() {
      const out = {};
      for (const j of joints) {
        j.bone.parent.getWorldQuaternion(pq);
        rw.copy(pq).multiply(j.rest);
        j.bone.getWorldPosition(pos);
        from.copy(j.axis).applyQuaternion(rw).normalize();
        to.copy(j.curr).sub(pos).normalize();
        out[j.bone.name] = Math.round(THREE.MathUtils.radToDeg(from.angleTo(to)));
      }
      return out;
    },
    aim(j) {
      const bone = j.bone;
      bone.quaternion.copy(j.rest);
      bone.parent.getWorldQuaternion(pq);
      rw.copy(pq).multiply(j.rest);
      bone.updateMatrixWorld(true);
      bone.getWorldPosition(pos);
      from.copy(j.axis).applyQuaternion(rw).normalize();
      to.copy(j.curr).sub(pos).normalize();
      turn.setFromUnitVectors(from, to);
      bone.quaternion.copy(pq.invert().multiply(turn.multiply(rw)));
      bone.updateMatrixWorld(true);
    },
  };
}

// ---------------------------------------------------------------------------
// Face: mouth from the speech cues, derpy blinks (the VNyan renderer's timing)
// ---------------------------------------------------------------------------
function makeFace(meshes, blinkGap) {
  const CLOSE_MS = 150, OPEN_MS = 230, LAG_MS = [180, 220], SETTLE_MS = 20, REOPEN_LAG_MS = 100;
  const eyes = { L: null, R: null };       // { start, openAt } in performance.now() ms
  let mouth = 0, mouthTarget = 0;

  function eyeAt(anim, now) {
    if (!anim || now < anim.start) return 0;
    if (now < anim.start + CLOSE_MS) return ease((now - anim.start) / CLOSE_MS);
    if (now < anim.openAt) return 1;
    if (now < anim.openAt + OPEN_MS) return 1 - ease((now - anim.openAt) / OPEN_MS);
    return 0;
  }

  function blinkOnce() {
    const [lead, lag] = Math.random() < 0.5 ? ["L", "R"] : ["R", "L"];
    const now = performance.now();
    const delay = rand(...LAG_MS);
    const leadOpen = now + delay + CLOSE_MS + SETTLE_MS;
    eyes[lead] = { start: now, openAt: leadOpen };
    eyes[lag] = { start: now + delay, openAt: leadOpen + REOPEN_LAG_MS };
    return leadOpen + REOPEN_LAG_MS + OPEN_MS - now;
  }

  function scheduleBlink() {
    setTimeout(() => {
      const done = blinkOnce();
      if (Math.random() < 0.15) setTimeout(blinkOnce, done + 120);
      scheduleBlink();
    }, rand(blinkGap[0], blinkGap[1]) * 1000);
  }

  function set(key, value) {
    for (const m of meshes) {
      const i = m.morphTargetDictionary[key];
      if (i !== undefined) m.morphTargetInfluences[i] = value;
    }
  }

  return {
    start: scheduleBlink,
    set,
    get mouth() { return mouth; },
    setMouth(v) { mouthTarget = v; },
    update(dt) {
      // snappy open, a touch slower to close, so short syllables still read
      const tau = mouthTarget > mouth ? 0.035 : 0.06;
      mouth += (mouthTarget - mouth) * (1 - Math.exp(-dt / tau));
      set("M_a", mouth);
      const now = performance.now();
      set("Blink_L", eyeAt(eyes.L, now));
      set("Blink_R", eyeAt(eyes.R, now));
    },
  };
}

// ---------------------------------------------------------------------------
// Squeaky shoes: a random squeak (never the same twice running) per footfall
// ---------------------------------------------------------------------------
function makeSqueaks(volume) {
  if (!(volume > 0)) return { step() {} };
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return { step() {} };
  const ctx = new Ctx();
  const buffers = [];
  let last = -1;
  for (const url of SQUEAKS) {
    fetch(url).then((r) => r.arrayBuffer()).then((a) => ctx.decodeAudioData(a))
      .then((b) => buffers.push(b)).catch(() => {});
  }
  return {
    step(pan) {
      if (!buffers.length) return;
      if (ctx.state === "suspended") ctx.resume();
      let i = Math.floor(Math.random() * buffers.length);
      if (buffers.length > 1 && i === last) i = (i + 1) % buffers.length;
      last = i;
      const src = ctx.createBufferSource();
      src.buffer = buffers[i];
      src.playbackRate.value = 1 + rand(-0.08, 0.08);
      const gain = ctx.createGain();
      gain.gain.value = volume;
      const panner = ctx.createStereoPanner();
      panner.pan.value = Math.max(-1, Math.min(1, pan)) * 0.6;
      src.connect(gain).connect(panner).connect(ctx.destination);
      src.start();
    },
  };
}

// ---------------------------------------------------------------------------
// The renderer
// ---------------------------------------------------------------------------
export async function makeDabi3DRenderer(opts) {
  const report = opts.report || function () {};
  const params = opts.params || new URLSearchParams();
  const num = (key, fallback) => {
    const v = parseFloat(params.get(key));
    return Number.isFinite(v) ? v : fallback;
  };

  let gl;
  try {
    gl = new THREE.WebGLRenderer({ antialias: true, alpha: true, premultipliedAlpha: true });
  } catch (err) {
    report({ event: "dabi3d-no-webgl", err: String(err) });
    return null;
  }
  gl.setClearColor(0x000000, 0);
  gl.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  const canvas = gl.domElement;
  canvas.id = "dabi3d-canvas";
  opts.container.appendChild(canvas);

  let gltf;
  try {
    gltf = await new GLTFLoader().loadAsync(MODEL_URL);
  } catch (err) {
    canvas.remove();
    gl.dispose();
    report({ event: "dabi3d-model-failed", err: String(err) });
    return null;
  }

  const scene = new THREE.Scene();
  const holder = new THREE.Group();        // his spot on the floor and which way he faces
  scene.add(holder);
  const model = gltf.scene;
  holder.add(model);

  const light = { value: new THREE.Vector3() };
  const ink = { value: INK_WORLD };
  dress(model, light, ink);
  const faceMeshes = [];
  model.traverse((o) => { if (o.isMesh && o.morphTargetDictionary && "M_a" in o.morphTargetDictionary) faceMeshes.push(o); });
  const face = makeFace(faceMeshes, parseRange(params.get("blinkgap")) || [30, 60]);
  face.set("Hide", 0);

  const size = new THREE.Box3().setFromObject(model).getSize(new THREE.Vector3());
  const height = size.y;                   // standing, horn included
  const head = model.getObjectByName("head");
  const hornTip = model.getObjectByName(boneName("horn.2"));

  const mixer = new THREE.AnimationMixer(model);
  const clips = Object.fromEntries(gltf.animations.map((c) => [c.name, c]));
  const actions = {};
  for (const name of ["Idle", "Walk", "Sit"]) {
    actions[name] = mixer.clipAction(clips[name]);
    actions[name].play();
  }

  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 100);

  if (params.has("still")) {
    return still({ gl, scene, camera, mixer, actions, face, light, params, num, report });
  }

  // ---- live: page pixels <-> the floor he walks on ----
  const sizePx = num("size", 360);
  const ppm = sizePx / height;              // page px per metre
  const tilt = THREE.MathUtils.degToRad(Math.min(60, Math.max(0, num("tilt", 15))));
  const speed = Math.max(0.2, num("speed", 1.75));
  ink.value = params.has("ink") ? num("ink", 2) / ppm : Math.max(INK_WORLD, 1.25 / ppm);
  camera.position.set(0, Math.sin(tilt) * 20, Math.cos(tilt) * 20);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld();
  light.value.copy(LIGHT).transformDirection(camera.matrixWorldInverse);
  const up = new THREE.Vector3(0, Math.cos(tilt), -Math.sin(tilt));   // the camera's up: screen-up in the world
  let W = 0, H = 0;

  function resize() {
    W = window.innerWidth || 1920;
    H = window.innerHeight || 1080;
    gl.setSize(W, H, false);
    camera.left = -W / 2 / ppm; camera.right = W / 2 / ppm;
    camera.top = H / 2 / ppm; camera.bottom = -H / 2 / ppm;
    camera.updateProjectionMatrix();
  }
  resize();
  window.addEventListener("resize", resize);

  // A page point is a point on the camera's plane through the origin. Moving in it keeps him the same size;
  // walking up the screen reads as walking away.
  function toWorld(p, out) {
    return out.set((p.x - W / 2) / ppm, 0, 0).addScaledVector(up, (H / 2 - p.y) / ppm);
  }
  const tmp = new THREE.Vector3();
  function toPage(v) {
    tmp.copy(v).project(camera);
    return { x: (tmp.x + 1) / 2 * W, y: (1 - tmp.y) / 2 * H };
  }

  // where he sits: rump on (x, y), `seat` px above the floor; clamped so he stays on screen
  function spot(p) {
    const x = typeof p.x === "number" ? p.x : W - sizePx * 0.5;
    const y = typeof p.y === "number" ? p.y : H - sizePx * 0.06;
    return {
      x: Math.min(W - sizePx * 0.2, Math.max(sizePx * 0.2, x)),
      y: Math.min(H + sizePx * 0.05, Math.max(sizePx * 0.8, y)),
      seat: Math.max(0, Number(p.seat) || 0),
    };
  }

  // ---- behaviour: sit -> rise -> walk -> settle -> land -> sit ----
  const body = {
    mode: "sit",
    pos: { x: 0, y: 0 },      // page px of his root: feet standing, rump sitting
    on: null,                 // the spot he's sitting on (mode sit)
    goal: null,               // the spot he's heading for
    yaw: 0,                   // 0 faces the camera
    sitW: 1,                  // Sit clip weight
    walk: 0,                  // Idle -> Walk blend
    hop: null,                // { from, to, t, sitFrom, sitTo, arc, spot }
  };
  let springsNeedReset = true;

  function place(s) {
    body.mode = "sit";
    body.on = body.goal = s;
    body.pos = { x: s.x, y: s.y };
    body.yaw = 0; body.sitW = 1; body.walk = 0; body.hop = null;
    springsNeedReset = true;
  }

  function startHop(to, sitTo, arc, spot = null) {
    body.hop = { from: { ...body.pos }, to, t: 0, sitFrom: body.sitW, sitTo, arc, spot };
  }

  // where to stand to hop onto a raised spot: beside it, on the side he's coming from
  function beside(s, fromX) {
    const side = fromX >= s.x ? 1 : -1;
    return { x: s.x + side * SEAT_SIDE * ppm, y: s.y + s.seat };
  }

  function rise() {
    const on = body.on;
    body.mode = "rise";
    body.on = null;
    const to = on && on.seat > 0 ? beside(on, body.goal.x) : { ...body.pos };
    startHop(to, 0, on && on.seat > 0 ? HOP : 0);
  }

  const footfalls = [0.25, 0.75];          // where each foot lands in the Walk clip (DabiFbxImport's FootDown)
  let lastWalkPhase = 0, footsteps = 0;
  const squeaks = makeSqueaks(num("squeak", 0.5));

  function behave(dt) {
    const b = body;
    if (b.mode === "sit" && b.goal && b.goal !== b.on) rise();

    if (b.hop) {
      const h = b.hop;
      h.t = Math.min(1, h.t + dt / SIT_TIME);
      const k = ease(h.t);
      b.pos.x = h.from.x + (h.to.x - h.from.x) * k;
      b.pos.y = h.from.y + (h.to.y - h.from.y) * k - Math.sin(Math.PI * h.t) * h.arc * ppm;
      b.sitW = h.sitFrom + (h.sitTo - h.sitFrom) * k;
      b.walk = Math.max(0, b.walk - dt * 4);
      b.yaw += angleTo(b.yaw, 0) * Math.min(1, dt * 10);
      if (h.t >= 1) {
        b.hop = null;
        if (b.mode === "rise") b.mode = "walk";
        else if (b.mode === "land") { b.mode = "sit"; b.on = h.spot; }   // a new goal since then gets him up again
      }
      return;
    }

    let want = 0;
    let faceYaw = 0;
    if (b.mode === "walk") {
      const target = b.goal.seat > 0 ? beside(b.goal, b.pos.x) : b.goal;
      const dx = target.x - b.pos.x, dy = target.y - b.pos.y;
      const dist = Math.hypot(dx, dy);
      if (dist < 1.5) {
        b.mode = "settle";
      } else {
        faceYaw = Math.atan2(dx, dy);     // 0 = down the screen, toward the camera
        const off = Math.abs(angleTo(b.yaw, faceYaw));
        // turn on the spot first (shuffling), then walk; ease in over the last few centimetres
        want = off > Math.PI / 3 ? 0.35 : clamp01(dist / (0.12 * ppm));
        if (off < Math.PI / 3) {
          const step = Math.min(WALK_SPEED * speed * ppm * b.walk * dt, dist) * Math.cos(off);
          b.pos.x += Math.sin(b.yaw) * step;
          b.pos.y += Math.cos(b.yaw) * step;
        }
      }
    }
    if (b.mode !== "walk") {
      faceYaw = 0;
      if (Math.abs(angleTo(b.yaw, 0)) > THREE.MathUtils.degToRad(5)) want = 0.3;   // shuffle round to face us
      else if (b.mode === "settle") {
        b.mode = "land";
        startHop({ x: b.goal.x, y: b.goal.y }, 1, b.goal.seat > 0 ? HOP : 0, b.goal);
      }
    }
    const turn = angleTo(b.yaw, faceYaw);
    b.yaw += Math.sign(turn) * Math.min(Math.abs(turn), TURN_SPEED * dt);
    b.walk += Math.sign(want - b.walk) * Math.min(Math.abs(want - b.walk), dt * 4);
  }

  // ---- the loop ----
  const nod = new THREE.Quaternion(), headParent = new THREE.Quaternion(), axisX = new THREE.Vector3(1, 0, 0);
  const anchorWorld = new THREE.Vector3();
  let talking = false, lastAnchor = null, last = performance.now();
  face.start();

  let acc = 0;
  function frame(now) {
    acc = Math.min(acc + Math.max(0, (now - last) / 1000), STEP * 8);   // after a stall, catch up at most 8 steps
    last = now;
    while (acc >= STEP) {
      simulate(STEP);
      acc -= STEP;
    }
    gl.render(scene, camera);

    if (opts.onAnchor && hornTip) {
      hornTip.getWorldPosition(anchorWorld);
      const a = toPage(anchorWorld);
      if (!lastAnchor || Math.abs(a.x - lastAnchor.x) > 0.5 || Math.abs(a.y - lastAnchor.y) > 0.5) {
        lastAnchor = a;
        opts.onAnchor(a.x, a.y - 0.04 * ppm);
      }
    }
    requestAnimationFrame(frame);
  }

  function simulate(dt) {
    behave(dt);

    const standW = 1 - body.sitW;
    actions.Sit.setEffectiveWeight(body.sitW);
    actions.Idle.setEffectiveWeight(standW * (1 - body.walk));
    actions.Walk.setEffectiveWeight(standW * body.walk);
    actions.Walk.timeScale = speed;
    // The mixer only writes a bone when its animated value changes, so put back what it last wrote before the
    // springs and the nod moved these bones; otherwise they'd keep their own last result as the pose to aim for.
    for (const [bone, q] of animated) bone.quaternion.copy(q);
    mixer.update(dt);
    for (const [bone, q] of animated) q.copy(bone.quaternion);

    // squeak on each footfall while he's really walking
    const clipLen = clips.Walk.duration;
    const phase = (actions.Walk.time % clipLen) / clipLen;
    if (standW * body.walk >= 0.25) {
      for (const f of footfalls) {
        if ((lastWalkPhase < f && phase >= f) || (phase < lastWalkPhase && (lastWalkPhase < f || phase >= f))) {
          squeaks.step(body.pos.x / W * 2 - 1);
          footsteps++;
        }
      }
    }
    lastWalkPhase = phase;

    // a little lift of the head as his mouth opens
    face.update(dt);
    if (head && face.mouth > 0.001) {
      head.parent.getWorldQuaternion(headParent);
      nod.setFromAxisAngle(axisX.set(1, 0, 0).applyQuaternion(holder.quaternion), -0.07 * face.mouth);
      head.quaternion.premultiply(headParent.clone().invert().multiply(nod).multiply(headParent));
    }

    toWorld(body.pos, holder.position);
    holder.rotation.y = body.yaw;
    holder.updateMatrixWorld(true);
    if (springsNeedReset) { springs.reset(); springsNeedReset = false; }
    springs.step(holder.position.y + 0.01);
  }

  const springs = makeSprings(model);
  const animated = new Map([...springs.bones, head].filter(Boolean).map((b) => [b, b.quaternion.clone()]));
  place(spot({}));
  requestAnimationFrame(frame);
  report({ event: "dabi3d-ready", height: Math.round(height * 100) / 100, size: sizePx });

  const api = {
    setTalking(on) {
      talking = on;
      if (!on) face.setMouth(0);
    },
    setMouthShape(shape) {
      face.setMouth(talking ? OPENNESS[shape] ?? 0 : 0);
    },
    setLevel(level) {
      face.setMouth(level > 0.06 ? Math.min(1, level * 3) : 0);
    },
    moveTo(p) {
      const s = spot(p || {});
      if (!p || !p.walk) {
        place(s);
      } else if (!(body.goal && s.x === body.goal.x && s.y === body.goal.y && s.seat === body.goal.seat)) {
        body.goal = s;                    // (the spot he's already on or heading for: nothing to do)
        if (body.mode === "settle") body.mode = "walk";   // still beside the old spot: walk to the new one
      }
    },
    // for poking at him from the browser console or a test
    sag: () => springs.sag(),
    state() {
      return { mode: body.mode, x: body.pos.x, y: body.pos.y, yaw: body.yaw, sit: body.sitW, walk: body.walk,
               goal: body.goal, ppm, height, footsteps };
    },
  };
  window.dabi3d = api;
  return api;
}

// ---------------------------------------------------------------------------
// still=1: one posed frame from a fixed angle, framed like Unity's reference
// renders (orthographic, `view` metres tall round `center`), for comparing the
// two. clip=Idle|Walk|Sit at t seconds; yaw > 0 swings round to his left.
// ---------------------------------------------------------------------------
function still({ gl, scene, camera, mixer, actions, face, light, params, num, report }) {
  const clip = params.get("clip") || "Idle";
  for (const [name, action] of Object.entries(actions)) {
    action.setEffectiveWeight(name === clip ? 1 : 0);
    action.time = name === clip ? num("t", 0) : 0;
  }
  mixer.update(0);
  face.set("M_a", num("mouth", 0));
  face.set("Blink", num("blink", 0));
  face.set("Blink_L", num("blinkL", 0));
  face.set("Blink_R", num("blinkR", 0));

  const W = window.innerWidth, H = window.innerHeight;
  gl.setSize(W, H, false);
  const view = num("view", 1.5);
  camera.top = view / 2; camera.bottom = -view / 2;
  camera.left = -view / 2 * W / H; camera.right = view / 2 * W / H;
  camera.updateProjectionMatrix();
  const yaw = THREE.MathUtils.degToRad(num("yaw", 0)), tilt = THREE.MathUtils.degToRad(num("tilt", 0));
  const center = new THREE.Vector3(0, num("center", clip === "Sit" ? 0.5 : 0.62), 0);
  camera.position.set(Math.sin(yaw) * Math.cos(tilt), Math.sin(tilt), Math.cos(yaw) * Math.cos(tilt))
    .multiplyScalar(10).add(center);
  camera.lookAt(center);
  camera.updateMatrixWorld();
  light.value.copy(LIGHT).transformDirection(camera.matrixWorldInverse);
  gl.render(scene, camera);
  document.title = "dabi3d still ready";
  report({ event: "dabi3d-still", clip });
  return { setTalking() {}, setMouthShape() {}, setLevel() {}, moveTo() {} };
}
