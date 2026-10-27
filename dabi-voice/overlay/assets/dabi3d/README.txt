Dabi's 3D body, for ?renderer=3d (dabi3d-renderer.js).

  dabi.glb   the Blender model, rig, face shape keys (M_a, Blink, Blink_L,
             Blink_R) and the Idle, Walk and Sit animations. Made by
             vtuber-george's blender/dabi_export_glb.py; don't edit it by hand.
             To update it, from vtuber-george:
               blender -b blender/dabi.blend --python blender/dabi_export_glb.py -- <this folder>/dabi.glb
  squeaks/   his squeaky shoes: bart's "Squeak toy sounds" (CC0,
             https://opengameart.org/content/squeak-toy-sounds), cut by
             vtuber-george for the Unity walker.

three.js (r170, MIT, ../vendor/three/LICENSE) draws him: three.module.min.js,
plus GLTFLoader.js and the BufferGeometryUtils.js it needs.
