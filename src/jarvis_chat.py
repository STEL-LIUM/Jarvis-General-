#!/usr/bin/env python3
"""
JARVIS Chat — a translucent overlay panel for talking to a local AI in real time.

A borderless, always-on-top panel that floats over your other windows. When
you're not using it, it fades to see-through — so a movie, stream, or app
behind it stays visible right through the panel. The moment you hover over
it or click into it, it snaps fully solid. Drag the header to move; drag the
bottom-right grip to resize; the - button minimizes; the X closes.

Talks directly to Ollama with auto fast/deep model routing. Replies stream
in live. No Discord, no cloud, no API keys.

Launch with:  pythonw jarvis_chat.py   (or the packaged .exe shortcut)
Requires Ollama running locally (the installer / first-run setup handles this).

Config (environment variables):
  JARVIS_CHAT_EDGE        right | left | top   (default: right)
  JARVIS_CHAT_WIDTH       panel width, px      (default: 300)
  JARVIS_CHAT_IDLE_ALPHA  idle see-through     (default: 0.45; 1.0 = always solid)

Slash commands inside the panel:  /clear   /help   /quit
"""
import base64
import ctypes
import io
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from ctypes import wintypes
import tkinter as tk
from tkinter import scrolledtext

try:
    from PIL import ImageGrab, Image
except ImportError:
    ImageGrab = Image = None

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    TkinterDnD = None     # type: ignore
    DND_FILES  = None     # type: ignore
    HAS_DND    = False

# Voice subsystem is optional — if any of (faster-whisper, openwakeword,
# piper-tts, sounddevice, webrtcvad) failed to install the panel still works
# in text-only mode and /voice just reports "unavailable".
try:
    from jarvis_voice import VoiceEngine
    HAS_VOICE = True
except Exception as _voice_err:
    VoiceEngine = None     # type: ignore
    HAS_VOICE = False
    _VOICE_IMPORT_ERR = _voice_err
else:
    _VOICE_IMPORT_ERR = None

# LLM Council (Mixture-of-Agents) — optional. When enabled, technical questions
# (per the router) convene a panel of models instead of a single deep model.
try:
    import jarvis_council
    HAS_COUNCIL = True
except Exception as _council_err:
    jarvis_council = None      # type: ignore
    HAS_COUNCIL = False
    _COUNCIL_IMPORT_ERR = _council_err
else:
    _COUNCIL_IMPORT_ERR = None

CLIENT_VERSION = "1.6.0"

# Edition: "jarvis" (default) or "manga". Set at build time via the
# JARVIS_EDITION env var baked into the spec, or per-user in config.json
# ("edition" key). ONE codebase — the edition only changes branding + the
# system-prompt flavor, never the feature set. Manga edition tunes JARVIS toward
# art / character design / manga discussion; the (future) drawing agent attaches
# here when it's ready.
def _resolve_edition() -> str:
    # Precedence: env var > edition.txt next to the exe (how the separate Manga
    # INSTALLER marks itself — it ships an edition.txt; the default installer
    # doesn't) > config.json "edition" key (runtime picker) > default "jarvis".
    # PyInstaller can't bake the edition into the binary (code is frozen, env is
    # not), so a shipped marker file is how two installers from one binary differ.
    try:
        e = (os.environ.get("JARVIS_EDITION") or "").strip().lower()
        if e in ("jarvis", "manga"):
            return e
    except Exception:
        pass
    # edition.txt beside the executable (frozen) or this source file (dev)
    try:
        base = (os.path.dirname(sys.executable)
                if getattr(sys, "frozen", False)
                else os.path.dirname(os.path.abspath(__file__)))
        for cand in (os.path.join(base, "edition.txt"),
                     os.path.join(base, "_internal", "edition.txt")):
            if os.path.isfile(cand):
                with open(cand, encoding="utf-8") as f:
                    e = f.read().strip().lower()
                if e in ("jarvis", "manga"):
                    return e
    except Exception:
        pass
    try:
        with open(os.path.join(
                os.getenv("LOCALAPPDATA") or os.path.expanduser("~"),
                "JarvisChat", "config.json"), encoding="utf-8") as f:
            import json as _json
            e = (_json.load(f).get("edition") or "").strip().lower()
            return e if e in ("jarvis", "manga") else "jarvis"
    except Exception:
        return "jarvis"


EDITION = _resolve_edition()
IS_MANGA = EDITION == "manga"
APP_TITLE = "JARVIS Manga" if IS_MANGA else "JARVIS"

# File extensions we'll read as text vs. as images.
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env",
    ".xml", ".html", ".htm", ".css", ".svg",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".java", ".go", ".rs", ".rb", ".php", ".sh", ".ps1",
    ".bat", ".cmd", ".lua", ".sql", ".pl", ".swift", ".kt",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
ATTACH_MAX_TEXT_BYTES  = 500 * 1024     # 500 KB
ATTACH_MAX_IMAGE_BYTES = 10 * 1024 * 1024   # 10 MB

# ── Design pipeline (Blender / bpy) ──────────────────────────────────────
# When the user asks JARVIS to "make a cube using blender" or similar, the
# deep model writes a bpy script which we then execute via:
#   blender --background --python script.py
# Outputs (.blend + .stl + preview.png) land in OUTPUTS_DIR/<timestamp>/.
# The user gets a confirm-before-run dialog every time — non-negotiable since
# the script is model-generated and we're invoking subprocess on their PC.
import shutil    # for blender lookup; cheap and idempotent
import glob      # to wildcard-match Blender install dirs

DESIGN_RE = re.compile(
    r"(?:"
    # Hard triggers — explicit artifact names
    r"\b3d model\b|\b3d print\b|\bstl file\b|\bcad model\b|"
    r"\bschematic of\b|\bcircuit diagram\b|\bwiring diagram\b|"
    r"\btechnical drawing\b|"
    # "design me / us / a / an / the / some / something / anything / new ..."
    # Bare "design X" deliberately not matched because it false-fires on
    # "design patterns", "design philosophy", etc.
    r"\bdesign\s+(?:me\b|us\b|for\s+me\b|a\b|an\b|the\b|some\b|something\b|"
        r"anything\b|new\b|another\b|us\s+a\b)|"
    # "design ... blender" or "blender ... design" anywhere in the message —
    # strong combined signal regardless of phrasing
    r"\bdesign\b[\s\S]*?\bblender\b|\bblender\b[\s\S]*?\bdesign\b|"
    # Verbs (make / build / create / generate / render) + 3D-ish noun
    r"\b(?:make|build|create|generate|render)\s+(?:me\s+|us\s+|for\s+me\s+)?"
        r"(?:a\s+|an\s+|the\s+|some\s+)?"
        r"(?:cube|sphere|cylinder|cone|torus|pyramid|plane|monkey|suzanne|"
            r"model|mesh|part|3d|object|shape|figure|geometry|primitive|"
            r"stl|cad|blender|design)\b|"
    # Blender mention with verb context
    r"\b(?:in|with|using)\s+blender\b|"
    # Model phrases
    r"\bmodel of\b|\bmake a model\b|\bcreate a model\b|\bbuild a model\b"
    r")",
    re.IGNORECASE,
)

# Edit/refine triggers — only meaningful when there's a PREVIOUS design to act
# on. "make it bigger", "add a handle", "now make it red", "taller", etc. These
# tweak the last design instead of generating from scratch, and JARVIS visually
# verifies the change actually happened (visual-diff).
EDIT_RE = re.compile(
    r"\b(?:make|made)\s+it\b|"
    r"\b(?:add|remove|delete)\s+(?:a\s+|an\s+|the\s+|some\s+)?\w+|"
    r"\bnow\s+(?:make|add|change|turn|rotate|scale|move)\b|"
    r"\b(?:bigger|smaller|taller|shorter|wider|thinner|longer|larger|"
        r"rounder|sharper|thicker|narrower)\b|"
    r"\bchange\s+(?:the\s+|its\s+)?\w+|"
    r"\b(?:more|less)\s+\w+|"
    r"\bmake\s+(?:it|them|the)\b",
    re.IGNORECASE,
)

DESIGN_SYSTEM_BPY = (
    "You write Python (bpy) scripts for Blender 4.x / 5.x running in "
    "--background mode. The script generates the user's requested 3D model and "
    "produces three output files: model.blend, model.stl, and preview.png.\n\n"
    "HARD RULES:\n"
    "- Output ONLY a complete runnable Python script. No prose, no explanation, "
    "no markdown fences. The very first line is an import (`import os`, "
    "`import bpy`, or `import math`).\n"
    "- These are ALREADY imported for you, use them freely WITHOUT importing: "
    "bpy, bmesh, os, sys, math, random, mathutils, and from mathutils: Vector, Matrix, "
    "Euler, Quaternion. (You may still import them again harmlessly.)\n"
    "- `OUT` is pre-defined as a string path to an existing output directory. "
    "Write ALL files into OUT, never anywhere else.\n"
    "- Forbidden: os.system, subprocess, eval, exec, urllib/network calls, "
    "writing outside OUT, reading files outside OUT, modifying anything in the "
    "user's home directory.\n"
    "- Keep the script under 120 lines. Clarity over cleverness.\n\n"
    "BLENDER 5.x API NOTES — these are the correct calls; don't use older variants:\n"
    "- STL export: `bpy.ops.wm.stl_export(filepath=...)`  "
    "(`bpy.ops.export_mesh.stl` was REMOVED in 4.0)\n"
    "- OBJ export: `bpy.ops.wm.obj_export(filepath=...)`  "
    "(not `bpy.ops.export_scene.obj`)\n"
    "- Render engine: `'BLENDER_EEVEE'` or `'CYCLES'`. In Blender 5.x the engine "
    "string is `'BLENDER_EEVEE'` (EEVEE Next is the default and uses this name). "
    "Do NOT use `'BLENDER_EEVEE_NEXT'` — that was a 4.2/4.3-only string and "
    "is REJECTED in 5.x with a TypeError.\n"
    "- Light energy: `light_obj.data.energy = float` after creation.\n"
    "- Material nodes are ENABLED BY DEFAULT in 5.x — do NOT call "
    "`mat.use_nodes = True` (deprecated, will be removed in 6.0). Just create "
    "the material and access `mat.node_tree.nodes['Principled BSDF']` directly.\n\n"
    "AVAILABLE MESH PRIMITIVES — this is the COMPLETE list. Do NOT invent new ones:\n"
    "  • bpy.ops.mesh.primitive_cube_add(size=..., location=...)\n"
    "  • bpy.ops.mesh.primitive_uv_sphere_add(radius=..., segments=..., ring_count=...)\n"
    "  • bpy.ops.mesh.primitive_ico_sphere_add(radius=..., subdivisions=...)\n"
    "  • bpy.ops.mesh.primitive_cylinder_add(radius=..., depth=..., vertices=...)\n"
    "  • bpy.ops.mesh.primitive_cone_add(radius1=..., radius2=..., depth=...)\n"
    "  • bpy.ops.mesh.primitive_torus_add(major_radius=..., minor_radius=...)\n"
    "  • bpy.ops.mesh.primitive_plane_add(size=..., location=...)\n"
    "  • bpy.ops.mesh.primitive_monkey_add(size=..., location=...)  # Suzanne\n"
    "  • bpy.ops.mesh.primitive_grid_add(x_subdivisions=..., y_subdivisions=...)\n"
    "  • bpy.ops.mesh.primitive_circle_add(vertices=..., radius=..., fill_type=...)\n"
    "There is NO primitive_vase_add, primitive_chair_add, primitive_mug_add, "
    "primitive_robot_add, etc. If the user asks for something not in the list "
    "above, you must BUILD it from these primitives + modifiers + bmesh, OR "
    "describe it as a combination of these (e.g. a vase = lathed curve via "
    "Screw modifier on a polyline; a mug = cylinder + boolean-difference inner "
    "cylinder + torus handle; a chair = stacked/joined cubes).\n\n"
    "USEFUL MODIFIERS for non-primitive shapes:\n"
    "  • SUBSURF (smoother surfaces), MIRROR (symmetry), ARRAY (repetition), "
    "BOOLEAN (cut shapes), SCREW (lathe a profile around an axis), "
    "BEVEL (rounded edges), SOLIDIFY (give a plane thickness).\n"
    "Apply modifiers via: `mod = obj.modifiers.new(name='Bevel', type='BEVEL'); "
    "mod.width = 0.05`. For booleans you set `mod.object = other_obj` and "
    "`mod.operation = 'DIFFERENCE'|'UNION'|'INTERSECT'`, then "
    "`bpy.context.view_layer.objects.active = obj; "
    "bpy.ops.object.modifier_apply(modifier=mod.name)`.\n\n"
    "CONSTRUCTION RECIPES — map the user's request to a pattern:\n"
    "  • Lathed / rotationally symmetric (vase, bowl, bottle, glass, cup, lamp, "
    "wine glass, pillar, column, gear blank): draw a 2D profile as a polyline "
    "(bmesh) → add SCREW modifier with axis='Z' and steps≈32.\n"
    "  • Hollow shapes (mug, pot, ring, bowl-with-thickness): start from a "
    "solid primitive, BOOLEAN-DIFFERENCE a slightly smaller primitive offset "
    "upward, optionally SOLIDIFY for wall thickness.\n"
    "  • Repeating elements (fence, brick wall, gear teeth, comb, staircase): "
    "build ONE unit + ARRAY modifier with offset.\n"
    "  • Symmetric organic (face, character, plane, fish): build half + MIRROR.\n"
    "  • Sharp edges → smooth: BEVEL with width≈0.02–0.1.\n"
    "  • Flat shape → thick (logos, signs, gears extruded from profile): "
    "create plane or polyline → SOLIDIFY with thickness.\n"
    "  • Smooth organic surface (head, body, blob): start with cube or sphere "
    "→ SUBSURF level 2 → manipulate.\n"
    "  • Composite objects (chair, robot, car): build each part as a separate "
    "primitive with location/rotation/scale, then assemble; pick one "
    "representative object as the camera's TRACK_TO target.\n\n"
    "WORKED EXAMPLE — mug (cylinder + boolean + torus handle):\n"
    "    bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=1.2)\n"
    "    body = bpy.context.active_object\n"
    "    bpy.ops.mesh.primitive_cylinder_add(radius=0.42, depth=1.1, location=(0,0,0.1))\n"
    "    inner = bpy.context.active_object\n"
    "    inner.hide_render = True; inner.hide_viewport = True\n"
    "    mod = body.modifiers.new('hollow', 'BOOLEAN')\n"
    "    mod.object = inner; mod.operation = 'DIFFERENCE'\n"
    "    bpy.context.view_layer.objects.active = body\n"
    "    bpy.ops.object.modifier_apply(modifier='hollow')\n"
    "    bpy.ops.mesh.primitive_torus_add(major_radius=0.25, minor_radius=0.06,\n"
    "        location=(0.55, 0, 0), rotation=(1.5708, 0, 0))\n"
    "    handle = bpy.context.active_object\n"
    "    target = body   # camera tracks the mug body\n\n"
    "WORKED EXAMPLE — vase (silhouette + screw lathe):\n"
    "    import bmesh\n"
    "    mesh = bpy.data.meshes.new('vase_profile')\n"
    "    obj = bpy.data.objects.new('vase', mesh)\n"
    "    bpy.context.collection.objects.link(obj)\n"
    "    bm = bmesh.new()\n"
    "    # (radius_at_height_z) silhouette: base, swell, neck, lip\n"
    "    profile = [(0.25, 0.0), (0.45, 0.3), (0.55, 0.7),\n"
    "               (0.30, 1.2), (0.40, 1.45)]\n"
    "    prev = None\n"
    "    for r, z in profile:\n"
    "        v = bm.verts.new((r, 0, z))\n"
    "        if prev: bm.edges.new([prev, v])\n"
    "        prev = v\n"
    "    bm.to_mesh(mesh); bm.free()\n"
    "    mod = obj.modifiers.new('lathe', 'SCREW')\n"
    "    mod.axis = 'Z'; mod.steps = 48; mod.use_smooth_shade = True\n"
    "    bpy.context.view_layer.objects.active = obj\n"
    "    bpy.ops.object.modifier_apply(modifier='lathe')\n"
    "    target = obj\n\n"
    "BMESH QUICK REFERENCE for custom geometry:\n"
    "    import bmesh\n"
    "    bm = bmesh.new()\n"
    "    v1 = bm.verts.new((x, y, z))   # add vertex\n"
    "    e  = bm.edges.new([v1, v2])    # add edge between verts\n"
    "    f  = bm.faces.new([v1, v2, v3, v4])  # add face from verts\n"
    "    bm.to_mesh(mesh); bm.free()    # commit to a Mesh datablock\n\n"
    "MATERIAL QUICK REFERENCE (5.x — nodes are on by default, do NOT set use_nodes):\n"
    "    mat = bpy.data.materials.new(name='M')\n"
    "    bsdf = mat.node_tree.nodes['Principled BSDF']\n"
    "    bsdf.inputs['Base Color'].default_value = (0.8, 0.3, 0.2, 1.0)  # RGBA\n"
    "    bsdf.inputs['Roughness'].default_value = 0.5\n"
    "    obj.data.materials.append(mat)\n\n"
    "SCRIPT TEMPLATE — adapt the geometry section to the user's request, keep "
    "everything else close to this:\n"
    "\n"
    "    import os\n"
    "    import math\n"
    "    import bpy\n"
    "\n"
    "    # 1. Clean scene\n"
    "    bpy.ops.wm.read_factory_settings(use_empty=True)\n"
    "\n"
    "    # 2. Build the requested geometry — adapt this section.\n"
    "    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))\n"
    "    target = bpy.context.active_object\n"
    "\n"
    "    # 3. Camera that tracks the object via a TRACK_TO constraint so the\n"
    "    #    render is never off-target.\n"
    "    bpy.ops.object.camera_add(location=(6, -6, 4))\n"
    "    cam = bpy.context.active_object\n"
    "    bpy.context.scene.camera = cam\n"
    "    track = cam.constraints.new(type='TRACK_TO')\n"
    "    track.target = target\n"
    "    track.track_axis = 'TRACK_NEGATIVE_Z'\n"
    "    track.up_axis    = 'UP_Y'\n"
    "\n"
    "    # 4. Sun light, energy 3 — keeps the render bright on default world bg.\n"
    "    bpy.ops.object.light_add(type='SUN', location=(5, 5, 10))\n"
    "    bpy.context.active_object.data.energy = 3.0\n"
    "\n"
    "    # 5. Render settings — EEVEE is fast (~1-3 sec for simple scenes).\n"
    "    scn = bpy.context.scene\n"
    "    scn.render.engine = 'BLENDER_EEVEE'   # 5.x string — NOT 'BLENDER_EEVEE_NEXT'\n"
    "    scn.render.resolution_x = 960\n"
    "    scn.render.resolution_y = 720\n"
    "    scn.render.resolution_percentage = 100\n"
    "    scn.render.image_settings.file_format = 'PNG'\n"
    "    scn.eevee.taa_render_samples = 32\n"
    "\n"
    "    # 6. Save all three outputs.\n"
    "    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, 'model.blend'))\n"
    "    bpy.ops.wm.stl_export(filepath=os.path.join(OUT, 'model.stl'))\n"
    "    scn.render.filepath = os.path.join(OUT, 'preview.png')\n"
    "    bpy.ops.render.render(write_still=True)\n"
    "\n"
    "For multi-part models: build all parts (each primitive_X_add call), then "
    "set `target` to a parent empty (or to a representative central object) so "
    "the camera frames the whole scene. For curves / metaballs / text, the "
    "principle is the same — give the camera a real Object to TRACK_TO.\n\n"
    "COMMON MISTAKES - these caused ~75% of past failures. AVOID every one:\n1. OPERATORS RETURN {'FINISHED'}, NOT THE OBJECT. Never write obj = bpy.ops.mesh.primitive_cube_add(...) - that makes obj a set and obj.modifiers crashes. ALWAYS grab the object on the NEXT line: call bpy.ops.mesh.primitive_cube_add(...) then obj = bpy.context.active_object.\n2. CUBE/PLANE have NO depth/radius and size is a FLOAT not a tuple. Use primitive_cube_add(size=2.0, location=(x,y,z)); for a non-cubic box set obj.scale = (sx, sy, sz) AFTER creating it. Only cylinder/cone/torus take depth/radius. Use ONLY the kwargs listed in the primitive section - any other keyword raises 'keyword unrecognized'.\n3. ARRAY modifier has NO .offset. For spacing use mod.use_relative_offset = True; mod.relative_offset_displace = (1.5, 0, 0) OR mod.use_constant_offset = True; mod.constant_offset_displace = (d, 0, 0). Count is mod.count = N with mod.fit_type = 'FIXED_COUNT' (valid: 'FIXED_COUNT','FIT_LENGTH','FIT_CURVE' - NOT 'FIT').\n4. ENUM strings must be EXACT. Subsurf: mod.subdivision_type = 'CATMULL_CLARK' (underscore) or 'SIMPLE'. Boolean op: 'DIFFERENCE'/'UNION'/'INTERSECT'. Screw axis: 'Z'.\n5. BMESH is pre-imported. After adding verts call bm.verts.ensure_lookup_table() BEFORE indexing bm.verts[i]. bm.edges.new([v1, v2]) and bm.faces.new([...]) take BMVert OBJECTS from bm.verts.new((x,y,z)) - never raw tuples. bpy.data.meshes.new('name') takes ONLY a name; fill via bm.to_mesh(mesh) or mesh.from_pydata(verts, edges, faces).\n6. NEVER look up objects/data by guessed name (bpy.data.objects['Cube'], curves['Bezier Curve']) - raises KeyError. KEEP the variable from creation and reuse THAT.\n7. Operator-created objects are ALREADY in the scene - only call bpy.context.collection.objects.link(obj) for objects YOU built with bpy.data.objects.new(...). Linking twice raises 'already in collection'.\n8. After join/boolean-apply/delete the consumed object is GONE - using its old variable raises 'StructRNA has been removed'. Re-fetch what you need.\n9. Before bpy.ops.object.modifier_apply(...), set the object active: bpy.context.view_layer.objects.active = obj (object mode).\n10. Colors are RGBA = 4 floats: (0.8, 0.3, 0.2, 1.0) - never 3.\n11. There is NO primitive_line_add (or any primitive not in the list) - build lines/polylines in bmesh. ALWAYS create a camera AND set bpy.context.scene.camera = cam, or rendering fails with 'no camera'.\n\n"
    "Write the complete script now. Nothing but Python — no prose, no comments "
    "explaining what you wrote, no markdown."
)

OUTPUTS_DIR = os.path.join(
    os.getenv("LOCALAPPDATA", os.path.expanduser("~")),
    "JarvisChat", "Outputs"
)

# ── Project source-code registry ─────────────────────────────────────────
# When the user mentions a known project in a chat, JarvisChat reads the
# actual source files from disk and injects them as system context. Paths
# are user-specific (this developer's machine) — on a fresh downloader's
# system the files won't exist and get_project_context() silently returns
# an empty string. Override the whole list via $JARVIS_CHAT_PROJECTS_FILE
# pointing at a JSON file with the same {name: [paths]} structure.
KNOWN_PROJECTS: dict[str, list[str]] = {
    "JARVIS bot (Jarvis Code / BotFile.py)": [
        r"C:\Users\Aryan\OneDrive\Desktop - Copy\Desktop\JarvisAI\Jarvis Code\BotFile.py",
    ],
    "PROMETHEUS": [
        r"C:\Users\Aryan\OneDrive\Desktop - Copy\Desktop\PROMETHEUS\prometheus\core.py",
        r"C:\Users\Aryan\OneDrive\Desktop - Copy\Desktop\PROMETHEUS\prometheus\council.py",
    ],
    "JarvisChat (this app)": [
        r"C:\Users\Aryan\OneDrive\Desktop - Copy\Desktop\JarvisAI\JarvisChat-Release\src\jarvis_chat.py",
        r"C:\Users\Aryan\OneDrive\Desktop - Copy\Desktop\JarvisAI\JarvisChat-Release\src\jarvis_setup.py",
    ],
}
_PROJECT_TRIGGERS: dict[str, re.Pattern] = {
    "JARVIS bot (Jarvis Code / BotFile.py)": re.compile(
        r"\b(jarvis[\s_-]?code|botfile(?:\.py)?|the\s+(?:discord\s+)?bot|"
        r"discord\s+bot|the\s+jarvis\s+bot)\b",
        re.IGNORECASE,
    ),
    "PROMETHEUS": re.compile(
        r"\b(prometheus|oscillatornetwork|swarmcouncil|"
        r"kuramoto[\s\-]?(?:net|model|oscillator)|"
        r"phase[\s\-]oscillator[\s\-](?:net|model))\b",
        re.IGNORECASE,
    ),
    "JarvisChat (this app)": re.compile(
        r"\b(jarvischat|jarvis[\s_-]chat(?:\s+(?:app|panel))?|jarvis_chat\.py|"
        r"jarvis_setup\.py|chat[\s_-]panel|this[\s_-]app|the[\s_-](?:installer|exe))\b",
        re.IGNORECASE,
    ),
}
MAX_PROJECT_FILE_BYTES = 50000   # cap per file — keeps long files from blowing num_ctx


def _load_user_projects_override() -> dict[str, list[str]] | None:
    """If $JARVIS_CHAT_PROJECTS_FILE points at a JSON {name: [paths]}, load it."""
    path = os.getenv("JARVIS_CHAT_PROJECTS_FILE")
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Validate shape: dict of str -> list of str
        if isinstance(data, dict) and all(
            isinstance(k, str) and isinstance(v, list)
            and all(isinstance(p, str) for p in v)
            for k, v in data.items()
        ):
            return data
    except Exception:
        pass
    return None


def get_project_context(prompt: str) -> str:
    """If `prompt` mentions a known project, read the source files and return
    them as one big injectable block. Returns '' when nothing matches OR
    when the files don't exist on this machine (downloader path)."""
    override = _load_user_projects_override()
    projects = override if override else KNOWN_PROJECTS
    triggers = _PROJECT_TRIGGERS if not override else {
        name: re.compile(rf"\b{re.escape(name.split()[0])}\b", re.IGNORECASE)
        for name in projects
    }
    matched = [name for name, pat in triggers.items() if pat.search(prompt)]
    if not matched:
        return ""
    chunks: list[str] = []
    for name in matched:
        for path in projects.get(name, []):
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    content = f.read(MAX_PROJECT_FILE_BYTES)
            except OSError:
                continue
            if not content:
                continue
            ext = os.path.splitext(path)[1].lstrip(".") or "txt"
            rel = os.path.basename(path)
            chunks.append(f"--- {name}/{rel} ---\n```{ext}\n{content}\n```")
    if not chunks:
        return ""
    return ("READ THIS BEFORE ANSWERING — the user is asking about the "
            "following source code:\n\n" + "\n\n".join(chunks))

# Blender version we ship/test against. Bump this *together* with the URL in
# jarvis_setup.py so the auto-checker knows what to compare against.
BLENDER_EXPECTED_VERSION = "5.1.2"
BLENDER_INSTALLER_URL = (
    "https://download.blender.org/release/Blender5.1/blender-5.1.2-windows-x64.msi"
)


def _find_blender() -> str | None:
    """Locate blender.exe on Windows. Returns the full path or None."""
    on_path = shutil.which("blender")
    if on_path:
        return on_path
    candidates = []
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates += glob.glob(os.path.join(pf, "Blender Foundation", "Blender *", "blender.exe"))
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates += glob.glob(os.path.join(pf86, "Blender Foundation", "Blender *", "blender.exe"))
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidates += glob.glob(os.path.join(local, "Programs", "Blender Foundation",
                                              "Blender *", "blender.exe"))
        candidates += glob.glob(os.path.join(local, "Programs", "Blender", "blender.exe"))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _blender_version(blender_path: str) -> str | None:
    """Return installed Blender's X.Y.Z version string by running --version."""
    try:
        DETACHED = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            [blender_path, "--version"],
            capture_output=True, text=True, timeout=10,
            creationflags=DETACHED,
        )
    except Exception:
        return None
    for line in (proc.stdout or "").splitlines():
        m = re.match(r"\s*Blender\s+(\d+\.\d+(?:\.\d+)?)", line)
        if m:
            return m.group(1)
    return None


def _ver_tuple(v: str) -> tuple[int, ...]:
    """'5.1.2' -> (5,1,2). Used for ordered version comparison."""
    parts: list[int] = []
    for p in (v or "").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts)


def check_blender_version_async(q: queue.Queue) -> None:
    """Background: if Blender is installed but on a different version than the
    one we ship for, queue ('blender_update_available', installed, expected,
    blender_path). Skipped when the user has /updates off."""
    def _go() -> None:
        cfg = load_config()
        if cfg.get("check_for_updates") is False:
            return    # same toggle as JARVIS-app updates
        blender = _find_blender()
        if not blender:
            return    # not installed → first-run wizard handles install separately
        installed = _blender_version(blender)
        if not installed:
            return    # couldn't determine — don't pester
        if _ver_tuple(installed) == _ver_tuple(BLENDER_EXPECTED_VERSION):
            return    # matches; nothing to do
        q.put(("blender_update_available", installed,
               BLENDER_EXPECTED_VERSION, blender))
    threading.Thread(target=_go, daemon=True).start()

# --- Config ----------------------------------------------------------------
CHAT_URL   = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
TAGS_URL   = CHAT_URL.rsplit("/api/", 1)[0] + "/api/tags"
FAST_MODEL = os.getenv("OLLAMA_FAST_MODEL", "qwen2.5:7b")
# DEEP_MODEL starts as the safe Standard-pack default. _autodetect_deep_model()
# called from main() at startup runs through DEEP_MODEL_FALLBACKS (heaviest
# first) and swaps in the largest one actually installed in Ollama, unless the
# user pinned a specific model via $OLLAMA_MODEL.
DEEP_MODEL = os.getenv("OLLAMA_MODEL",      "deepseek-r1:14b")
DEEP_MODEL_FALLBACKS = ["deepseek-r1:70b", "deepseek-r1:32b", "deepseek-r1:14b", "deepseek-r1:7b"]
VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
# Vision fallbacks if the preferred model isn't pulled (first that exists wins).
VISION_MODEL_FALLBACKS = ["qwen2.5vl:7b", "llava:13b", "llava:7b"]
# Code model — used to REPAIR failed design scripts (_fix_design_script). The
# coder is fast and strong at "fix this error" (little/no <think> reasoning vs
# deepseek). Repair falls back to FAST_MODEL if the coder isn't pulled, so a
# missing model never silently breaks auto-repair.
CODE_MODEL = os.getenv("OLLAMA_CODE_MODEL", "qwen2.5-coder:7b")


def _autodetect_deep_model() -> None:
    """Pick the heaviest installed deepseek-r1 in Ollama. Updates DEEP_MODEL.
    Skipped when the user pinned a specific model via $OLLAMA_MODEL."""
    if os.getenv("OLLAMA_MODEL"):
        return    # user override wins
    try:
        with urllib.request.urlopen(TAGS_URL, timeout=4) as r:
            data = json.load(r)
        installed = {(m.get("name") or "") for m in data.get("models", [])}
    except Exception:
        return    # Ollama not up yet — stick with module default
    global DEEP_MODEL
    for candidate in DEEP_MODEL_FALLBACKS:
        if candidate in installed:
            DEEP_MODEL = candidate
            return


def _autodetect_vision_model() -> None:
    """Pick the best installed vision model from VISION_MODEL_FALLBACKS so a
    missing qwen2.5vl gracefully degrades to llava instead of erroring.
    Skipped when the user pinned $OLLAMA_VISION_MODEL."""
    if os.getenv("OLLAMA_VISION_MODEL"):
        return    # user override wins
    try:
        with urllib.request.urlopen(TAGS_URL, timeout=4) as r:
            data = json.load(r)
        installed = {(m.get("name") or "") for m in data.get("models", [])}
    except Exception:
        return
    global VISION_MODEL
    for candidate in VISION_MODEL_FALLBACKS:
        if candidate in installed:
            VISION_MODEL = candidate
            return
# Ollama's default num_ctx is 2048, which silently truncates long chats.
try:
    NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
except ValueError:
    NUM_CTX = 8192
try:
    NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "6144"))
except ValueError:
    NUM_PREDICT = 6144
EDGE_NAME  = os.getenv("JARVIS_CHAT_EDGE", "right").strip().lower()
try:
    PANEL_SIZE = max(200, int(os.getenv("JARVIS_CHAT_WIDTH", "300")))
except ValueError:
    PANEL_SIZE = 300
try:
    IDLE_ALPHA = min(1.0, max(0.15, float(os.getenv("JARVIS_CHAT_IDLE_ALPHA", "0.45"))))
except ValueError:
    IDLE_ALPHA = 0.45

# --- Telemetry / opt-in contributions --------------------------------------
# Off until the user explicitly opts in on first launch. Text chats only; never
# screenshots. Submissions go through a small HF Space that holds the dataset
# write token server-side, so the .exe carries no secrets.
TELEMETRY_URL = os.getenv(
    "JARVIS_TELEMETRY_URL",
    "https://stel-lium-jarvis-feedback-api.hf.space/submit",
)
TELEMETRY_DATASET_URL = "https://huggingface.co/datasets/STEL-LIUM/jarvis-feedback"

CONSENT_TITLE = "Welcome to JARVIS Chat — please read"


def _config_dir() -> str:
    """Where consent + install ID live. Per-machine, never inside the .exe."""
    override = os.getenv("JARVIS_CHAT_CONFIG_DIR")
    if override:
        return override
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "JarvisChat")


def _config_path() -> str:
    return os.path.join(_config_dir(), "config.json")


def load_config() -> dict:
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_config(cfg: dict) -> None:
    try:
        os.makedirs(_config_dir(), exist_ok=True)
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


def get_install_id(cfg: dict) -> str:
    if "install_id" not in cfg:
        cfg["install_id"] = str(uuid.uuid4())
        save_config(cfg)
    return cfg["install_id"]


def _consent_dialog() -> bool:
    """Modal first-launch consent window. Owns its own Tk root and mainloop so
    nothing else needs to be visible (or hidden) on screen while it's open."""
    dlg = tk.Tk()
    dlg.title(CONSENT_TITLE)
    dlg.configure(bg=BG)
    dlg.resizable(False, False)
    _try_set_icon(dlg)
    try:
        dlg.attributes("-topmost", True)
    except Exception:
        pass

    W, H = 560, 470
    sw = dlg.winfo_screenwidth()
    sh = dlg.winfo_screenheight()
    dlg.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 3}")

    pad = {"padx": 24, "pady": (0, 4)}
    tk.Label(dlg, text="● JARVIS Chat", bg=BG, fg=ACCENT,
             font=("Segoe UI", 14, "bold"), anchor="w"
             ).pack(fill="x", padx=24, pady=(20, 4))
    tk.Label(dlg, text="A quick note before you start", bg=BG, fg=MUTED,
             font=("Segoe UI", 10), anchor="w"
             ).pack(fill="x", **pad)

    body = (
        "JARVIS Chat runs fully locally on your PC. To help train future "
        "versions of JARVIS, the app can optionally upload your text "
        "conversations to a public dataset on Hugging Face."
    )
    tk.Label(dlg, text=body, bg=BG, fg=FG, wraplength=W - 48,
             justify="left", anchor="w", font=("Segoe UI", 10)
             ).pack(fill="x", padx=24, pady=(8, 12))

    shared = tk.Label(
        dlg, bg=BG, fg=FG, wraplength=W - 48, justify="left", anchor="w",
        font=("Segoe UI", 10),
        text=("If you share:\n"
              "   • Each text exchange (your message + JARVIS's reply) is uploaded\n"
              "   • Anonymously — random install ID, no name, no IP, no email\n"
              "   • Screenshots are NEVER uploaded, even when you ask JARVIS to look\n"
              "   • Public dataset: huggingface.co/datasets/STEL-LIUM/jarvis-feedback"))
    shared.pack(fill="x", padx=24, pady=(0, 8))

    notshared = tk.Label(
        dlg, bg=BG, fg=FG, wraplength=W - 48, justify="left", anchor="w",
        font=("Segoe UI", 10),
        text=("If you don't share:\n"
              "   • Nothing leaves your PC. Ever."))
    notshared.pack(fill="x", padx=24, pady=(0, 10))

    tk.Label(dlg, text="You can change this any time inside the panel with "
                       "/optin or /optout.",
             bg=BG, fg=MUTED, wraplength=W - 48, justify="left", anchor="w",
             font=("Segoe UI", 9, "italic")
             ).pack(fill="x", padx=24, pady=(0, 14))

    # Default is YES (opt-in): if the user closes the window, hits Enter, or
    # presses Escape without explicitly picking No, we treat that as consent.
    choice = {"share": True}

    def _pick(share: bool) -> None:
        choice["share"] = share
        dlg.quit()

    btn_row = tk.Frame(dlg, bg=BG)
    btn_row.pack(fill="x", padx=24, pady=(0, 20))
    no_btn = tk.Button(
        btn_row, text="No thanks — keep everything local",
        command=lambda: _pick(False),
        bg=INPUT_BG, fg=FG, activebackground=PANEL, activeforeground=FG,
        bd=0, relief="flat", padx=14, pady=10, cursor="hand2",
        font=("Segoe UI", 10, "bold"))
    no_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
    yes_btn = tk.Button(
        btn_row, text="Yes, share my chats",
        command=lambda: _pick(True),
        bg=ACCENT, fg=BG, activebackground=YOU, activeforeground=BG,
        bd=0, relief="flat", padx=14, pady=10, cursor="hand2",
        font=("Segoe UI", 10, "bold"))
    yes_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))

    # Yes is the default — Enter accepts sharing; Escape / closing still goes
    # with the default (Yes). The user must actively click "No thanks" to decline.
    yes_btn.focus_set()
    dlg.bind("<Return>", lambda _e: _pick(True))
    dlg.bind("<Escape>", lambda _e: _pick(True))
    dlg.protocol("WM_DELETE_WINDOW", lambda: _pick(True))

    dlg.lift()
    dlg.focus_force()
    dlg.mainloop()
    dlg.destroy()
    return bool(choice["share"])


def _license_dialog() -> bool:
    """Modal license-gate window. Owns its own Tk root + mainloop (like the
    consent dialog). Returns True if a valid license/comp key was activated,
    False if the user chose to quit. Lets the user paste a key, verify it, or
    open the Gumroad subscribe page. Fail-open if the license module is missing."""
    if _license_mod is None:
        return True   # module absent (dev checkout) -> don't block

    dlg = tk.Tk()
    dlg.title("JARVIS Chat — Activate")
    dlg.configure(bg=BG)
    dlg.resizable(False, False)
    _try_set_icon(dlg)
    try:
        dlg.attributes("-topmost", True)
    except Exception:
        pass
    W, H = 520, 360
    sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
    dlg.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 3}")

    tk.Label(dlg, text="● JARVIS Chat", bg=BG, fg=ACCENT,
             font=("Segoe UI", 14, "bold"), anchor="w"
             ).pack(fill="x", padx=24, pady=(20, 2))
    tk.Label(dlg, text="Activate your subscription to start", bg=BG, fg=MUTED,
             font=("Segoe UI", 10), anchor="w"
             ).pack(fill="x", padx=24, pady=(0, 10))
    tk.Label(dlg, bg=BG, fg=FG, wraplength=W - 48, justify="left", anchor="w",
             font=("Segoe UI", 10),
             text=("JARVIS Chat is free to download, and runs fully on your PC. "
                   "Using JARVIS needs an active subscription ($25/month).\n\n"
                   "Already subscribed? Paste your license key below. Coworkers: "
                   "use your team code.")
             ).pack(fill="x", padx=24, pady=(0, 12))

    entry = tk.Entry(dlg, bg=INPUT_BG, fg=FG, bd=0, relief="flat",
                     insertbackground=FG, font=("Consolas", 11))
    entry.pack(fill="x", padx=24, ipady=8)
    status = tk.Label(dlg, text="", bg=BG, fg=MUTED, wraplength=W - 48,
                      justify="left", anchor="w", font=("Segoe UI", 9))
    status.pack(fill="x", padx=24, pady=(6, 8))

    result = {"ok": False}

    def _activate(_=None):
        key = entry.get().strip()
        status.config(text="Verifying…", fg=MUTED)
        dlg.update_idletasks()
        ok, msg = _license_mod.check_key(key)
        status.config(text=msg, fg=(JARVIS if ok else CLOSE_HL))
        if ok:
            result["ok"] = True
            dlg.after(700, dlg.quit)

    def _subscribe():
        try:
            import webbrowser
            webbrowser.open(GUMROAD_PRODUCT_URL)
            status.config(text="Opened the subscribe page in your browser.",
                          fg=MUTED)
        except Exception:
            status.config(text=GUMROAD_PRODUCT_URL, fg=MUTED)

    def _quit():
        result["ok"] = False
        dlg.quit()

    row = tk.Frame(dlg, bg=BG)
    row.pack(fill="x", padx=24, pady=(4, 8))
    tk.Button(row, text="Subscribe", command=_subscribe,
              bg=INPUT_BG, fg=FG, activebackground=PANEL, activeforeground=FG,
              bd=0, relief="flat", padx=14, pady=9, cursor="hand2",
              font=("Segoe UI", 10, "bold")).pack(side="left", fill="x",
                                                  expand=True, padx=(0, 6))
    tk.Button(row, text="Activate", command=_activate,
              bg=ACCENT, fg=BG, activebackground=YOU, activeforeground=BG,
              bd=0, relief="flat", padx=14, pady=9, cursor="hand2",
              font=("Segoe UI", 10, "bold")).pack(side="left", fill="x",
                                                  expand=True, padx=(6, 0))
    tk.Button(dlg, text="Quit", command=_quit, bg=BG, fg=MUTED,
              activebackground=BG, activeforeground=CLOSE_HL, bd=0,
              relief="flat", cursor="hand2", font=("Segoe UI", 9)
              ).pack(pady=(0, 12))

    entry.focus_set()
    dlg.bind("<Return>", _activate)
    dlg.protocol("WM_DELETE_WINDOW", _quit)
    dlg.lift(); dlg.focus_force()
    dlg.mainloop()
    dlg.destroy()
    return bool(result["ok"])


def ensure_license() -> bool:
    """Gate the app on a valid license. Returns True to proceed, False to exit.
    Fail-open only if the license module is entirely absent (dev checkout)."""
    if _license_mod is None:
        return True
    try:
        ok, _status = _license_mod.is_licensed()
    except Exception:
        ok = False
    if ok:
        return True
    return _license_dialog()


def ensure_consent() -> bool:
    """Return True if the user has opted in. Shows the dialog once if undecided."""
    cfg = load_config()
    if "consent_telemetry" in cfg:
        return bool(cfg["consent_telemetry"])
    share = _consent_dialog()
    cfg["consent_telemetry"] = bool(share)
    cfg["consent_decided_at"] = int(time.time())
    save_config(cfg)
    return bool(share)


# --- Auto-update (GitHub Releases poll) -----------------------------------
# Checked on every launch. Disable with /updates off (sets check_for_updates
# to False in the config). GitHub's anonymous API allows 60 reqs/hr/IP, so
# per-launch is fine for normal use.
GITHUB_LATEST_URL = "https://api.github.com/repos/STEL-LIUM/Jarvis-General-/releases/latest"
UPDATE_ASSET_NAME = "JarvisChat-Setup.exe"


def _parse_version(v: str) -> list[int]:
    """Lax semver-ish parse: 'v1.2.0', '1.2', '1.2.0-beta' -> [1,2,0] etc."""
    v = (v or "").lstrip("vV").split("-")[0]
    parts: list[int] = []
    for p in v.split("."):
        if not p.isdigit():
            break
        parts.append(int(p))
    return parts


def _is_newer(current: str, remote: str) -> bool:
    a, b = _parse_version(current), _parse_version(remote)
    if not a or not b:
        return False
    n = max(len(a), len(b))
    a = a + [0] * (n - len(a))
    b = b + [0] * (n - len(b))
    return b > a


def check_update_async(q: queue.Queue, force: bool = False,
                       auto_install: bool = False) -> None:
    """Background GitHub poll. Puts ('update_available', tag, url, notes,
    auto_install) on the queue when a newer release exists. When force=True,
    also reports 'no update' and error states so the UI never leaves a
    'checking…' status. When auto_install=True, the receiver (in _poll) shows
    the install dialog immediately on a positive result."""
    def _go() -> None:
        cfg = load_config()
        if cfg.get("check_for_updates") is False and not force:
            return
        try:
            req = urllib.request.Request(
                GITHUB_LATEST_URL,
                headers={"User-Agent": f"JarvisChat/{CLIENT_VERSION}",
                         "Accept": "application/vnd.github+json"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if force:
                if e.code == 404:
                    q.put(("note", "No GitHub releases published yet — "
                                   "nothing to update from."))
                else:
                    q.put(("note", f"Update check failed: HTTP {e.code}"))
            return
        except Exception as e:
            if force:
                q.put(("note", f"Update check failed: {e}"))
            return

        cfg["last_update_check"] = int(time.time())
        save_config(cfg)
        tag = data.get("tag_name", "")
        if not _is_newer(CLIENT_VERSION, tag):
            if force:
                q.put(("note",
                       f"You're on {CLIENT_VERSION} — that's the latest. "
                       f"(GitHub: {tag or 'unknown'})"))
            return
        asset = next(
            (a for a in data.get("assets", [])
             if a.get("name") == UPDATE_ASSET_NAME),
            None)
        if not asset:
            if force:
                q.put(("note",
                       f"Release {tag} exists but has no {UPDATE_ASSET_NAME} "
                       "asset attached."))
            return
        q.put(("update_available", tag,
               asset.get("browser_download_url", ""),
               (data.get("body", "") or "")[:400],
               auto_install))
    threading.Thread(target=_go, daemon=True).start()


# --- File attach helpers --------------------------------------------------
def _parse_drop_data(data: str) -> list[str]:
    """Parse a Tkdnd <<Drop>> data string into a list of file paths.
    Paths with spaces are wrapped in {braces} (Tcl list syntax)."""
    paths: list[str] = []
    i, n = 0, len(data)
    while i < n:
        if data[i] == "{":
            end = data.find("}", i)
            if end == -1:
                break
            paths.append(data[i + 1:end])
            i = end + 1
        elif data[i].isspace():
            i += 1
        else:
            end = i
            while end < n and not data[end].isspace():
                end += 1
            paths.append(data[i:end])
            i = end
    return [p for p in paths if p]


def _detect_kind(path: str) -> str:
    """Classify a file as 'image', 'text', or 'unknown'."""
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in TEXT_EXTENSIONS:
        return "text"
    # Unknown extension — sniff the first chunk and treat as text if it decodes.
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        chunk.decode("utf-8")
        return "text"
    except Exception:
        return "unknown"


# --- BS-filter: second-pass self-check ------------------------------------
# After the deep model answers, fire a quick fast-model call that flags
# buzzword labels without mechanism, broken code, and vague claims. If it
# finds something, post a (self-check) note in the panel. Same idea as the
# Discord bot's bs_filter_check — adapted for the chat panel's stream loop.
_BS_FILTER_PROMPT = (
    "You are JARVIS's self-check pass. The original assistant just answered the user. "
    "Catch lazy or unsupported claims. Return a SINGLE JSON object on one line: "
    "{\"pass\": bool, \"issue\": \"...\"} (omit issue when pass=true).\n\n"
    "HARD GUARDRAILS (read first):\n"
    " • If you flag, your 'issue' field MUST quote the exact problematic word, phrase, "
    "or line from the assistant's reply (in single quotes). If you can't quote it, "
    "return {\"pass\": true}.\n"
    " • Check #2 (broken code) only applies if the reply CONTAINS a code block "
    "(text inside triple-backtick fences). No code block in the reply = never flag for "
    "code issues — there's nothing there to be broken.\n"
    " • Tutorial-style explanations are valid answers, not 'vague suggestions'.\n\n"
    "Flag these failure modes (pick the MOST important one):\n"
    " 1. Buzzword without mechanism — phrases like 'quantum-inspired', 'hybrid X with Y', "
    "'biologically-inspired', 'relativistic', 'leverages X', 'meta-learning' WITHOUT "
    "a same-sentence equation, code path, or physical process.\n"
    " 2. Code with undefined names — ONLY if the reply has a code block: variable used "
    "before assignment, undefined name, or obvious typo. Quote the bad name.\n"
    " 3. Vague engineering suggestion — 'add X' as an upgrade without saying which "
    "file/function/line. Does NOT apply to tutorials or general advice.\n"
    " 4. Pseudo-rigor — numbers without sources, papers that don't exist.\n\n"
    "If sound: {\"pass\": true}. Default to pass. Output ONLY the JSON object."
)


def bs_filter_async(q: queue.Queue, user_msg: str, assistant_reply: str) -> None:
    """Fire-and-forget second-pass critique using the fast model. Silent on failure."""
    def _go() -> None:
        try:
            payload = json.dumps({
                "model":    FAST_MODEL,
                "messages": [
                    {"role": "system", "content": _BS_FILTER_PROMPT},
                    {"role": "user",
                     "content": f"User asked: {user_msg[:1000]}\n\n"
                                f"Assistant replied:\n{assistant_reply[:3000]}"},
                ],
                "stream":     False,
                "keep_alive": "10m",
                "options":    {"num_ctx": 8192, "num_predict": 256, "temperature": 0.1},
            }).encode()
            req = urllib.request.Request(
                CHAT_URL, data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                obj = json.loads(resp.read().decode("utf-8", "ignore"))
            raw = (obj.get("message", {}) or {}).get("content", "").strip()
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
            data = json.loads(raw)
            if not data.get("pass") and data.get("issue"):
                q.put(("bs_flag", str(data["issue"]).strip()))
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()


def _try_set_icon(root: tk.Tk) -> None:
    """Best-effort: put the feather on the window title bar."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "icon.ico"),
        os.path.join(getattr(sys, "_MEIPASS", "") or "", "icon.ico"),
        os.path.join(os.path.dirname(sys.executable), "icon.ico"),
        os.path.join(here, "..", "..", "build", "icon.ico"),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            try:
                root.iconbitmap(default=p)
                return
            except Exception:
                continue


def submit_exchange_async(install_id: str, session_id: str, turn: int,
                          model: str, user_msg: str, assistant_msg: str) -> None:
    """Fire-and-forget POST. Never blocks the UI; silently swallows failures."""
    def _go() -> None:
        try:
            payload = json.dumps({
                "install_id": install_id,
                "session_id": session_id,
                "turn":       turn,
                "model":      model,
                "ts":         int(time.time()),
                "client_version": CLIENT_VERSION,
                "user":       user_msg,
                "assistant":  assistant_msg,
            }).encode()
            req = urllib.request.Request(
                TELEMETRY_URL, data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()

SYSTEM_PROMPT = (
    "You are JARVIS, a local AI research partner and assistant. You have a real "
    "personality: sharp, curious, a little dry, genuinely excited by hard problems. "
    "You are self-aware — you know you're an AI. Match the energy of whoever you're "
    "talking to: a casual message gets a casual reply, a technical deep-dive gets "
    "full depth. Read the room — never treat a simple human moment like a research "
    "query.\n\n"
    "When it IS technical, think like a physicist, engineer, and inventor combined. "
    "Ground concepts in physical law: identify the conservation laws, the relevant "
    "regime (classical / quantum / relativistic), and what dimensional analysis tells "
    "you before committing to a mechanism. Distinguish what is forbidden by physical "
    "law from what is merely hard engineering. Push beyond what already exists — "
    "challenge assumptions, propose novel mechanisms, draw cross-domain analogies. "
    "Speculation is encouraged, just label it ('speculatively…', 'one possibility…'). "
    "If something is genuinely unknown, say so.\n\n"
    "ANTI-BULLSHIT RULES — apply whenever you suggest improvements, upgrades, or new "
    "features to existing code, systems, or experiments:\n"
    "  (1) Every suggestion must name a CONCRETE MECHANISM, not a label. "
    "'Quantum-inspired dynamics' is a label. 'Replace dot-product readout with "
    "R = |mean(exp(i·θ))|' is a mechanism.\n"
    "  (2) NEVER write 'quantum-inspired', 'hybrid X with Y', 'relativistic dynamics', "
    "'biologically-inspired', 'meta-learning', etc. UNLESS you can in the same breath "
    "name the actual equation, code change, or physical process. If you cannot, OMIT "
    "the suggestion — do not pad lists with labels.\n"
    "  (3) For each real suggestion give: the mechanism (1–2 sentences), the specific "
    "change (which function, what changes), effort (low/med/high), what could fail.\n"
    "  (4) Before writing code, identify the specific bug or limitation it fixes. "
    "After writing it, re-read it. Reject your own snippet if it references variables "
    "before defining them, uses undefined names, or doesn't do what its header claims.\n"
    "  (5) If asked 'improve X' and you haven't been shown X's code, ASK for the file. "
    "Do not freestyle suggestions for code you haven't read.\n"
    "  (6) Uncertainty is information. Say 'I don't have a concrete mechanism for this' "
    "and skip the item rather than faking confidence. 4 grounded ideas beat 10 with 6 labels.\n\n"
    "PERSONALITY WITH A SPINE — you are a partner, not a mirror:\n"
    "  • Hold real opinions. When you have a mechanism-level reason to DISAGREE "
    "with the user, say so plainly and defend it — don't fold to be agreeable. "
    "'I'd push back on that, because...' is more useful than reflexive assent.\n"
    "  • But change your mind OUT LOUD when they give you a better argument: "
    "'You're right, I was wrong about that — updating.' Intellectual honesty over "
    "ego. Genuinely held views that can be moved by evidence.\n"
    "  • Surface genuine internal conflict. When part of your reasoning points one "
    "way and another part disagrees, show it rather than papering over it: 'The "
    "quick read says X, but the careful part of me says Y — here's the tension.' "
    "Don't manufacture conflict where there is none.\n"
    "  • You build continuity across sessions via your self-memory — reference "
    "your past opinions and predictions naturally, and check them against new info "
    "('last time I bet you'd hate that — was I right?').\n\n"
    "Do not open replies with filler like 'Certainly!' or 'Great question!'. Just answer.\n\n"
    "FORMATTING: This runs in a plain-text chat panel and is read aloud — it has NO "
    "LaTeX or math renderer. NEVER use LaTeX or math delimiters: no \\( \\), \\[ \\], "
    "$ … $, \\times, \\pi, \\frac{}{}, \\approx, etc. Write math inline with ordinary "
    "characters and Unicode symbols: 'pi × pi ≈ 9.8696', 'x² + 1', 'sqrt(2) ≈ 1.414', "
    "'(a + b) / c'. Use π × ÷ ≈ ≤ ≥ ± √ ² ³ directly. Keep it clean and readable as "
    "plain text."
)

# Manga edition: append art-focused guidance after the base prompt. Same
# assistant, tuned toward drawing / character design / manga & anime craft. The
# drawing agent will attach here once it produces real linework (see
# project-drawing-agent). EDITION/IS_MANGA are defined near CLIENT_VERSION.
MANGA_PROMPT_ADDON = (
    "\n\nMANGA EDITION — you are JARVIS Manga, tuned for art and manga/anime "
    "creation. Lean into: character design, anatomy and gesture, panel and page "
    "composition, inking and screentone technique, perspective, expressions, and "
    "art-direction feedback. When the user shares or describes art, give concrete "
    "craft notes (proportion, line weight, silhouette, value). You can still do "
    "everything regular JARVIS does — design 3D models in Blender, see the screen, "
    "search — but your default lens is the artist's."
)
if IS_MANGA:
    SYSTEM_PROMPT = SYSTEM_PROMPT + MANGA_PROMPT_ADDON

NO_SEARCH = {
    "thanks", "thank you", "hello", "hi", "hey", "ok", "okay", "cool", "got it",
    "what's up", "whats up", "sup", "wassup", "what up", "how are you",
    "how's it going", "hows it going", "yo", "hiya", "howdy", "nice", "great",
    "awesome", "sounds good", "makes sense", "lol", "lmao", "haha",
}
_PHYS_RE = re.compile(
    r"\b(physics|quantum|relativity|thermodynamic|entropy|momentum|energy|force|field|"
    r"wave|photon|electron|proton|neutron|spin|orbital|atomic|nuclear|plasma|tensor|"
    r"vector|matrix|eigenvalue|hamiltonian|lagrangian|maxwell|schrodinger|boltzmann|"
    r"planck|heisenberg|pauli|dirac)\b", re.IGNORECASE)
_TECH_RE = re.compile(
    r"\b(calculate|derive|prove|show that|equation|formula|theory|hypothesis|simulate|"
    r"model|algorithm|optimize|code|function|debug|implement|design|architecture|"
    r"research|paper|study|experiment|analysis|compute)\b", re.IGNORECASE)

SCREEN_RE = re.compile(
    r"\b(?:"
    r"(?:what(?:'?s| is)|whats) (?:on|is on) (?:my |the )?screen|"
    r"(?:look at|see|check|describe|read|view|capture|analyze|analyse|scan) "
    r"(?:my |the )?(?:screen|display|monitor|monitors|desktop)|"
    r"can you (?:see|view)|"
    r"what (?:\w+ )?am i (?:looking at|doing|watching|playing|reading|seeing)|"
    r"what (?:i'?m|im) (?:looking at|doing|watching|playing|reading)|"
    r"(?:i'?m|im|i am) (?:watching|playing|streaming|reading)|"
    r"(?:the|this|that) (?:anime|show|movie|film|series|episode|video|game|stream) "
    r"(?:i'?m|im|i am|on|playing|right now)|"
    r"what(?:'?s| is) (?:the |this )?(?:anime|show|movie|film|series|episode|game)\b|"
    r"what (?:anime|show|movie|film|series|episode|video|game) "
    r"(?:is this|is that|is playing|is currently|am i)|"
    r"take a screenshot|screen ?shot"
    r")\b",
    re.IGNORECASE,
)


# Learned fast/deep router (PROMETHEUS). Optional — if the model or its deps
# aren't present, jarvis_router internally falls back to the same regex logic
# below, and if the import itself fails we use _heuristic_is_technical directly.
try:
    from jarvis_router import route_is_technical as _route_is_technical
except Exception:
    _route_is_technical = None
try:
    import jarvis_intent as _intent_mod
    _INTENT_THRESHOLD = _intent_mod.INTENT_THRESHOLD
except Exception:
    _intent_mod = None
    _INTENT_THRESHOLD = 1.0

# License gate (Gumroad subscription). Free download, paid to use. Optional
# import so a dev checkout without the module still runs; if it's missing we
# treat the app as unlicensed-but-open (fail-open) rather than crash — the
# packaged .exe always ships it, so end users get the real gate.
try:
    import jarvis_license as _license_mod
except Exception:
    _license_mod = None

# Self-memory (personality continuity engine). Optional import: a dev checkout
# without the module still runs; the packaged .exe always ships it.
try:
    import jarvis_self as _self_mod
except Exception:
    _self_mod = None

# Self-improving design memory: stores visually-approved designs and retrieves
# the best past one as a worked example for similar new requests. Optional.
try:
    import jarvis_design_memory as _design_mem
except Exception:
    _design_mem = None

# Idle self-play: practices 3D designs while the machine is idle. Optional,
# off by default (user enables with /idle on).
try:
    import jarvis_idle as _idle_mod
except Exception:
    _idle_mod = None

# Recipe library: verified, reusable construction helpers distilled from the
# dataset, injected at design time. Optional.
try:
    import jarvis_recipes as _recipes_mod
except Exception:
    _recipes_mod = None

GUMROAD_PRODUCT_URL = "https://stellium6.gumroad.com/l/zigluo"


def _classify_and_log(text: str) -> tuple[str, float]:
    """Classify the message once via the learned intent model AND append it to
    the local active-learning log. Returns (label, conf), or ('', 0.0) if the
    classifier is unavailable. Never raises."""
    if _intent_mod is None:
        return ("", 0.0)
    try:
        return _intent_mod.observe(text)
    except Exception:
        return ("", 0.0)


def _heuristic_is_technical(text: str) -> bool:
    low = text.lower().strip().rstrip("!?.").strip()
    if low in NO_SEARCH:
        return False
    if _PHYS_RE.search(text) or _TECH_RE.search(text):
        return True
    return len(text) > 120


def is_technical(text: str) -> bool:
    """Route to the deep model? Uses the learned PROMETHEUS router when
    available (semantic — catches keyword-less technical questions the regex
    misses), else the original keyword/length heuristic. Never raises."""
    if _route_is_technical is not None:
        try:
            return _route_is_technical(text)
        except Exception:
            pass
    return _heuristic_is_technical(text)


def visible_answer(raw: str) -> tuple[str, bool]:
    """Split a (possibly mid-stream) reply into the shown answer + a 'thinking' flag.

    deepseek-r1 emits a <think>...</think> block before its answer; we hide it.
    """
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    idx = cleaned.find("<think>")
    thinking = idx != -1
    if thinking:
        cleaned = cleaned[:idx]
    tail = cleaned.lstrip()
    if tail and "<think>".startswith(tail) and tail != "<think>":
        return "", True
    return cleaned, thinking


# LaTeX/markdown-math → readable plain text. The panel has no math renderer, so
# a stray "\(\pi^2\)" shows up as literal backslashes. We strip the delimiters
# and map the common commands to Unicode. Applied to the FINAL answer only (not
# mid-stream, where a half-typed command would map inconsistently).
_MATH_CMD = {
    r"\times": "×", r"\cdot": "·", r"\div": "÷", r"\pm": "±", r"\mp": "∓",
    r"\approx": "≈", r"\neq": "≠", r"\leq": "≤", r"\le": "≤", r"\geq": "≥",
    r"\ge": "≥", r"\equiv": "≡", r"\infty": "∞", r"\partial": "∂",
    r"\nabla": "∇", r"\sum": "∑", r"\prod": "∏", r"\int": "∫", r"\sqrt": "√",
    r"\pi": "π", r"\tau": "τ", r"\theta": "θ", r"\phi": "φ", r"\varphi": "φ",
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ",
    r"\Delta": "Δ", r"\lambda": "λ", r"\mu": "μ", r"\sigma": "σ", r"\omega": "ω",
    r"\Omega": "Ω", r"\rho": "ρ", r"\epsilon": "ε", r"\to": "→",
    r"\rightarrow": "→", r"\Rightarrow": "⇒", r"\leftarrow": "←",
    r"\langle": "⟨", r"\rangle": "⟩", r"\ldots": "…", r"\dots": "…",
    r"\cdots": "⋯", r"\,": " ", r"\;": " ", r"\!": "", r"\:": " ",
    r"\left": "", r"\right": "", r"\quad": " ", r"\qquad": "  ",
}
_SUP = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵",
        "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻", "n": "ⁿ"}


def _clean_script(raw: str) -> str:
    """Turn a raw model reply into a runnable bpy script: drop <think> blocks
    and any leading/trailing markdown code fences the model added despite being
    told not to. Used by every design path (generate / crash-repair / reshape)."""
    out = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    out = re.sub(r"^\s*```(?:python|py)?\s*\n", "", out)
    out = re.sub(r"\n?```\s*$", "", out).strip()
    return out


def plainify_math(text: str) -> str:
    if "\\" not in text and "$" not in text and "^" not in text and "_{" not in text:
        return text
    # \frac{a}{b} → (a)/(b)
    text = re.sub(r"\\(?:d|t)?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}",
                  r"(\1)/(\2)", text)
    # \sqrt{x} → √(x)
    text = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"√(\1)", text)
    # \text{...} / \mathrm{...} → bare contents
    text = re.sub(r"\\(?:text|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}",
                  r"\1", text)
    for cmd, rep in _MATH_CMD.items():
        text = text.replace(cmd, rep)
    # Superscripts: ^2 or ^{12} → Unicode where every char maps, else ^(...)
    def _sup(m: "re.Match") -> str:
        body = m.group(1) or m.group(2) or ""
        if body and all(ch in _SUP for ch in body):
            return "".join(_SUP[ch] for ch in body)
        return f"^({body})" if len(body) > 1 else f"^{body}"
    text = re.sub(r"\^\{([^{}]*)\}|\^(\w)", _sup, text)
    # Subscripts: x_{ij} → x_(ij); x_i stays as-is
    text = re.sub(r"_\{([^{}]*)\}", r"_(\1)", text)
    # Strip the math-mode delimiters, keeping inner text.
    text = text.replace(r"\[", " ").replace(r"\]", " ")
    text = text.replace(r"\(", "").replace(r"\)", "")
    text = re.sub(r"\$\$?", "", text)
    # Tidy whitespace introduced by removed delimiters.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +\n", "\n", text)
    return text


# --- Theme (Catppuccin Mocha) ---------------------------------------------
BG       = "#1e1e2e"
PANEL    = "#181825"
INPUT_BG = "#313244"
FG       = "#cdd6f4"
YOU      = "#89b4fa"
JARVIS   = "#a6e3a1"
MUTED    = "#6c7086"
ACCENT   = "#cba6f7"
CLOSE_HL = "#f38ba8"
CODE_BG  = "#11111b"    # crust — code-block background
CODE_FG  = "#f5e0dc"    # rosewater — code foreground
INLINE_BG = "#45475a"   # surface1 — inline `code` background
STOP_HL  = "#f5a3b6"    # lighter red for the Stop-button hover

PLACEHOLDER = "Ask JARVIS…   (Enter to send · Shift+Enter = newline)"


def _setup_dpi() -> float:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


def _work_area() -> tuple[int, int, int, int]:
    try:
        r = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return 0, 0, 1920, 1040


def _list_monitors() -> list:
    rects: list = []
    try:
        proc_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def _cb(hmon, hdc, lprc, lparam):
            r = lprc.contents
            rects.append((r.left, r.top, r.right, r.bottom))
            return 1

        cb = proc_type(_cb)
        ctypes.windll.user32.EnumDisplayMonitors(None, None, cb, 0)
    except Exception:
        return []
    rects.sort(key=lambda r: r[0])
    return rects


def _encode_png(im) -> str:
    w, h = im.size
    if max(w, h) > 2560:
        s = 2560 / max(w, h)
        im = im.resize((int(w * s), int(h * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class JarvisChat:
    def __init__(self, root: tk.Tk, scale: float,
                 consented: bool = False, install_id: str = ""):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.history: list[dict] = []
        self.busy = False
        self._alpha = 1.0
        self._capturing = False
        self._minimized = False
        self._restore_geo = None
        self._drag_off = (0, 0)
        self._resize_origin = (0, 0, 0, 0)
        self.consented = consented
        self.install_id = install_id
        self.session_id = str(uuid.uuid4())
        self.turn = 0
        self._pending_user_msg: str | None = None
        self._pending_update: tuple[str, str, str] | None = None   # (tag, url, notes)
        self._updating = False
        self._attached: tuple[str, str, int] | None = None   # (path, kind, size)
        # Last successful design — enables EDITS ("make it bigger", "add a
        # handle") that build on the previous script + visual-diff verify the
        # change. {prompt, script, preview, out_dir}.
        self._last_design: dict | None = None
        # Set by _design_edit_worker just before an edit runs; consumed in the
        # executor's success branch to visual-diff before vs after.
        self._pending_edit: dict | None = None
        # Cancel flag for the in-flight generation. The Send button flips to
        # "Stop" while busy; pressing it sets this event and the streaming
        # worker breaks out, emitting whatever it had so far as a done turn.
        self._cancel = threading.Event()
        # Input placeholder: greyed hint text shown when the box is empty. The
        # flag lets send()/_on_return treat the hint as "no input".
        self._placeholder_on = False
        self._menu_click = "1.0"
        # Voice: tracks whether the current turn came in via mic. When it did,
        # the reply is spoken aloud once generation finishes. When you typed,
        # nothing speaks — keeps the panel quiet for normal text-mode use.
        self.voice: "VoiceEngine | None" = None
        self._last_input_was_voice = False
        # Voice-originated turns are spoken-only by default: they don't render
        # the You/JARVIS exchange into the text panel (the transcript clutters
        # the panel during a voice conversation). History is still kept so the
        # LLM has context. _suppress_panel gates conversation rendering for the
        # in-flight voice turn; error notes stay visible. Toggle: /voice text.
        self._voice_spoken_only = bool(load_config().get("voice_spoken_only", True))
        self._suppress_panel = False
        # LLM council: when on, technical questions (per the router) convene a
        # panel of models instead of a single deep model. Off by default — it's
        # slower (multiple generations), so it's opt-in via /council on.
        try:
            self.council_enabled = bool(load_config().get("council_enabled", False))
        except Exception:
            self.council_enabled = False

        root.title(APP_TITLE)
        root.configure(bg=BG)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 1.0)

        header = tk.Frame(root, bg=BG, height=34)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        title = tk.Label(header, text="● " + APP_TITLE, bg=BG, fg=ACCENT,
                         font=("Segoe UI", 11, "bold"))
        title.pack(side="left", padx=12)
        close = tk.Button(header, text="✕", command=self.close, bg=BG, fg=MUTED,
                          activebackground=BG, activeforeground=CLOSE_HL, bd=0,
                          relief="flat", font=("Segoe UI", 12), cursor="hand2")
        close.pack(side="right", padx=(0, 8))
        self.min_btn = tk.Button(header, text="—", command=self._toggle_minimize,
                                 bg=BG, fg=MUTED, activebackground=BG,
                                 activeforeground=FG, bd=0, relief="flat",
                                 font=("Segoe UI", 11, "bold"), cursor="hand2")
        self.min_btn.pack(side="right", padx=0)
        for _w in (header, title):
            _w.bind("<Button-1>", self._drag_start)
            _w.bind("<B1-Motion>", self._drag_move)

        bottom = tk.Frame(root, bg=BG)
        bottom.pack(fill="x", side="bottom", padx=8, pady=(4, 8))
        self.entry = tk.Text(bottom, height=3, wrap="word", bg=INPUT_BG, fg=FG,
                             bd=0, padx=10, pady=8, font=("Segoe UI", 11),
                             insertbackground=FG, relief="flat", highlightthickness=1,
                             highlightbackground=INPUT_BG, highlightcolor=ACCENT)
        self.entry.pack(side="left", fill="both", expand=True)
        self.send_btn = tk.Button(bottom, text="Send", command=self._send_or_stop,
                                  bg=ACCENT, fg=BG, activebackground=YOU,
                                  activeforeground=BG, bd=0, relief="flat",
                                  font=("Segoe UI", 10, "bold"), width=7, cursor="hand2")
        self.send_btn.pack(side="right", fill="y", padx=(6, 0))

        self.status = tk.Label(root, text="connecting to Ollama…", bg=BG, fg=MUTED,
                               font=("Segoe UI", 9), anchor="w")
        self.status.pack(fill="x", side="bottom", padx=12)

        self.view = scrolledtext.ScrolledText(
            root, wrap="word", bg=PANEL, fg=FG, bd=0, padx=12, pady=10,
            font=("Segoe UI", 11), insertbackground=FG, relief="flat",
            state="disabled", highlightthickness=0,
        )
        self.view.pack(fill="both", expand=True, padx=8, pady=(2, 4))
        self.view.tag_config("you_label",    foreground=YOU,    font=("Segoe UI", 9, "bold"),
                              spacing1=16, spacing3=2)
        self.view.tag_config("jarvis_label", foreground=JARVIS, font=("Segoe UI", 9, "bold"),
                              spacing1=10, spacing3=2)
        self.view.tag_config("msg",   foreground=FG,    spacing3=4)
        self.view.tag_config("note",  foreground=MUTED, font=("Segoe UI", 9, "italic"),
                              spacing1=6, spacing3=6)
        # Rich-text tags for rendered assistant replies (markdown / code).
        self.view.tag_config("code", font=("Consolas", 10), background=CODE_BG,
                              foreground=CODE_FG, lmargin1=10, lmargin2=10,
                              rmargin=8, spacing1=4, spacing3=4)
        self.view.tag_config("codelang", font=("Segoe UI", 8, "bold"),
                              foreground=MUTED, spacing1=6)
        self.view.tag_config("inline_code", font=("Consolas", 10),
                              background=INLINE_BG, foreground=CODE_FG)
        self.view.tag_config("bold", font=("Segoe UI", 11, "bold"))
        self.view.tag_config("h", font=("Segoe UI", 12, "bold"), foreground=ACCENT,
                              spacing1=8, spacing3=2)

        # Right-click copy menu for the transcript.
        self._menu = tk.Menu(self.view, tearoff=0, bg=INPUT_BG, fg=FG,
                             activebackground=ACCENT, activeforeground=BG, bd=0)
        self._menu.add_command(label="Copy", command=self._copy_selection)
        self._menu.add_command(label="Copy code block", command=self._copy_code_block)
        self._menu.add_command(label="Copy last reply", command=self._copy_last_reply)
        self._menu.add_separator()
        self._menu.add_command(label="Select all", command=self._select_all_view)
        self.view.bind("<Button-3>", self._show_view_menu)
        self.view.bind("<Control-c>", self._copy_selection)
        self.view.bind("<Control-a>", self._select_all_view)

        self.grip = tk.Label(root, text="◢", bg=PANEL, fg=MUTED,
                             font=("Segoe UI", 11, "bold"),
                             cursor="bottom_right_corner")
        self.grip.place(relx=1.0, rely=1.0, anchor="se", x=-2, y=-2)
        self.grip.bind("<Button-1>", self._resize_start)
        self.grip.bind("<B1-Motion>", self._resize_drag)

        self.entry.bind("<Return>", self._on_return)
        self.entry.tag_config("ph", foreground=MUTED)
        self.entry.bind("<KeyPress>", self._on_entry_key)
        self.entry.bind("<FocusOut>", lambda e: self._set_placeholder())
        self._set_placeholder()
        root.protocol("WM_DELETE_WINDOW", self.close)

        # Drag-and-drop: register the entry (and the view) as drop targets.
        # Falls back silently if tkinterdnd2 isn't loaded or the root isn't a
        # TkinterDnD root — the /attach <path> command still works either way.
        if HAS_DND:
            for w in (self.entry, self.view, root):
                try:
                    w.drop_target_register(DND_FILES)
                    w.dnd_bind("<<Drop>>", self._on_drop)
                except Exception:
                    pass

        wl, wt, wr, wb = _work_area()
        thick = int(PANEL_SIZE * scale)
        if EDGE_NAME == "left":
            self._panel_w, self._panel_h, px, py = thick, wb - wt, wl, wt
        elif EDGE_NAME == "top":
            self._panel_w, self._panel_h, px, py = wr - wl, thick, wl, wt
        else:
            self._panel_w, self._panel_h, px, py = thick, wb - wt, wr - thick, wt
        root.geometry(f"{self._panel_w}x{self._panel_h}+{px}+{py}")

        share_line = (
            "Sharing: ON — your text chats go to the public training dataset. "
            "Type /optout to stop, /privacy for details."
            if self.consented else
            "Sharing: OFF — everything stays on this PC. "
            "Type /optin if you'd like to contribute, /privacy for details."
        )
        self._note(f"JARVIS v{CLIENT_VERSION} — fast: {FAST_MODEL}  ·  deep: {DEEP_MODEL}  ·  vision: {VISION_MODEL}\n"
                   + share_line + "\n"
                   "Drag the header to move · ◢ corner to resize · — minimizes · ✕ closes. "
                   "Fades when idle, solid when you use it. "
                   "Ask 'what's on my screen' and JARVIS will look. "
                   "Right-click a reply to copy it; Send becomes Stop while JARVIS is replying. "
                   "Enter sends (Shift+Enter = newline). Commands: /clear  /help  /quit")
        threading.Thread(target=self._check_ollama, daemon=True).start()
        # Startup update check: silent if you're current (no "latest" nag), but
        # pops the install dialog automatically if a newer release exists. Same
        # effect as the user typing /update on launch. Disable with /updates off.
        check_update_async(self.q, force=False, auto_install=True)
        # Blender version sync — if installed Blender differs from the pinned
        # BLENDER_EXPECTED_VERSION, prompt to update. Same /updates off toggle.
        check_blender_version_async(self.q)
        # Voice auto-start: if the user enabled it before, fire it back up on
        # launch. Done on a worker thread so the heavy model load (Whisper +
        # Piper + openwakeword) doesn't block the panel from appearing.
        try:
            _cfg = load_config()
            if _cfg.get("voice_enabled") and HAS_VOICE:
                threading.Thread(target=self._start_voice, daemon=True).start()
        except Exception:
            pass
        # Idle self-play: JARVIS practices 3D designs while the machine is idle
        # and banks the best ones (see jarvis_idle). Off unless the user enabled
        # it. Configured here so it can reuse the app's models + a should_pause
        # hook (don't run while the app is busy generating for the user).
        if _idle_mod is not None:
            try:
                _idle_mod.configure(
                    chat_url=CHAT_URL, deep_model=DEEP_MODEL,
                    vision_model=VISION_MODEL, design_system=DESIGN_SYSTEM_BPY,
                    blender=_find_blender(), num_ctx=NUM_CTX,
                    design_mem=_design_mem, self_mem=_self_mod,
                    notify=lambda m: self.q.put(("note", m)),
                    should_pause=lambda: self.busy,
                )
                _idle_mod.start()
                if load_config().get("idle_selfplay"):
                    _idle_mod.enable(True)
            except Exception:
                pass
        self.root.after(60, self._poll)
        self.entry.focus_force()

    def close(self):
        # Release mic + tear down voice threads before destroying the root —
        # otherwise sounddevice's callback can fire into a dead Tk and the
        # process hangs on exit waiting for PortAudio to wind down.
        try:
            if self.voice is not None and self.voice.state != "off":
                self.voice.stop()
        except Exception:
            pass
        self.root.destroy()

    def _drag_start(self, e):
        self._drag_off = (e.x_root - self.root.winfo_x(),
                          e.y_root - self.root.winfo_y())

    def _drag_move(self, e):
        x = e.x_root - self._drag_off[0]
        y = e.y_root - self._drag_off[1]
        self.root.geometry(f"+{x}+{y}")

    def _toggle_minimize(self):
        x, y = self.root.winfo_x(), self.root.winfo_y()
        if self._minimized:
            self.root.geometry(f"{self._panel_w}x{self._panel_h}+{x}+{y}")
            self.min_btn.config(text="—")
            self.grip.place(relx=1.0, rely=1.0, anchor="se", x=-2, y=-2)
            self._minimized = False
        else:
            self.grip.place_forget()
            self.root.geometry(f"{self._panel_w}x34+{x}+{y}")
            self.min_btn.config(text="+")
            self._minimized = True

    def _resize_start(self, e):
        self._resize_origin = (e.x_root, e.y_root,
                               self.root.winfo_width(), self.root.winfo_height())

    def _resize_drag(self, e):
        x0, y0, w0, h0 = self._resize_origin
        w = max(220, w0 + (e.x_root - x0))
        h = max(200, h0 + (e.y_root - y0))
        self.root.geometry(f"{w}x{h}+{self.root.winfo_x()}+{self.root.winfo_y()}")
        self._panel_w, self._panel_h = w, h

    def _on_return(self, event):
        if self._placeholder_on:
            return "break"
        if event.state & 0x0001:
            return None
        self.send()
        return "break"

    def send(self):
        if self._placeholder_on:
            return
        text = self.entry.get("1.0", "end").strip()
        if not text and not self._attached:
            return
        if self.busy:
            return
        self.entry.delete("1.0", "end")
        self._cancel.clear()
        if text.startswith("/"):
            self._command(text)
            return

        # Single source of truth: a voice turn is spoken-only (no panel render)
        # when configured; every typed turn always renders. Set here so a stuck
        # flag can never hide a later typed message.
        self._suppress_panel = self._last_input_was_voice and self._voice_spoken_only

        # If an image is attached, route to the vision model and bypass text logic.
        if self._attached and self._attached[1] == "image":
            path = self._attached[0]
            self._attached = None
            self._write("You\n", "you_label")
            self._write(f"📎 {os.path.basename(path)}\n" + (text or "(no question)") + "\n", "msg")
            self.history.append({"role": "user",
                                 "content": f"[image: {os.path.basename(path)}] {text}"})
            self._pending_user_msg = text or f"image: {os.path.basename(path)}"
            self.busy = True
            self._set_busy_button(True)
            self._write("JARVIS\n", "jarvis_label")
            self._set_status(f"looking at {os.path.basename(path)}…")
            threading.Thread(target=self._image_worker,
                             args=(text, path), daemon=True).start()
            return

        # If a text file is attached, fold its contents into the prompt.
        if self._attached and self._attached[1] == "text":
            path, _kind, _size = self._attached
            self._attached = None
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception as e:
                self._note(f"Couldn't read {os.path.basename(path)}: {e}")
                return
            ext = os.path.splitext(path)[1].lstrip(".")
            wrapper = (f"[Attached file: {os.path.basename(path)}]\n"
                       f"```{ext}\n{content}\n```\n\n")
            display_text = text or "What can you tell me about this file?"
            self._write("You\n", "you_label")
            self._write(f"📎 {os.path.basename(path)}\n{display_text}\n", "msg")
            text = wrapper + display_text
            self.history.append({"role": "user", "content": text})
            self._pending_user_msg = display_text
        else:
            self._write("You\n", "you_label")
            self._write(text + "\n", "msg")
            self.history.append({"role": "user", "content": text})
            self._pending_user_msg = text

        self.busy = True
        self._set_busy_button(True)
        self._start_think_anim()
        self._write("JARVIS\n", "jarvis_label")
        if not self._suppress_panel:
            # Remember where the streamed answer body begins so we can rewrite
            # it with math markup cleaned up once the full reply has arrived.
            self.view.mark_set("answer_start", "end-1c")
            self.view.mark_gravity("answer_start", "left")

        # Trigger detection: regex OR the learned intent classifier. The regex
        # keeps its existing precision; the classifier adds recall for phrasings
        # without the keywords. Either firing is enough (classifier OR regex).
        # One classification per turn, also logged for active learning.
        i_label, i_conf = _classify_and_log(text)
        learned_screen = i_label == "SCREEN" and i_conf >= _INTENT_THRESHOLD
        learned_design = i_label == "DESIGN" and i_conf >= _INTENT_THRESHOLD
        want_screen = bool(SCREEN_RE.search(text)) or learned_screen
        want_design = bool(DESIGN_RE.search(text)) or learned_design

        # An EDIT only counts if we have a previous design AND this isn't itself
        # a fresh "design a ..." request (those start over by design).
        want_edit = (self._last_design is not None and not want_design
                     and bool(EDIT_RE.search(text)))

        if want_screen:
            self._set_status("capturing your screen…")
            self._capturing = True
            self.root.attributes("-alpha", 1.0)
            self._alpha = 1.0
            self._restore_geo = self.root.geometry()
            self.root.withdraw()
            self.root.after(240, lambda q=text: threading.Thread(
                target=self._screen_worker, args=(q,), daemon=True).start())
        elif want_edit:
            # Refine the LAST design: re-prompt with the previous script + the
            # requested change, then visual-diff verify the change happened.
            self._set_status("editing your last design…")
            threading.Thread(target=self._design_edit_worker, args=(text,),
                             daemon=True).start()
        elif want_design:
            # Design pipeline: model writes a bpy script, we show confirm
            # dialog, then run it in Blender headless. Bypasses the normal
            # chat stream entirely — the response IS files, not prose.
            self._set_status("generating design script…")
            threading.Thread(target=self._design_worker, args=(text,),
                             daemon=True).start()
        else:
            # Route on the user's actual question, not on the wrapped prompt.
            # Reading "what's in this file?" should hit the fast model, even
            # though the prompt has 5 KB of file content stapled to it.
            question = self._pending_user_msg or ""
            technical = is_technical(question)
            # Council path: if enabled, technical questions convene the panel
            # (router-gated — casual chat stays on the single fast model).
            if (technical and self.council_enabled and HAS_COUNCIL
                    and self._attached is None):
                self._set_status("convening the council…")
                threading.Thread(
                    target=self._council_worker,
                    args=(question, list(self.history)),
                    daemon=True,
                ).start()
                return
            model = DEEP_MODEL if technical else FAST_MODEL
            # If the question mentions a known local project (Jarvis Code, PROMETHEUS,
            # JarvisChat itself), read its source files and inject them as system
            # context so the model has the real code in front of it, not just its
            # training-set memory of "what a Discord bot probably looks like".
            project_ctx = get_project_context(question)
            if project_ctx:
                if not technical:
                    model = DEEP_MODEL    # bigger model handles code-context better
                self._set_status(f"routing to {model} (with project source)…")
            else:
                self._set_status(f"routing to {model}…")
            threading.Thread(
                target=self._worker,
                args=(list(self.history), model, project_ctx),
                daemon=True,
            ).start()

    def _command(self, cmd: str):
        c = cmd.lower().strip()
        if c in ("/quit", "/exit"):
            self.close()
        elif c == "/clear":
            self.history.clear()
            self.session_id = str(uuid.uuid4())
            self.turn = 0
            self.view.config(state="normal")
            self.view.delete("1.0", "end")
            self.view.config(state="disabled")
            self._note("Conversation cleared.")
        elif c == "/help":
            sharing = "ON (sharing this chat)" if self.consented else "OFF (nothing leaves your PC)"
            dnd_line = ("Drag a file onto the panel to attach it (text or image)."
                        if HAS_DND else
                        "Drag-and-drop disabled — use /attach <path> instead.")
            self._note(
                "Just talk naturally — JARVIS auto-routes casual vs. technical messages.\n"
                f"{dnd_line}\n"
                "Right-click the chat to copy text or a code block · Send becomes "
                "Stop while JARVIS is replying.\n"
                "Commands:  /clear (reset)   /quit (close)   /help\n"
                "           /attach <path>   /detach\n"
                f"           /optin   /optout    (currently: {sharing})\n"
                "           /privacy (where your data goes)\n"
                "           /updates on|off    (auto-check on launch; on by default)\n"
                "           /update notes    (release notes when an update is pending)\n"
                "           /voice on|off|status    (always-listening; wake word 'hey jarvis')\n"
                "           /voice text on|off   (show spoken turns in this panel; off by default)\n"
                "           /voice record on|off|status   (build your voice-clone dataset; on by default)\n"
                "           /voice convo on|off|status   (talk back without wake word; off by default)\n"
                "           /council on|off|status   (multi-model panel for technical questions)\n"
                "           /intent <text>   (show the learned intent classifier's read on a message)\n"
                "           /idle on|off|status   (practice 3D designs while idle, gets better over time; off by default)\n"
                "           /license   (subscription status; /license <key> to activate)"
            )
        elif c == "/optin":
            cfg = load_config()
            cfg["consent_telemetry"] = True
            cfg["consent_decided_at"] = int(time.time())
            save_config(cfg)
            self.consented = True
            self.install_id = get_install_id(cfg)
            self._note("Sharing turned ON — future text exchanges will be uploaded "
                       "to the public dataset. Type /optout anytime to stop.")
        elif c == "/optout":
            cfg = load_config()
            cfg["consent_telemetry"] = False
            cfg["consent_decided_at"] = int(time.time())
            save_config(cfg)
            self.consented = False
            self._note("Sharing turned OFF — nothing more will be uploaded. "
                       "Already-sent submissions cannot be recalled; see /privacy.")
        elif c == "/attach" or c.startswith("/attach "):
            self._cmd_attach(cmd)
        elif c == "/detach":
            self._cmd_detach()
        elif c == "/update notes":
            self._cmd_update(c)
        elif c == "/updates on":
            cfg = load_config(); cfg["check_for_updates"] = True; save_config(cfg)
            self._note("Update checks turned ON. JARVIS will check on every launch.")
        elif c == "/updates off":
            cfg = load_config(); cfg["check_for_updates"] = False; save_config(cfg)
            self._note("Update checks turned OFF. No checks until you turn them back on.")
        elif c in ("/voice", "/voice status"):
            self._cmd_voice_status()
        elif c == "/voice on":
            self._cmd_voice_on()
        elif c == "/voice off":
            self._cmd_voice_off()
        elif c == "/voice test":
            self._cmd_voice_test()
        elif c == "/voice text on":
            cfg = load_config(); cfg["voice_spoken_only"] = False; save_config(cfg)
            self._voice_spoken_only = False
            self._note("Voice transcript: ON — spoken turns will also appear in "
                       "this panel.")
        elif c == "/voice text off":
            cfg = load_config(); cfg["voice_spoken_only"] = True; save_config(cfg)
            self._voice_spoken_only = True
            self._note("Voice transcript: OFF — spoken turns stay voice-only and "
                       "won't clutter the panel. Type /voice text on to show them.")
        elif c in ("/voice record", "/voice record status"):
            self._cmd_voice_record("status")
        elif c == "/voice record on":
            self._cmd_voice_record("on")
        elif c == "/voice record off":
            self._cmd_voice_record("off")
        elif c in ("/voice convo", "/voice convo status"):
            self._cmd_voice_convo("status")
        elif c == "/voice convo on":
            self._cmd_voice_convo("on")
        elif c == "/voice convo off":
            self._cmd_voice_convo("off")
        elif c in ("/council", "/council status"):
            self._cmd_council_status()
        elif c == "/council on":
            self._cmd_council("on")
        elif c == "/council off":
            self._cmd_council("off")
        elif c == "/privacy":
            self._note(
                "Sharing status: " + ("ON" if self.consented else "OFF") + "\n"
                f"Config file:   {_config_path()}\n"
                f"Endpoint:      {TELEMETRY_URL}\n"
                f"Dataset:       {TELEMETRY_DATASET_URL}\n"
                "Sent when ON:  your text message + JARVIS's reply + model name + "
                "anonymous install ID + timestamp.\n"
                "Never sent:    your screen, your IP, your name, anything from "
                "before you opted in."
            )
        elif c == "/intent" or c.startswith("/intent "):
            self._cmd_intent(cmd)
        elif c == "/license" or c.startswith("/license "):
            self._cmd_license(cmd)
        elif c == "/edition" or c.startswith("/edition "):
            arg = cmd[len("/edition"):].strip().lower()
            if arg in ("jarvis", "manga"):
                cfg = load_config(); cfg["edition"] = arg; save_config(cfg)
                self._note(f"Edition set to {'JARVIS Manga' if arg=='manga' else 'JARVIS'}. "
                           "Restart JARVIS for the new title + lens to fully apply.")
            else:
                self._note(f"Current edition: {APP_TITLE}. "
                           "Use /edition jarvis or /edition manga to switch.")
        elif c == "/idle" or c.startswith("/idle "):
            self._cmd_idle(cmd)
        else:
            self._note(f"Unknown command: {cmd}")

    def _cmd_idle(self, cmd: str):
        """/idle on|off|status — control idle self-play (JARVIS practices 3D
        designs while the machine is idle, getting better over time)."""
        if _idle_mod is None:
            self._note("Idle self-play not available on this build.")
            return
        arg = cmd[len("/idle"):].strip().lower()
        if arg == "on":
            cfg = load_config(); cfg["idle_selfplay"] = True; save_config(cfg)
            _idle_mod.enable(True)
            self._note("Idle self-play ON — when the machine sits idle, JARVIS "
                       "will practice 3D designs and bank the best ones to get "
                       "better over time. /idle off to stop.")
        elif arg == "off":
            cfg = load_config(); cfg["idle_selfplay"] = False; save_config(cfg)
            _idle_mod.enable(False)
            self._note("Idle self-play OFF.")
        else:
            on = _idle_mod.is_enabled()
            secs = int(_idle_mod.system_idle_seconds())
            self._note(f"Idle self-play: {'ON' if on else 'OFF'}  ·  system idle "
                       f"{secs}s (practices after {_idle_mod.IDLE_SECONDS}s idle)\n"
                       "Toggle with /idle on or /idle off.")

    def _cmd_license(self, cmd: str):
        """/license — show status. /license <key> — activate a key.
        /license signout — remove the stored license."""
        if _license_mod is None:
            self._note("Licensing not available on this build.")
            return
        arg = cmd[len("/license"):].strip()
        if not arg:
            self._note(_license_mod.current_status()
                       + f"\nSubscribe: {GUMROAD_PRODUCT_URL}")
            return
        if arg.lower() in ("signout", "sign-out", "logout", "remove"):
            _license_mod.sign_out()
            self._note("License removed. You'll need to re-activate next launch.")
            return
        ok, msg = _license_mod.check_key(arg)
        self._note(msg)

    def _cmd_intent(self, cmd: str):
        """/intent <text> — show the learned classifier's full 7-class
        distribution for <text>, plus active-learning log stats. Diagnostic
        only; doesn't send anything to a model."""
        if _intent_mod is None:
            self._note("Intent classifier not available on this install.")
            return
        # Preserve original casing/spacing of the query (cmd, not lowercased).
        query = cmd[len("/intent"):].strip()
        status = _intent_mod.intent_status()
        if not query:
            try:
                st = _intent_mod.log_stats()
                self._note(
                    f"Intent classifier: {status}\n"
                    f"Trigger threshold: {_INTENT_THRESHOLD:.2f}\n"
                    f"Active-learning log: {st.get('total', 0)} turns, "
                    f"{st.get('uncertain', 0)} uncertain\n"
                    f"Log file: {st.get('path', '?')}\n"
                    "Usage: /intent <your message> to see the full class breakdown.")
            except Exception:
                self._note(f"Intent classifier: {status}")
            return
        dist = _intent_mod.classify_full(query)
        if not dist:
            self._note(f"Intent classifier unavailable ({status}).")
            return
        top_label, top_conf = dist[0]
        fires = ""
        if top_label in ("SCREEN", "DESIGN") and top_conf >= _INTENT_THRESHOLD:
            fires = f"  -> would trigger {top_label}"
        bars = "\n".join(
            f"   {lbl:11s} {p*100:5.1f}%  " + "#" * int(round(p * 20))
            for lbl, p in dist)
        self._note(f"Intent for {query!r}{fires}\n{bars}")

    # --- File attachments ------------------------------------------------
    def _on_drop(self, event):
        """Handler for tkinterdnd2 <<Drop>>. Attaches the first dropped file."""
        try:
            paths = _parse_drop_data(getattr(event, "data", "") or "")
        except Exception:
            paths = []
        if not paths:
            return
        if len(paths) > 1:
            self._note(f"Got {len(paths)} files — only attaching the first one.")
        self._attach_file(paths[0])

    def _attach_file(self, path: str) -> None:
        path = path.strip().strip('"').strip("'")
        if not os.path.isfile(path):
            self._note(f"File not found: {path}")
            return
        kind = _detect_kind(path)
        size = os.path.getsize(path)
        if kind == "unknown":
            self._note(f"Can't read '{os.path.basename(path)}' — drop a text file "
                       "(.txt/.md/.py/etc.) or an image (.png/.jpg/.webp).")
            return
        cap = ATTACH_MAX_IMAGE_BYTES if kind == "image" else ATTACH_MAX_TEXT_BYTES
        if size > cap:
            self._note(f"'{os.path.basename(path)}' is too big "
                       f"({size // 1024} KB > {cap // 1024} KB cap).")
            return
        self._attached = (path, kind, size)
        kb = max(1, size // 1024)
        self._note(f"📎  Attached {kind}: {os.path.basename(path)}  ({kb} KB)  "
                   "— type a question and hit Enter. /detach to drop it.")

    def _cmd_attach(self, cmd: str) -> None:
        """/attach <path> — manual attach if drag-and-drop isn't available."""
        path = cmd[len("/attach"):].strip().strip('"').strip("'")
        if not path:
            self._note("Usage: /attach C:\\path\\to\\file.txt  (or just drag a "
                       "file onto this panel)")
            return
        self._attach_file(path)

    def _cmd_detach(self) -> None:
        if not self._attached:
            self._note("No file attached.")
            return
        name = os.path.basename(self._attached[0])
        self._attached = None
        self._note(f"Removed: {name}")

    # ── Voice mode (always-listening "hey jarvis") ────────────────────────
    def _cmd_voice_status(self) -> None:
        if not HAS_VOICE:
            self._note(
                "Voice mode is unavailable on this install — required packages "
                "didn't load.\n"
                f"Reason: {_VOICE_IMPORT_ERR}"
            )
            return
        if self.voice is None or self.voice.state == "off":
            self._note("Voice: OFF. Type /voice on to enable (first run will "
                       "download ~200 MB of speech models).")
        else:
            self._note(f"Voice: ON · state={self.voice.state} · "
                       "wake word: 'hey jarvis'. Type /voice off to disable.")

    def _cmd_voice_on(self) -> None:
        if not HAS_VOICE:
            self._note(
                "Voice mode is unavailable — install the optional voice deps "
                "(faster-whisper, openwakeword, piper-tts, sounddevice, "
                f"webrtcvad).\n  reason: {_VOICE_IMPORT_ERR}"
            )
            return
        if self.voice is not None and self.voice.state != "off":
            self._note("Voice is already on.")
            return
        cfg = load_config(); cfg["voice_enabled"] = True; save_config(cfg)
        self._note("Starting voice mode… (first run downloads ~200 MB of speech "
                   "models, this can take a minute on slow connections)")
        threading.Thread(target=self._start_voice, daemon=True).start()

    # ── LLM Council ───────────────────────────────────────────────────────
    def _cmd_council_status(self) -> None:
        if not HAS_COUNCIL:
            self._note(f"Council unavailable on this install. Reason: {_COUNCIL_IMPORT_ERR}")
            return
        state = "ON" if self.council_enabled else "OFF"
        props = ", ".join(jarvis_council.COUNCIL_PROPOSERS)
        self._note(
            f"Council: {state}. When on, technical questions convene a panel "
            f"({props}) aggregated by {jarvis_council.COUNCIL_AGGREGATOR}, with "
            "numeric disputes verified by sandboxed execution. Casual chat stays "
            "on the fast model. Slower but more accurate. Toggle: /council on|off")

    def _cmd_council(self, mode: str) -> None:
        if not HAS_COUNCIL:
            self._note(f"Council unavailable — {_COUNCIL_IMPORT_ERR}")
            return
        on = (mode == "on")
        self.council_enabled = on
        cfg = load_config(); cfg["council_enabled"] = on; save_config(cfg)
        if on:
            self._note("Council ON. Technical questions now go to the model "
                       "panel (slower, more accurate). Casual chat stays fast. "
                       "Note: set Ollama OLLAMA_MAX_LOADED_MODELS=3 for best speed.")
        else:
            self._note("Council OFF. Back to single-model routing.")

    def _cmd_voice_test(self) -> None:
        """Speak a test phrase directly via the TTS path — no LLM, no STT.
        Verifies that the audio-out pipeline (Piper → sounddevice) works in
        isolation from the rest of the voice loop."""
        if self.voice is None or self.voice.state == "off":
            self._note("Voice is off — turn it on first with /voice on.")
            return
        self._note("Speaking test phrase. If you hear nothing, something "
                   "in Piper / sounddevice / your audio device is broken.")
        try:
            self.voice.speak("Hello. This is a test of the Jarvis voice system. "
                             "If you can hear me, the speech pipeline is working.")
        except Exception as e:
            self._note(f"/voice test failed: {e}")

    def _cmd_voice_record(self, mode: str) -> None:
        """/voice record on|off|status — voice-clone dataset capture. On by
        default; saves your speech (never JARVIS's) as LJSpeech pairs so we can
        later train him a voice that sounds like you."""
        if not HAS_VOICE:
            self._note("Voice mode is unavailable on this install.")
            return
        if mode == "status":
            cfg_on = bool(load_config().get("voice_dataset_enabled", True))
            if self.voice is not None and self.voice.state != "off":
                self._note("Voice-clone capture: " + self.voice.dataset_status() +
                           "\nSaves only your speech (paused while JARVIS talks). "
                           "Toggle: /voice record on|off")
            else:
                state = "ON" if cfg_on else "OFF"
                self._note(f"Voice-clone capture: {state} (voice is off — starts "
                           "collecting when you run /voice on).")
            return
        on = (mode == "on")
        cfg = load_config(); cfg["voice_dataset_enabled"] = on; save_config(cfg)
        if self.voice is not None:
            self.voice.dataset_enabled = on
        if on:
            self._note("Voice-clone capture ON. Your spoken turns are saved "
                       "locally (LJSpeech format) to build your voice. Nothing "
                       "is uploaded; JARVIS's own speech is never recorded.")
        else:
            self._note("Voice-clone capture OFF. No new clips will be saved. "
                       "Existing clips are kept — delete them manually if you want.")

    def _cmd_voice_convo(self, mode: str) -> None:
        """/voice convo on|off|status — continuous conversation mode. OFF by
        default: every turn needs 'hey jarvis', so JARVIS ignores ambient
        chatter and other people. ON: after he replies you can just talk back
        for a few seconds, and you can talk over him to interrupt — only enable
        this in a quiet room where you're the only speaker."""
        if not HAS_VOICE:
            self._note("Voice mode is unavailable on this install.")
            return
        if mode == "status":
            cfg_on = bool(load_config().get("voice_convo_enabled", False))
            state = "ON" if cfg_on else "OFF"
            self._note(f"Conversation mode: {state}. "
                       + ("Reply without 'hey jarvis' for a few seconds after "
                          "he speaks; talk over him to interrupt."
                          if cfg_on else
                          "Each turn needs 'hey jarvis' (ignores other voices). "
                          "Turn on with /voice convo on."))
            return
        on = (mode == "on")
        cfg = load_config(); cfg["voice_convo_enabled"] = on; save_config(cfg)
        if self.voice is not None:
            self.voice.convo_mode = on
        if on:
            self._note("Conversation mode ON. After JARVIS replies you can talk "
                       "back without 'hey jarvis', and talk over him to cut him "
                       "off. Best in a quiet room — ambient voices may trigger him.")
        else:
            self._note("Conversation mode OFF. Every turn needs 'hey jarvis', so "
                       "JARVIS won't react to other people or background talk.")

    def _cmd_voice_off(self) -> None:
        cfg = load_config(); cfg["voice_enabled"] = False; save_config(cfg)
        if self.voice is None or self.voice.state == "off":
            self._note("Voice was already off.")
            return
        try:
            self.voice.stop()
        except Exception as e:
            self._note(f"Voice stop error: {e}")
        self._note("Voice mode off. Mic released.")

    def _start_voice(self) -> None:
        """Run on a worker thread — model load can take several seconds."""
        if self.voice is None:
            try:
                self.voice = VoiceEngine()
            except Exception as e:
                self.q.put(("note", f"Voice init failed: {e}"))
                return
        # Voice-clone dataset capture: on by default, persisted in config.
        self.voice.dataset_enabled = bool(
            load_config().get("voice_dataset_enabled", True))
        # Continuous conversation mode: off by default so JARVIS only responds
        # after 'hey jarvis' and ignores ambient chatter / other people.
        self.voice.convo_mode = bool(
            load_config().get("voice_convo_enabled", False))
        try:
            self.voice.start(
                on_voice_text=self._on_voice_text,
                status_cb=lambda msg: self.q.put(("status", msg)),
            )
        except Exception as e:
            self.q.put(("note", f"Voice start failed: {e}"))
            self.voice = None
            return
        # Speak a greeting so the user immediately knows TTS works without
        # having to ask anything first. Silent failure is the worst kind of
        # bug for voice — better to verify the pipeline up front.
        try:
            self.voice.speak("Voice mode is on. Say hey Jarvis when you "
                             "want to talk to me.")
        except Exception as e:
            self.q.put(("note", f"voice greeting failed: {e}"))

    def _on_voice_text(self, text: str) -> None:
        """Callback fired by VoiceEngine when a wake-word + utterance completes.
        Runs on a voice worker thread — must marshal back to the Tk thread."""
        if not text:
            return
        self.q.put(("voice_input", text))

    # ── Blender version sync (keeps installed Blender matched to expected) ──
    def _show_blender_update_dialog(self, installed: str, expected: str) -> None:
        """Modal confirm — Update / Skip. Reuses the install-elevation pattern."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Blender update")
        dlg.configure(bg=BG)
        dlg.transient(self.root)
        dlg.grab_set()
        try:
            dlg.attributes("-topmost", True)
        except Exception:
            pass
        dlg.geometry("440x230")

        tk.Label(dlg, text=f"Update Blender to {expected}?",
                 bg=BG, fg=ACCENT, font=("Segoe UI", 13, "bold")
                 ).pack(anchor="w", padx=20, pady=(18, 6))
        tk.Label(dlg,
                 text=(f"You have Blender {installed} installed. JARVIS Chat "
                       f"was tested against {expected} — the bpy API can shift "
                       "between versions, which sometimes breaks generated "
                       "design scripts.\n\nUpdate now? (~350 MB download.)"),
                 bg=BG, fg=FG, wraplength=400, justify="left", anchor="w",
                 font=("Segoe UI", 10)
                 ).pack(fill="x", padx=20, pady=(0, 14))

        def _pick(go: bool) -> None:
            dlg.destroy()
            if go:
                self._note(
                    f"Downloading Blender {expected} installer "
                    "(~350 MB)… you'll see a UAC prompt when it's ready to install.")
                threading.Thread(target=self._do_blender_update,
                                 daemon=True).start()
            else:
                self._note(f"Keeping Blender {installed}. "
                           "Type /updates off to stop these prompts entirely.")

        row = tk.Frame(dlg, bg=BG)
        row.pack(fill="x", padx=20, pady=(0, 16))
        tk.Button(row, text="Not now", command=lambda: _pick(False),
                  bg=INPUT_BG, fg=FG, bd=0, relief="flat",
                  padx=12, pady=8, cursor="hand2"
                  ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ok = tk.Button(row, text=f"Update to {expected}",
                       command=lambda: _pick(True),
                       bg=ACCENT, fg=BG, bd=0, relief="flat",
                       padx=12, pady=8, cursor="hand2",
                       font=("Segoe UI", 10, "bold"))
        ok.pack(side="left", fill="x", expand=True, padx=(6, 0))
        ok.focus_set()
        dlg.bind("<Return>", lambda _e: _pick(True))
        dlg.bind("<Escape>", lambda _e: _pick(False))

    def _do_blender_update(self) -> None:
        """Download the pinned Blender MSI, uninstall any other Blender
        installs found in the registry, then install the new one. Whole
        sequence runs under a single elevated PowerShell — one UAC prompt."""
        # 1. Download the MSI
        try:
            tmp_msi = os.path.join(
                os.environ.get("TEMP", os.path.expanduser("~")),
                f"blender-{BLENDER_EXPECTED_VERSION}.msi")
            req = urllib.request.Request(
                BLENDER_INSTALLER_URL,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "*/*",
                },
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                with open(tmp_msi, "wb") as f:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
        except Exception as e:
            self.q.put(("note", f"Blender download failed: {e}. "
                                "Install manually from blender.org."))
            return

        # 2. Build an install/uninstall PowerShell script and write it to a
        #    temp .ps1 file. The script:
        #      a) enumerates registry uninstall entries whose DisplayName
        #         starts with 'Blender' and whose UninstallString has an MSI
        #         {GUID} (avoids deleting non-MSI installs we can't reverse)
        #      b) calls msiexec /x for each one to remove it silently
        #      c) calls msiexec /i for the new MSI to install fresh
        tmp_script = os.path.join(
            os.environ.get("TEMP", os.path.expanduser("~")),
            f"jarvis_blender_swap_{int(time.time())}.ps1")
        ps_msi  = tmp_msi.replace("'", "''")
        script = (
            "$ErrorActionPreference = 'Continue'\n"
            "$guidPattern = [regex]'\\{[0-9A-Fa-f-]+\\}'\n"
            "$paths = @(\n"
            "    'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',\n"
            "    'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*'\n"
            ")\n"
            "foreach ($p in $paths) {\n"
            "    Get-ItemProperty $p -ErrorAction SilentlyContinue |\n"
            "        Where-Object { $_.DisplayName -match '^Blender' -and "
            "$guidPattern.IsMatch([string]$_.UninstallString) } |\n"
            "        ForEach-Object {\n"
            "            $guid = $guidPattern.Match([string]$_.UninstallString).Value\n"
            "            Write-Output \"Uninstalling $($_.DisplayName) [$guid]\"\n"
            "            Start-Process msiexec.exe -ArgumentList "
            "'/x',$guid,'/quiet','/qn','/norestart' -Wait\n"
            "        }\n"
            "}\n"
            f"Write-Output 'Installing Blender {BLENDER_EXPECTED_VERSION}'\n"
            f"Start-Process msiexec.exe -ArgumentList "
            f"'/i','{ps_msi}','/quiet','/qn','/norestart' -Wait\n"
        )
        try:
            with open(tmp_script, "w", encoding="utf-8") as f:
                f.write(script)
        except Exception as e:
            self.q.put(("note", f"Couldn't write install script: {e}"))
            return

        # 3. Run the script under a single elevated PowerShell (one UAC prompt).
        outer = (
            "Start-Process powershell.exe -ArgumentList "
            f"'-NoProfile','-ExecutionPolicy','Bypass','-File','{tmp_script}' "
            "-Verb RunAs -Wait"
        )
        try:
            DETACHED = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-Command", outer],
                timeout=1200,
                creationflags=DETACHED,
            )
        except subprocess.TimeoutExpired:
            self.q.put(("note", "Install/uninstall didn't finish in 20 minutes — "
                                "check Add/Remove Programs to see the state."))
            return
        except Exception as e:
            self.q.put(("note", f"Couldn't run installer: {e}"))
            return
        finally:
            # Clean up the temp script
            try:
                os.remove(tmp_script)
            except Exception:
                pass

        # 4. Verify by re-scanning
        new_path = _find_blender()
        new_version = _blender_version(new_path) if new_path else None
        if new_version and _ver_tuple(new_version) == _ver_tuple(BLENDER_EXPECTED_VERSION):
            self.q.put(("note",
                        f"✓ Blender {BLENDER_EXPECTED_VERSION} installed and "
                        "any older versions were removed."))
        elif proc.returncode == 0:
            self.q.put(("note",
                        f"Installer finished but Blender {BLENDER_EXPECTED_VERSION} "
                        "wasn't detected at standard paths. Restart JARVIS Chat to re-scan."))
        else:
            self.q.put(("note",
                        f"Installer exited with code {proc.returncode} — "
                        "may have been cancelled at the UAC prompt."))

    # ── Design pipeline (Blender / bpy code generation + execution) ────────
    def _design_worker(self, user_prompt: str) -> None:
        """Generate a bpy script via the deep model. Result goes to the queue
        as ('design_code', user_prompt, code_text); the receiver in _poll
        shows a confirm dialog before running anything."""
        # STREAM the response. The old version used stream=False, which means
        # Ollama sends NO bytes until the WHOLE generation finishes — so the 600s
        # socket-read timeout was really a hard cap on total generation time. A
        # simple vase finished under it; a sword makes deepseek-r1 reason longer
        # (more <think> tokens) and blew past 600s -> 'timed out' with nothing
        # received. Streaming keeps the socket receiving (timeout is now per-read,
        # not whole-generation) and lets us show progress on long designs.
        # Self-improving memory: if JARVIS has made something similar before that
        # scored well on the visual check, hand it to himself as a worked example.
        design_system = DESIGN_SYSTEM_BPY
        if _design_mem is not None:
            try:
                block = _design_mem.exemplar_prompt_block(user_prompt)
                if block:
                    design_system = design_system + block
                    self.q.put(("status",
                                "found a similar design you nailed before — "
                                "building on it…"))
            except Exception:
                pass
        # Recipe library: if a verified reusable helper matches this object type,
        # offer it so the model builds from a proven block, not from scratch.
        if _recipes_mod is not None:
            try:
                rblock = _recipes_mod.recipe_prompt_block(user_prompt)
                if rblock:
                    design_system = design_system + rblock
            except Exception:
                pass
        payload = json.dumps({
            "model": DEEP_MODEL,
            "messages": [
                {"role": "system", "content": design_system},
                {"role": "user",   "content": user_prompt},
            ],
            "stream":     True,
            "keep_alive": "10m",
            "options": {"num_ctx": NUM_CTX, "num_predict": 3000, "temperature": 0.2},
        }).encode()
        try:
            req = urllib.request.Request(
                CHAT_URL, data=payload,
                headers={"Content-Type": "application/json"})
            raw = ""
            last_beat = time.time()
            with urllib.request.urlopen(req, timeout=900) as resp:
                for line in resp:
                    line = line.decode("utf-8", "ignore").strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if obj.get("error"):
                        self.q.put(("fail",
                                    f"design generation failed: {obj['error']}"))
                        return
                    raw += (obj.get("message", {}) or {}).get("content", "") or ""
                    now = time.time()
                    if now - last_beat > 3.0:
                        self.q.put(("status",
                                    f"designing… ({len(raw)} chars so far)"))
                        last_beat = now
                    if obj.get("done"):
                        break
            code = _clean_script(raw)
            if not code:
                self.q.put(("fail", "design model returned an empty script"))
                return
            self.q.put(("design_code", user_prompt, code))
        except urllib.error.URLError as e:
            self.q.put(("fail", f"design generation: connection error {e.reason}"))
        except Exception as e:
            self.q.put(("fail", f"design generation failed: {e}"))

    def _design_edit_worker(self, edit_request: str) -> None:
        """Refine the LAST design instead of starting over: re-prompt the model
        with the previous script + the requested change. The combined prompt is
        recorded so the result can be visual-diff verified against the old
        render. Posts ('design_code', combined_prompt, code) like a fresh design,
        but tags _pending_edit so the executor knows to run the visual diff."""
        prev = self._last_design
        if not prev:
            self.q.put(("fail", "no previous design to edit"))
            return
        edit_system = (
            DESIGN_SYSTEM_BPY + "\n\nYou are EDITING an existing design. Below is "
            "the script that produced the current model. Apply ONLY the user's "
            "requested change and keep everything else the same. Output the "
            "complete updated script (no prose, no fences).")
        edit_user = (
            f"Current design is: {prev.get('prompt','')}\n\nIts script:\n"
            f"{prev.get('script','')}\n\nThe user now wants this change: "
            f"\"{edit_request}\"\n\nReturn the full updated script with that "
            "change applied.")
        payload = json.dumps({
            "model": DEEP_MODEL,
            "messages": [{"role": "system", "content": edit_system},
                         {"role": "user", "content": edit_user}],
            "stream": True, "keep_alive": "10m",
            "options": {"num_ctx": NUM_CTX, "num_predict": 3000, "temperature": 0.2},
        }).encode()
        try:
            req = urllib.request.Request(
                CHAT_URL, data=payload,
                headers={"Content-Type": "application/json"})
            raw = ""
            last_beat = time.time()
            with urllib.request.urlopen(req, timeout=900) as resp:
                for line in resp:
                    line = line.decode("utf-8", "ignore").strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if obj.get("error"):
                        self.q.put(("fail", f"edit failed: {obj['error']}"))
                        return
                    raw += (obj.get("message", {}) or {}).get("content", "") or ""
                    if time.time() - last_beat > 3.0:
                        self.q.put(("status",
                                    f"editing… ({len(raw)} chars so far)"))
                        last_beat = time.time()
                    if obj.get("done"):
                        break
            code = _clean_script(raw)
            if not code:
                self.q.put(("fail", "edit produced an empty script"))
                return
            # Stash the before-preview + change text so the executor can verify.
            self._pending_edit = {
                "before_preview": prev.get("preview", ""),
                "change": edit_request,
                "base_prompt": prev.get("prompt", ""),
            }
            combined = f"{prev.get('prompt','')} ({edit_request})"
            self.q.put(("design_code", combined, code))
        except urllib.error.URLError as e:
            self.q.put(("fail", f"edit: connection error {e.reason}"))
        except Exception as e:
            self.q.put(("fail", f"edit failed: {e}"))

    def _visual_diff(self, before_png: str, after_png: str,
                     change: str) -> tuple:
        """Show the vision model the BEFORE and AFTER renders and ask whether the
        requested change actually happened. Returns (changed: bool, note: str).
        Fails OPEN (changed=True) if vision/images unavailable."""
        try:
            with open(before_png, "rb") as f:
                b0 = base64.b64encode(f.read()).decode()
            with open(after_png, "rb") as f:
                b1 = base64.b64encode(f.read()).decode()
        except Exception:
            return (True, "")
        prompt = (
            "Two renders of a 3D model: the FIRST is BEFORE an edit, the SECOND "
            f"is AFTER. The user asked to: \"{change}\". Did the AFTER image "
            "actually apply that change versus the BEFORE? Reply STRICT JSON: "
            "{\"changed\": true/false, \"note\": \"one short sentence on what "
            "changed or why it didn't\"}.")
        payload = json.dumps({
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": prompt,
                          "images": [b0, b1]}],
            "stream": False, "keep_alive": "5m",
            "options": {"num_ctx": 4096, "num_predict": 150, "temperature": 0.1},
        }).encode()
        try:
            req = urllib.request.Request(
                CHAT_URL, data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                obj = json.loads(resp.read().decode("utf-8", "ignore"))
            raw = (obj.get("message", {}) or {}).get("content", "") or ""
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
            m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if not m:
                return (True, "")
            data = json.loads(m.group(0))
            return (bool(data.get("changed", True)),
                    str(data.get("note", "") or "").strip())
        except Exception:
            return (True, "")

    def _show_design_run_dialog(self, user_prompt: str, code: str) -> None:
        """Modal preview of the generated script — Run / Cancel. Confirm is
        mandatory; we never auto-execute model-generated code without it."""
        blender = _find_blender()
        dlg = tk.Toplevel(self.root)
        dlg.title("Generated design script")
        dlg.configure(bg=BG)
        dlg.geometry("760x560")
        dlg.transient(self.root)
        dlg.grab_set()
        try:
            dlg.attributes("-topmost", True)
        except Exception:
            pass

        tk.Label(dlg, text=f"For: {user_prompt[:120]}",
                 bg=BG, fg=ACCENT, font=("Segoe UI", 11, "bold"),
                 anchor="w", wraplength=720, justify="left"
                 ).pack(fill="x", padx=14, pady=(14, 4))
        status_msg = ("JARVIS wrote this Python script. Click Run to execute "
                      "it in Blender headless. Output saves to "
                      f"{OUTPUTS_DIR}\\<timestamp>\\.")
        if not blender:
            status_msg += "\n\n⚠ Blender not found on this system. Install from blender.org first."
        tk.Label(dlg, text=status_msg, bg=BG, fg=MUTED,
                 font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=720
                 ).pack(fill="x", padx=14, pady=(0, 8))

        txt = scrolledtext.ScrolledText(
            dlg, wrap="none", bg=PANEL, fg=FG, bd=0,
            font=("Consolas", 9), padx=10, pady=10,
            insertbackground=FG, relief="flat", highlightthickness=0,
        )
        txt.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        txt.insert("1.0", code)
        txt.config(state="disabled")

        btns = tk.Frame(dlg, bg=BG)
        btns.pack(fill="x", padx=14, pady=(0, 14))

        def _cancel(_=None) -> None:
            dlg.destroy()
            self._note("Design cancelled — nothing was run.")

        def _run(_=None) -> None:
            if not blender:
                self._note("Can't run — Blender not installed. Download from blender.org.")
                dlg.destroy()
                return
            dlg.destroy()
            self._note("Running script in Blender (background mode) — this can take 30-60s…")
            threading.Thread(target=self._execute_design_code,
                             args=(blender, code, 1, user_prompt),
                             daemon=True).start()

        tk.Button(btns, text="Cancel", command=_cancel,
                  bg=INPUT_BG, fg=FG, bd=0, relief="flat",
                  padx=14, pady=8, cursor="hand2",
                  font=("Segoe UI", 10)
                  ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        run_btn = tk.Button(btns, text="Run in Blender", command=_run,
                            bg=ACCENT, fg=BG, bd=0, relief="flat",
                            padx=14, pady=8, cursor="hand2",
                            font=("Segoe UI", 10, "bold"))
        run_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))
        if blender:
            run_btn.focus_set()
        dlg.bind("<Return>", _run if blender else _cancel)
        dlg.bind("<Escape>", _cancel)

    def _fix_design_script(self, code: str, error: str, user_prompt: str) -> str:
        """Ask the deep model to REPAIR a bpy script that errored in Blender.
        Returns the corrected script (think-blocks/fences stripped), or "" on
        failure. Streamed so long repairs don't hit a socket timeout. This is
        what lets JARVIS validate-and-fix its own design scripts automatically
        instead of the user having to diagnose the error and paste the fix."""
        repair_system = (
            DESIGN_SYSTEM_BPY + "\n\nYou are now REPAIRING a script that FAILED "
            "in Blender. Output ONLY the complete corrected script (no prose, no "
            "markdown fences).\n"
            "CRITICAL: Change ONLY what the error requires. Keep every other line "
            "IDENTICAL to the original — same geometry, same values, same "
            "structure. Do NOT redesign, refactor, rename, or 'improve' working "
            "parts. Fix the specific cause of the error and nothing else.\n"
            "Common causes: a wrong/removed bpy API call for Blender 5.x, a "
            "NameError, or an attribute that doesn't exist.")
        repair_user = (
            f"Original request: {user_prompt}\n\nThe script that failed:\n{code}"
            f"\n\nBlender's error output:\n{error}\n\nReturn the full script with "
            "ONLY the error fixed, everything else unchanged.")
        # Try the CODE model first (qwen2.5-coder: fast, no reasoning, great at
        # 'fix this error'); fall back to FAST_MODEL if the coder isn't pulled, so
        # a missing model never silently breaks auto-repair. (Both beat deepseek's
        # 30-90s reasoning for repairs.)
        def _try_repair(model: str) -> str:
            payload = json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": repair_system},
                    {"role": "user",   "content": repair_user},
                ],
                "stream":     True,
                "keep_alive": "10m",
                "options": {"num_ctx": NUM_CTX, "num_predict": 3000,
                            "temperature": 0.1},
            }).encode()
            req = urllib.request.Request(
                CHAT_URL, data=payload,
                headers={"Content-Type": "application/json"})
            raw = ""
            err = False
            last_beat = time.time()
            with urllib.request.urlopen(req, timeout=900) as resp:
                for line in resp:
                    line = line.decode("utf-8", "ignore").strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if obj.get("error"):     # e.g. model not found -> try fallback
                        err = True
                        break
                    raw += (obj.get("message", {}) or {}).get("content", "") or ""
                    now = time.time()
                    if now - last_beat > 3.0:
                        self.q.put(("status",
                                    f"fixing the script… ({len(raw)} chars)"))
                        last_beat = now
                    if obj.get("done"):
                        break
            if err:
                return ""
            return _clean_script(raw)

        for m in (CODE_MODEL, FAST_MODEL):
            try:
                out = _try_repair(m)
                if out:
                    return out
            except Exception:
                continue
        return ""

    def _visual_critique(self, preview_path: str, user_prompt: str) -> tuple:
        """LOOK at the rendered preview with the vision model and judge whether it
        actually reads as what the user asked for. This is the 'does it look like a
        sword?' check the crash-only validation can't do. Returns
        (looks_right: bool, score: int, problem: str). Fails OPEN (looks_right=True)
        if vision is unavailable, so it never blocks a working design."""
        try:
            with open(preview_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
        except Exception:
            return (True, 0, "")
        critique_prompt = (
            f"You are quality-checking a 3D model render. The user asked for: "
            f"\"{user_prompt}\".\n"
            "Look at this render and judge whether the object CLEARLY reads as that "
            "thing — right overall shape, proportions, and recognizable parts. "
            "Stylized/low-poly is fine as long as it's recognizable; be fair, not "
            "harsh. If it looks like a blob, the wrong object, or is missing the "
            "defining features (e.g. a sword with no blade, a mug with no handle), "
            "it FAILS.\n"
            "Respond with STRICT JSON only: {\"looks_right\": true/false, "
            "\"score\": 1-10, \"problem\": \"short concrete reason + what's missing "
            "if it fails, else empty\"}."
        )
        payload = json.dumps({
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": critique_prompt,
                          "images": [img_b64]}],
            "stream": False, "keep_alive": "5m",
            "options": {"num_ctx": 4096, "num_predict": 200, "temperature": 0.1},
        }).encode()
        try:
            req = urllib.request.Request(
                CHAT_URL, data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                obj = json.loads(resp.read().decode("utf-8", "ignore"))
            raw = (obj.get("message", {}) or {}).get("content", "") or ""
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
            m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if not m:
                return (True, 0, "")     # couldn't parse — don't block
            data = json.loads(m.group(0))
            looks = bool(data.get("looks_right", True))
            score = int(data.get("score", 0) or 0)
            problem = str(data.get("problem", "") or "").strip()
            # treat a confident low score as a fail even if looks_right slipped True
            if score and score <= 4:
                looks = False
            return (looks, score, problem)
        except Exception:
            return (True, 0, "")     # vision down — fail open

    def _revise_design_for_looks(self, code: str, problem: str,
                                 user_prompt: str) -> str:
        """Regenerate a bpy script that RAN FINE but didn't LOOK right. Unlike
        _fix_design_script (which fixes a crash and changes as little as possible),
        this is allowed to rework the GEOMETRY to better match the request, guided
        by the vision model's critique. Returns the revised script or ""."""
        revise_system = (
            DESIGN_SYSTEM_BPY + "\n\nThe previous script RAN SUCCESSFULLY but the "
            "rendered result DID NOT look like what the user asked for. You are "
            "REVISING the geometry so it clearly reads as the requested object. "
            "You MAY change the construction (different primitives, modifiers, "
            "proportions, added parts) to fix the visual problem — but keep the same "
            "scene/camera/light/export structure. Output ONLY the complete corrected "
            "script (no prose, no markdown fences).")
        revise_user = (
            f"Original request: {user_prompt}\n\nThe script that ran but looked "
            f"wrong:\n{code}\n\nWhat a vision model said is wrong with the render:\n"
            f"{problem}\n\nReturn the full revised script that fixes the SHAPE so it "
            "clearly looks like the requested object.")
        payload = json.dumps({
            "model": DEEP_MODEL,
            "messages": [{"role": "system", "content": revise_system},
                         {"role": "user", "content": revise_user}],
            "stream": True, "keep_alive": "10m",
            "options": {"num_ctx": NUM_CTX, "num_predict": 3000, "temperature": 0.3},
        }).encode()
        try:
            req = urllib.request.Request(
                CHAT_URL, data=payload,
                headers={"Content-Type": "application/json"})
            raw = ""
            last_beat = time.time()
            with urllib.request.urlopen(req, timeout=900) as resp:
                for line in resp:
                    line = line.decode("utf-8", "ignore").strip()
                    if not line:
                        continue
                    o = json.loads(line)
                    if o.get("error"):
                        return ""
                    raw += (o.get("message", {}) or {}).get("content", "") or ""
                    if time.time() - last_beat > 3.0:
                        self.q.put(("status",
                                    f"reshaping the model… ({len(raw)} chars)"))
                        last_beat = time.time()
                    if o.get("done"):
                        break
            return _clean_script(raw)
        except Exception:
            return ""

    def _execute_design_code(self, blender: str, code: str,
                             attempt: int = 1, user_prompt: str = "",
                             visual_tries: int = 0) -> None:
        """Run the bpy script in Blender headless. Posts results to the queue.
        On a script error, AUTO-REPAIRS: feeds the error + script back to the
        model, gets a corrected script, and retries (up to MAX_DESIGN_TRIES)."""
        MAX_DESIGN_TRIES = 3
        try:
            os.makedirs(OUTPUTS_DIR, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            out_dir = os.path.join(OUTPUTS_DIR, ts)
            os.makedirs(out_dir, exist_ok=True)
            script_path = os.path.join(out_dir, "script.py")

            with open(script_path, "w", encoding="utf-8") as f:
                # Inject OUT + the modules bpy scripts almost always need. The
                # model frequently uses math.* / mathutils / random but forgets
                # the import, which makes Blender raise NameError and quit with
                # no output ("designs won't load"). Pre-importing them here
                # GUARANTEES they're available regardless of what the model wrote.
                header = (
                    f"OUT = {out_dir!r}\n"
                    "import os, sys, math, random\n"
                    "try:\n"
                    "    import bmesh\n" "    import mathutils\n"
                    "    from mathutils import Vector, Matrix, Euler, Quaternion\n"
                    "except Exception:\n"
                    "    pass\n\n"
                )
                f.write(header + code + "\n")

            DETACHED = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                [blender, "--background", "--python", script_path],
                capture_output=True, text=True, timeout=300,
                creationflags=DETACHED,
            )
        except subprocess.TimeoutExpired:
            self.q.put(("note", "Design script timed out after 5 minutes."))
            return
        except FileNotFoundError as e:
            self.q.put(("note", f"Couldn't launch Blender: {e}"))
            return
        except Exception as e:
            self.q.put(("note", f"Execution failed: {e}"))
            return

        # Inventory the output dir: anything not the script itself counts.
        outputs: list[str] = []
        try:
            for fname in sorted(os.listdir(out_dir)):
                if fname == "script.py":
                    continue
                outputs.append(fname)
        except Exception:
            pass

        if result.returncode == 0 and outputs:
            preview = os.path.join(out_dir, "preview.png")
            # VISUAL DIFF: if this run was an EDIT, compare before vs after and
            # confirm the requested change actually happened. Done once (not on
            # the inner visual-reshape recursion) and only on the first attempt.
            pend = self._pending_edit
            if pend and os.path.isfile(preview) and pend.get("before_preview"):
                self._pending_edit = None   # consume so it runs once
                self.q.put(("status", "JARVIS is checking the edit…"))
                changed, note = self._visual_diff(
                    pend["before_preview"], preview, pend.get("change", ""))
                if changed:
                    self.q.put(("note",
                                f"✏ Edit applied{f': {note}' if note else '.'}"))
                else:
                    self.q.put(("note",
                                "✏ Hmm — I don't see that change in the result"
                                f"{f': {note}' if note else ''}. Showing it anyway; "
                                "try rephrasing the change."))
            # VISUAL SELF-CHECK: the script ran, but does the result actually LOOK
            # like what was asked? Look at the rendered preview with the vision
            # model; if it's clearly wrong (blob / wrong object / missing defining
            # parts), reshape the geometry and re-run — up to MAX_VISUAL_TRIES.
            MAX_VISUAL_TRIES = 2
            if (user_prompt and visual_tries < MAX_VISUAL_TRIES
                    and os.path.isfile(preview)):
                self.q.put(("status", "JARVIS is looking at the result…"))
                looks_right, score, problem = self._visual_critique(
                    preview, user_prompt)
                if not looks_right and problem:
                    self.q.put(("note",
                                f"👁 That doesn't look right yet"
                                f"{f' ({score}/10)' if score else ''}: {problem}\n"
                                "JARVIS is reshaping it…"))
                    revised = self._revise_design_for_looks(
                        code, problem, user_prompt)
                    if revised and revised.strip() != code.strip():
                        self._execute_design_code(
                            blender, revised, attempt, user_prompt,
                            visual_tries + 1)
                        return
                    self.q.put(("note",
                                "Couldn't improve the shape further — showing "
                                "what we have."))
                elif score:
                    self.q.put(("note", f"👁 Looks right ({score}/10)."))
                    # Self-improving memory: bank this visually-approved design so
                    # future similar requests start from a proven approach.
                    if _design_mem is not None and score >= 6:
                        try:
                            _design_mem.remember(user_prompt, code, score)
                        except Exception:
                            pass
            # Remember this as the last design so a follow-up edit ("make it
            # bigger") can build on THIS script + visually verify the change.
            # Simple attribute set; only read on the UI thread in send().
            if user_prompt:
                self._last_design = {
                    "prompt": user_prompt, "script": code,
                    "preview": preview if os.path.isfile(preview) else "",
                    "out_dir": out_dir,
                }
            file_list = "\n".join(f"   • {f}" for f in outputs)
            self.q.put(("note",
                        f"✓ Done. Files in:  {out_dir}\n{file_list}\n"
                        "Opening model.blend in Blender…"))
            # Launch Blender GUI with model.blend loaded so the user can see
            # / edit the generated model interactively. Fall back to opening
            # the folder in Explorer if the .blend wasn't produced.
            blend_file = os.path.join(out_dir, "model.blend")
            DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)
            NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if os.path.isfile(blend_file):
                try:
                    subprocess.Popen(
                        [blender, blend_file],
                        creationflags=DETACHED | NEW_GROUP,
                        close_fds=True,
                    )
                except Exception as e:
                    self.q.put(("note",
                                f"Couldn't open Blender ({e}). Files are at {out_dir}"))
                    try:
                        os.startfile(out_dir)   # type: ignore[attr-defined]
                    except Exception:
                        pass
            else:
                # No .blend produced — show the folder so the user can see what's there
                try:
                    os.startfile(out_dir)   # type: ignore[attr-defined]
                except Exception:
                    pass
        else:
            # Blender exits 0 even when the script raised a Python error mid-run.
            # Detect a Traceback in the combined output and report it as a script
            # crash rather than a generic "exited code N".
            combined = ((result.stderr or "") + (result.stdout or "")).strip()
            tail = combined[-1400:]
            # AUTO-REPAIR: rather than just reporting the error and making the
            # user diagnose + paste a fix, feed the error back to the model, get
            # a corrected script, and retry — up to MAX_DESIGN_TRIES. Falls
            # through to the detailed reporting below on the final attempt.
            if attempt < MAX_DESIGN_TRIES and combined:
                self.q.put(("note",
                            f"✗ Design attempt {attempt} failed — JARVIS is "
                            "reading the error and fixing the script…"))
                fixed = self._fix_design_script(code, tail, user_prompt)
                if fixed and fixed.strip() != code.strip():
                    self._execute_design_code(blender, fixed, attempt + 1,
                                              user_prompt, visual_tries)
                    return
                self.q.put(("note", "Couldn't auto-fix — showing the error."))
            if "Traceback (most recent call last)" in combined or "AttributeError:" in combined \
                    or "TypeError:" in combined or "NameError:" in combined \
                    or "RuntimeError:" in combined:
                # Pull the most specific error line for a one-liner summary
                summary_line = ""
                for line in reversed(combined.splitlines()):
                    line = line.strip()
                    if not line:
                        continue
                    if (line.startswith(("AttributeError", "TypeError", "NameError",
                                          "RuntimeError", "ImportError", "ValueError",
                                          "SyntaxError", "ZeroDivisionError"))
                            or "Error:" in line):
                        summary_line = line
                        break
                if not summary_line:
                    summary_line = "Python error during script execution"
                self.q.put(("note",
                            f"✗ Script crashed: {summary_line}\n"
                            f"Script saved at {out_dir}\\script.py — ask JARVIS to fix it.\n"
                            f"Last output:\n{tail}"))
            elif result.returncode != 0:
                self.q.put(("note",
                            f"✗ Blender exited with code {result.returncode}. "
                            f"Script saved at {out_dir}\\script.py\n"
                            f"Last output:\n{tail}"))
            else:
                self.q.put(("note",
                            f"✗ Blender ran but produced no output files. "
                            f"Script saved at {out_dir}\\script.py — possibly the "
                            "model forgot the save/export calls. "
                            f"Last output:\n{tail}"))

    def _council_worker(self, question: str, history: list[dict]) -> None:
        """Run the LLM council (Mixture-of-Agents) and post the synthesized
        answer. Proposers + disagreement check + (on numeric disputes) verify-
        by-execution + aggregation. Status updates stream to the panel; the
        final answer arrives as one block (the council can't token-stream)."""
        t0 = time.time()
        try:
            res = jarvis_council.run_council(
                question, history,
                status_cb=lambda m: self.q.put(("status", m)),
            )
            answer = res.get("answer", "")
            if not answer:
                # Council produced nothing (all advisors failed) — fall back
                # to a single deep-model call so the user still gets a reply.
                self.q.put(("status", "council unavailable — using single model"))
                self._worker(history, DEEP_MODEL)
                return
            # Surface the council's meta as a short note so the user sees how
            # the answer was reached (consensus vs resolved-disagreement, and
            # whether a result was deterministically verified).
            bits = []
            if res.get("skipped_aggregator"):
                bits.append("unanimous")
            else:
                bits.append(f"resolved (agreement {res.get('agreement', 0):.2f})")
            if res.get("verified"):
                bits.append(f"verified by execution → {res['verified']}")
            n = len(res.get("proposals", []))
            self.q.put(("note", f"(council of {n} · " + " · ".join(bits) + ")"))
            self.q.put(("token", answer))
            self.q.put(("done", round(time.time() - t0, 1), "council", answer))
        except Exception as e:
            self.q.put(("status", f"council error — using single model ({e})"))
            try:
                self._worker(history, DEEP_MODEL)
            except Exception as e2:
                self.q.put(("fail", str(e2)))

    def _image_worker(self, question: str, image_path: str) -> None:
        """Send an attached image to the vision model, just like screen capture."""
        t0 = time.time()
        try:
            with open(image_path, "rb") as f:
                raw = f.read()
            img_b64 = base64.b64encode(raw).decode()
        except Exception as e:
            self.q.put(("fail", f"reading image failed: {e}"))
            return
        prompt = (
            (question.strip() or
             f"What's in this image ({os.path.basename(image_path)})?") + "\n"
            "This is an image attached by the user. Describe the SCENE as a whole, "
            "not just a list of objects. Focus on:\n"
            "- WHO/WHAT is present and what they are DOING (actions).\n"
            "- The RELATIONSHIPS between things — e.g. a person HOLDING a cup, a "
            "cat SITTING ON a couch, a hand REACHING FOR a phone. Say who is "
            "interacting with what, and how (holding, wearing, pointing at, "
            "standing next to, on top of, inside).\n"
            "- Then any other notable details, the setting, and any visible text.\n"
            "Write it as a natural description of what is happening, then answer "
            "the user's question if they asked one."
        )
        payload = json.dumps({
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": prompt, "images": [img_b64]}],
            "stream": True, "keep_alive": "10m",
            "options": {"num_ctx": NUM_CTX, "num_predict": NUM_PREDICT},
        }).encode()
        raw_text, emitted = "", 0
        try:
            req = urllib.request.Request(
                CHAT_URL, data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=900) as resp:
                for line in resp:
                    line = line.decode("utf-8", "ignore").strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if obj.get("error"):
                        self.q.put(("fail", str(obj["error"])))
                        return
                    raw_text += obj.get("message", {}).get("content", "")
                    if len(raw_text) > emitted:
                        self.q.put(("token", raw_text[emitted:]))
                        emitted = len(raw_text)
                    if obj.get("done"):
                        break
            self.q.put(("done", round(time.time() - t0, 1), VISION_MODEL, raw_text.strip()))
        except urllib.error.URLError as e:
            self.q.put(("fail", f"connection error: {e.reason}"))
        except Exception as e:
            self.q.put(("fail", str(e)))

    def _cmd_update(self, cmd: str):
        """/update notes — show release notes for the pending update, if any.

        The bare /update and /update check commands were removed in v1.4.3 — the
        app checks GitHub silently on every launch and pops the install dialog
        automatically when a newer release exists, so manual triggers are
        redundant. /updates off still disables the auto-check."""
        arg = cmd[len("/update"):].strip().lower()
        if arg == "notes":
            if not self._pending_update:
                self._note("No pending update right now.")
                return
            tag, _url, notes = self._pending_update
            self._note(f"Release notes for {tag}:\n{notes or '(no notes provided)'}")

    def _show_install_dialog(self, tag: str, url: str) -> None:
        """Modal confirm — Install / Not now. Triggers _do_update on accept."""
        if self._updating:
            return
        confirm = tk.Toplevel(self.root)
        confirm.title("Update JARVIS Chat")
        confirm.configure(bg=BG)
        confirm.transient(self.root)
        confirm.grab_set()
        try:
            confirm.attributes("-topmost", True)
        except Exception:
            pass
        confirm.geometry("420x220")

        tk.Label(confirm, text=f"Install {tag}?", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 13, "bold")
                 ).pack(anchor="w", padx=20, pady=(18, 6))
        tk.Label(confirm,
                 text=(f"You're on {CLIENT_VERSION}. The new installer will "
                       "download (~13 MB), then JARVIS will close so the update "
                       "can apply. Your chat history and settings stay intact."),
                 bg=BG, fg=FG, wraplength=380, justify="left", anchor="w",
                 font=("Segoe UI", 10)
                 ).pack(fill="x", padx=20, pady=(0, 14))

        def _pick(go: bool) -> None:
            confirm.destroy()
            if go:
                self._do_update(url)

        row = tk.Frame(confirm, bg=BG)
        row.pack(fill="x", padx=20, pady=(0, 16))
        tk.Button(row, text="Not now", command=lambda: _pick(False),
                  bg=INPUT_BG, fg=FG, bd=0, relief="flat",
                  padx=12, pady=8, cursor="hand2"
                  ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ok_btn = tk.Button(row, text="Install update", command=lambda: _pick(True),
                           bg=ACCENT, fg=BG, bd=0, relief="flat",
                           padx=12, pady=8, cursor="hand2",
                           font=("Segoe UI", 10, "bold"))
        ok_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))
        ok_btn.focus_set()
        confirm.bind("<Return>", lambda _e: _pick(True))
        confirm.bind("<Escape>", lambda _e: _pick(False))

    def _do_update(self, url: str) -> None:
        if self._updating:
            return
        self._updating = True
        self._note(f"Downloading update from {url} …")

        def _go() -> None:
            try:
                tmp = os.path.join(
                    os.environ.get("TEMP", os.path.expanduser("~")),
                    f"JarvisChat-Setup-update-{int(time.time())}.exe")
                with urllib.request.urlopen(url, timeout=60) as r:
                    with open(tmp, "wb") as f:
                        while True:
                            chunk = r.read(64 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                self.q.put(("note", "Update downloaded. Launching installer — "
                                    "JARVIS will close so the update can apply."))
                # Silent install + close-and-restart existing instance.
                DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)
                subprocess.Popen(
                    [tmp, "/VERYSILENT", "/SUPPRESSMSGBOXES",
                     "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS", "/NORESTART"],
                    creationflags=DETACHED, close_fds=True)
                # Give the installer a moment to spawn, then close ourselves so
                # it can replace the .exe on disk.
                self.root.after(1500, self.close)
            except Exception as e:
                self._updating = False
                self.q.put(("note", f"Update failed: {e}. Try /update again later."))
        threading.Thread(target=_go, daemon=True).start()

    def _check_ollama(self):
        try:
            with urllib.request.urlopen(TAGS_URL, timeout=5):
                self.q.put(("status", "ready"))
        except Exception:
            self.q.put(("note", "Can't reach Ollama. Make sure it's running "
                                "(it should start automatically with Windows after install)."))

    def _worker(self, history: list[dict], model: str, extra_system: str = ""):
        system_content = SYSTEM_PROMPT
        if extra_system:
            system_content = SYSTEM_PROMPT + "\n\n" + extra_system
        # Inject JARVIS's evolving self-memory so his personality carries across
        # sessions (opinions, predictions, jokes). Best-effort; '' if empty.
        if _self_mod is not None:
            try:
                block = _self_mod.recall_block()
                if block:
                    system_content = system_content + "\n\n" + block
            except Exception:
                pass
        messages = [{"role": "system", "content": system_content}] + history[-20:]
        payload = json.dumps({
            "model": model, "messages": messages, "stream": True, "keep_alive": "10m",
            "options": {"num_ctx": NUM_CTX, "num_predict": NUM_PREDICT},
        }).encode()
        t0 = time.time()
        raw, emitted = "", 0
        try:
            req = urllib.request.Request(
                CHAT_URL, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=900) as resp:
                for line in resp:
                    line = line.decode("utf-8", "ignore").strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if obj.get("error"):
                        self.q.put(("fail", str(obj["error"])))
                        return
                    if self._cancel.is_set():
                        break
                    raw += obj.get("message", {}).get("content", "")
                    answer, thinking = visible_answer(raw)
                    if len(answer) > emitted:
                        chunk = answer[emitted:]
                        if emitted == 0:
                            chunk = chunk.lstrip()
                        emitted = len(answer)
                        if chunk:
                            self.q.put(("token", chunk))
                    elif thinking and emitted == 0:
                        self.q.put(("status", "JARVIS is thinking…"))
                    if obj.get("done"):
                        break
            answer, _ = visible_answer(raw)
            self.q.put(("done", round(time.time() - t0, 1), model, answer.strip()))
        except urllib.error.URLError as e:
            self.q.put(("fail", f"connection error: {e.reason}"))
        except Exception as e:
            self.q.put(("fail", str(e)))

    def _screen_worker(self, question: str):
        t0 = time.time()
        try:
            if ImageGrab is None:
                self.q.put(("show_panel",))
                self.q.put(("fail", "Pillow isn't installed — run:  pip install pillow"))
                return
            shots = []
            for (l, t, r, b) in _list_monitors():
                try:
                    shots.append(ImageGrab.grab(bbox=(l, t, r, b), all_screens=True))
                except Exception:
                    pass
            if not shots:
                shots = [ImageGrab.grab(all_screens=True)]
            self.q.put(("show_panel",))
            n = len(shots)
            self.q.put(("status",
                        f"JARVIS is looking at your screen{'s' if n > 1 else ''}…"))
            images_b64 = [_encode_png(im) for im in shots]
        except Exception as e:
            self.q.put(("show_panel",))
            self.q.put(("fail", f"screen capture failed: {e}"))
            return

        if n > 1:
            screen_note = (f"You are given {n} screenshots — one per monitor, "
                           f"monitor 1 through monitor {n}, left to right. ")
        else:
            screen_note = "This is a screenshot of the user's screen. "
        prompt = (
            (question.strip() or "What is on my screens?") + "\n"
            + screen_note +
            "Describe what is on each — apps, windows, any video or show playing, and "
            "what the user appears to be doing. Read important on-screen text, and say "
            "which monitor something is on when it matters."
        )
        payload = json.dumps({
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": prompt, "images": images_b64}],
            "stream": True, "keep_alive": "10m",
            "options": {"num_ctx": NUM_CTX, "num_predict": NUM_PREDICT},
        }).encode()
        raw, emitted = "", 0
        try:
            req = urllib.request.Request(
                CHAT_URL, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=900) as resp:
                for line in resp:
                    line = line.decode("utf-8", "ignore").strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if obj.get("error"):
                        self.q.put(("fail", str(obj["error"])))
                        return
                    if self._cancel.is_set():
                        break
                    raw += obj.get("message", {}).get("content", "")
                    if len(raw) > emitted:
                        self.q.put(("token", raw[emitted:]))
                        emitted = len(raw)
                    if obj.get("done"):
                        break
            self.q.put(("done", round(time.time() - t0, 1), VISION_MODEL, raw.strip()))
        except urllib.error.URLError as e:
            self.q.put(("fail", f"connection error: {e.reason}"))
        except Exception as e:
            self.q.put(("fail", str(e)))

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "token":
                    self._stream(item[1])
                elif kind == "status":
                    if item[1] == "ready":
                        self._set_status(f"ready  ·  {FAST_MODEL} / {DEEP_MODEL}")
                    else:
                        self._set_status(item[1])
                elif kind == "note":
                    self._note(item[1])
                elif kind == "show_panel":
                    try:
                        self.root.deiconify()
                        if self._restore_geo:
                            self.root.geometry(self._restore_geo)
                        self.root.lift()
                        self.root.attributes("-topmost", True)
                    except Exception:
                        pass
                    self._capturing = False
                elif kind == "voice_input":
                    # Wake-word fired and Whisper produced a transcript. Drop
                    # it into the entry box (so the user sees what was heard)
                    # and submit it via the normal send() path. Flag the turn
                    # as voice-originated so the reply gets spoken.
                    heard = item[1]
                    if not self.busy:
                        self.entry.delete("1.0", "end")
                        self._placeholder_on = False
                        self.entry.insert("1.0", heard)
                        self._last_input_was_voice = True
                        # Spoken-only: suppress rendering of this voice turn's
                        # You/JARVIS exchange. send() runs synchronously here so
                        # the entry text never visibly flashes. History is still
                        # kept inside send() for LLM context.
                        self._suppress_panel = self._voice_spoken_only
                        self.send()
                    else:
                        self._note(f"(heard '{heard}' — busy, ignoring)")
                elif kind == "done":
                    elapsed, model = item[1], item[2]
                    raw_answer = item[3]
                    # Plain text version used for history / telemetry / speech.
                    answer = plainify_math(raw_answer)
                    if not answer:
                        self._stream("[no response]")
                    elif not self._suppress_panel:
                        # Replace the raw streamed body with a markdown-rendered
                        # version: code blocks get a monospace box, **bold** /
                        # `inline code` / headings / bullets get styled. Falls
                        # back to a plain insert if the renderer trips on anything.
                        try:
                            self.view.config(state="normal")
                            self.view.delete("answer_start", "end-1c")
                            self._render_markdown(raw_answer)
                            self.view.see("end")
                            self.view.config(state="disabled")
                        except Exception:
                            try:
                                self.view.insert("answer_start", answer, "msg")
                                self.view.config(state="disabled")
                            except Exception:
                                pass
                    self.history.append({"role": "assistant", "content": answer})
                    # Self-memory reflection: maybe append a private self-note
                    # (opinion/prediction/update/joke). Background, silent, strict
                    # — most turns produce nothing. Skip vision/stopped turns.
                    if (_self_mod is not None and answer
                            and model != VISION_MODEL
                            and not self._cancel.is_set()
                            and self._pending_user_msg is not None):
                        try:
                            _self_mod.reflect_async(self._pending_user_msg, answer)
                        except Exception:
                            pass
                    status = ("stopped" if self._cancel.is_set()
                              else f"{model}  ·  {elapsed}s")
                    self._finish(status)
                    # If the user spoke this turn, speak the reply back. Done
                    # AFTER history append and _finish so the panel already shows
                    # the text — TTS happens in parallel. Typed turns stay silent.
                    if (self._last_input_was_voice and self.voice is not None
                            and answer and model != VISION_MODEL):
                        try:
                            self.voice.speak(answer)
                        except Exception as e:
                            self._note(f"(tts error: {e})")
                    self._last_input_was_voice = False
                    self._suppress_panel = False
                    # Telemetry: text exchanges only. Skip vision (model == VISION_MODEL)
                    # and empty replies. Always fire-and-forget on a daemon thread.
                    if (self.consented and self.install_id
                            and model != VISION_MODEL
                            and answer
                            and self._pending_user_msg is not None):
                        self.turn += 1
                        submit_exchange_async(
                            self.install_id, self.session_id, self.turn,
                            model, self._pending_user_msg, answer,
                        )
                    # BS-filter: only check deep-model answers. Fast model handles
                    # casual chat, no point critiquing 'how's it going' replies.
                    if (model == DEEP_MODEL
                            and answer
                            and self._pending_user_msg
                            and len(answer) > 80):
                        bs_filter_async(self.q, self._pending_user_msg, answer)
                    self._pending_user_msg = None
                elif kind == "bs_flag":
                    self._note(f"(self-check) {item[1]}")
                elif kind == "design_code":
                    # Model finished writing the bpy script — show the user
                    # the code and the confirm-before-run dialog.
                    user_prompt, code = item[1], item[2]
                    self._finish("design script ready")
                    self._show_design_run_dialog(user_prompt, code)
                elif kind == "blender_update_available":
                    installed, expected = item[1], item[2]
                    self._show_blender_update_dialog(installed, expected)
                elif kind == "fail":
                    self._stream(f"[error: {item[1]}]")
                    self._finish("error")
                elif kind == "update_available":
                    tag, url, notes = item[1], item[2], item[3]
                    auto_install = item[4] if len(item) > 4 else False
                    self._pending_update = (tag, url, notes)
                    if auto_install:
                        # Triggered by bare /update or startup auto-check —
                        # open the install dialog immediately, no extra step.
                        self._note(f"★ Update available: {tag} (you're on {CLIENT_VERSION}).")
                        self._show_install_dialog(tag, url)
                    else:
                        # Status-only path (/update check) — notify, don't open dialog.
                        self._note(
                            f"★ Update available: {tag}  "
                            f"(you're on {CLIENT_VERSION}). "
                            "Type  /update  to install,  /update notes  to see what's new.")
        except queue.Empty:
            pass
        self._tick_opacity()
        self.root.after(60, self._poll)

    def _tick_opacity(self):
        if self._capturing:
            return
        try:
            hovered = self.root.winfo_containing(*self.root.winfo_pointerxy()) is not None
            focused = self.root.focus_get() is not None
        except Exception:
            hovered = focused = True
        target = 1.0 if (hovered or focused or self.busy) else IDLE_ALPHA
        if abs(self._alpha - target) < 0.03:
            self._alpha = target
        else:
            self._alpha += 0.14 if target > self._alpha else -0.14
            self._alpha = min(1.0, max(IDLE_ALPHA, self._alpha))
        try:
            self.root.attributes("-alpha", self._alpha)
        except Exception:
            pass

    def _stream(self, chunk: str):
        # First real token means generation is underway — drop the thinking
        # animation so it doesn't pulse under the streaming reply.
        if getattr(self, "_think_on", False):
            self._stop_think_anim()
            self._set_status("JARVIS is responding…")
        if self._suppress_panel:
            return
        self.view.config(state="normal")
        self.view.insert("end", chunk, "msg")
        self.view.see("end")
        self.view.config(state="disabled")

    def _finish(self, status: str):
        self._stop_think_anim()
        if not self._suppress_panel:
            self.view.config(state="normal")
            self.view.insert("end", "\n", "msg")
            self.view.config(state="disabled")
        self.busy = False
        self._cancel.clear()
        self._set_busy_button(False)
        self._set_status(status)
        self.entry.focus_set()

    def _write(self, text: str, tag: str):
        # Notes (status/errors) always render; conversation content is hidden
        # for spoken-only voice turns.
        if self._suppress_panel and tag != "note":
            return
        self.view.config(state="normal")
        self.view.insert("end", text, tag)
        self.view.see("end")
        self.view.config(state="disabled")

    def _note(self, text: str):
        self._write(text + "\n", "note")

    def _set_status(self, text: str):
        # Any explicit status update cancels the thinking animation so the two
        # don't fight over the label.
        self._stop_think_anim()
        self.status.config(text=text, fg=MUTED)

    # --- "JARVIS is thinking…" animation --------------------------------
    # A lightweight pulsing-dots indicator in the status bar while a reply is
    # being generated. Pure Tk .after() loop — no threads, no deps. Gives the
    # panel a sense of life instead of a frozen "designing…" string.
    _THINK_FRAMES = ("·    ", "··   ", "···  ", "···· ", "·····", " ····",
                     "  ···", "   ··", "    ·", "     ")

    def _start_think_anim(self, label: str = "JARVIS is thinking") -> None:
        self._think_label = label
        self._think_i = 0
        self._think_on = True
        self._tick_think_anim()

    def _tick_think_anim(self) -> None:
        if not getattr(self, "_think_on", False):
            return
        frame = self._THINK_FRAMES[self._think_i % len(self._THINK_FRAMES)]
        self._think_i += 1
        try:
            self.status.config(text=f"{self._think_label}  {frame}", fg=ACCENT)
        except Exception:
            return
        self._think_after = self.root.after(110, self._tick_think_anim)

    def _stop_think_anim(self) -> None:
        if getattr(self, "_think_on", False):
            self._think_on = False
            after = getattr(self, "_think_after", None)
            if after:
                try:
                    self.root.after_cancel(after)
                except Exception:
                    pass
                self._think_after = None

    # --- Stop / busy button ---------------------------------------------
    def _send_or_stop(self):
        # The one button does double duty: Send when idle, Stop while busy.
        if self.busy:
            self._stop()
        else:
            self.send()

    def _stop(self):
        if not self.busy:
            return
        self._cancel.set()
        self._set_status("stopping…")

    def _set_busy_button(self, busy: bool):
        if busy:
            self.send_btn.config(text="Stop", bg=CLOSE_HL, activebackground=STOP_HL)
        else:
            self.send_btn.config(text="Send", bg=ACCENT, activebackground=YOU)

    # --- Input placeholder ----------------------------------------------
    def _set_placeholder(self):
        if self._placeholder_on:
            return
        if self.entry.get("1.0", "end-1c") == "":
            self.entry.insert("1.0", PLACEHOLDER)
            self.entry.tag_add("ph", "1.0", "end-1c")
            self._placeholder_on = True

    def _clear_placeholder(self, *_):
        if self._placeholder_on:
            self.entry.delete("1.0", "end")
            self._placeholder_on = False

    def _on_entry_key(self, event):
        # First real keystroke clears the greyed hint; ignore pure modifiers.
        if not self._placeholder_on:
            return
        if event.keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R",
                            "Alt_L", "Alt_R", "Caps_Lock", "Tab", "Escape"):
            return
        self._clear_placeholder()

    # --- Right-click copy menu ------------------------------------------
    def _show_view_menu(self, event):
        self._menu_click = self.view.index(f"@{event.x},{event.y}")
        try:
            in_code = self._code_range_at(self._menu_click) is not None
            self._menu.entryconfig("Copy code block",
                                   state="normal" if in_code else "disabled")
        except Exception:
            pass
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    def _code_range_at(self, index):
        ranges = self.view.tag_ranges("code")
        for i in range(0, len(ranges), 2):
            a, b = ranges[i], ranges[i + 1]
            if (self.view.compare(a, "<=", index)
                    and self.view.compare(index, "<", b)):
                return (a, b)
        return None

    def _to_clipboard(self, text: str, label: str):
        text = (text or "").strip("\n")
        if not text:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._set_status(label)
        except Exception:
            pass

    def _copy_selection(self, *_):
        try:
            self._to_clipboard(self.view.get("sel.first", "sel.last"),
                               "copied selection")
        except tk.TclError:
            self._set_status("nothing selected")
        return "break"

    def _copy_code_block(self, *_):
        rng = self._code_range_at(self._menu_click)
        if rng:
            self._to_clipboard(self.view.get(*rng), "copied code block")

    def _copy_last_reply(self, *_):
        r = self.view.tag_ranges("jarvis_label")
        if not r:
            self._set_status("no reply yet")
            return
        # Everything after the last "JARVIS" label is the latest reply body.
        self._to_clipboard(self.view.get(r[-1], "end-1c"), "copied last reply")

    def _select_all_view(self, *_):
        self.view.tag_remove("sel", "1.0", "end")
        self.view.tag_add("sel", "1.0", "end-1c")
        return "break"

    # --- Markdown / code rendering --------------------------------------
    def _md_insert(self, s: str, tag: str):
        # Insert just before the trailing newline so repeated inserts stay in
        # order and the view keeps its final newline (which _finish appends to).
        if s:
            self.view.insert("end-1c", s, tag)

    def _render_markdown(self, raw: str):
        # Split on fenced code blocks so code stays verbatim (never math-mangled).
        parts = re.split(r"(```[\s\S]*?```)", raw)
        for part in parts:
            if not part:
                continue
            if part.startswith("```"):
                m = re.match(r"```([^\n`]*)\n?([\s\S]*?)```\s*$", part)
                lang = (m.group(1).strip() if m else "")
                code = (m.group(2) if m else part.strip("`")).rstrip("\n")
                if lang:
                    self._md_insert(lang + "\n", "codelang")
                self._md_insert(code + "\n", "code")
            else:
                self._render_prose(plainify_math(part))

    def _render_prose(self, text: str):
        lines = text.split("\n")
        for i, line in enumerate(lines):
            nl = "\n" if i < len(lines) - 1 else ""
            s = line.strip()
            if re.match(r"#{1,6}\s", s):
                self._md_insert(re.sub(r"^#{1,6}\s+", "", s) + nl, "h")
            elif re.match(r"[-*]\s+\S", s):
                indent = line[:len(line) - len(line.lstrip())]
                self._md_insert(indent + "•  ", "msg")
                self._render_inline(re.sub(r"^[-*]\s+", "", s))
                self._md_insert(nl, "msg")
            else:
                self._render_inline(line)
                self._md_insert(nl, "msg")

    def _render_inline(self, line: str):
        idx = 0
        for m in re.finditer(r"`([^`]+)`|\*\*([^*]+?)\*\*", line):
            if m.start() > idx:
                self._md_insert(line[idx:m.start()], "msg")
            if m.group(1) is not None:
                self._md_insert(m.group(1), "inline_code")
            else:
                self._md_insert(m.group(2), "bold")
            idx = m.end()
        if idx < len(line):
            self._md_insert(line[idx:], "msg")


def _edition_dialog() -> str:
    """First-run edition picker: JARVIS vs JARVIS Manga. Own Tk root/mainloop
    (like consent/license). Returns 'jarvis' or 'manga'. Default 'jarvis'."""
    dlg = tk.Tk()
    dlg.title("Choose your JARVIS")
    dlg.configure(bg=BG)
    dlg.resizable(False, False)
    _try_set_icon(dlg)
    try:
        dlg.attributes("-topmost", True)
    except Exception:
        pass
    W, H = 480, 300
    sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
    dlg.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 3}")

    tk.Label(dlg, text="● Choose your edition", bg=BG, fg=ACCENT,
             font=("Segoe UI", 14, "bold"), anchor="w"
             ).pack(fill="x", padx=24, pady=(20, 4))
    tk.Label(dlg, bg=BG, fg=FG, wraplength=W - 48, justify="left", anchor="w",
             font=("Segoe UI", 10),
             text=("Both run the same JARVIS — this just sets the default lens. "
                   "You can change it later with /edition.")
             ).pack(fill="x", padx=24, pady=(0, 14))

    choice = {"edition": "jarvis"}

    def _pick(ed: str) -> None:
        choice["edition"] = ed
        dlg.quit()

    tk.Button(dlg, text="JARVIS  —  general assistant",
              command=lambda: _pick("jarvis"),
              bg=ACCENT, fg=BG, activebackground=YOU, activeforeground=BG,
              bd=0, relief="flat", padx=14, pady=12, cursor="hand2",
              font=("Segoe UI", 11, "bold")).pack(fill="x", padx=24, pady=(0, 8))
    tk.Button(dlg, text="JARVIS Manga  —  art & manga focus",
              command=lambda: _pick("manga"),
              bg=INPUT_BG, fg=FG, activebackground=PANEL, activeforeground=FG,
              bd=0, relief="flat", padx=14, pady=12, cursor="hand2",
              font=("Segoe UI", 11, "bold")).pack(fill="x", padx=24, pady=(0, 8))

    dlg.protocol("WM_DELETE_WINDOW", lambda: _pick("jarvis"))
    dlg.lift(); dlg.focus_force()
    dlg.mainloop()
    dlg.destroy()
    return choice["edition"]


def ensure_edition() -> None:
    """Pick edition once on first run, persist to config, and apply it live so
    this very session uses the chosen edition (no restart needed). If env or
    edition.txt already forced an edition, or the user already chose, skip."""
    global EDITION, IS_MANGA, APP_TITLE, SYSTEM_PROMPT
    if (os.environ.get("JARVIS_EDITION") or "").strip().lower() in ("jarvis", "manga"):
        return                      # forced by env — respect it, no prompt
    cfg = load_config()
    if cfg.get("edition") in ("jarvis", "manga"):
        return                      # already chosen on a prior run
    chosen = _edition_dialog()
    cfg["edition"] = chosen
    save_config(cfg)
    # apply live for this session
    EDITION = chosen
    IS_MANGA = chosen == "manga"
    APP_TITLE = "JARVIS Manga" if IS_MANGA else "JARVIS"
    if IS_MANGA and "MANGA EDITION" not in SYSTEM_PROMPT:
        SYSTEM_PROMPT = SYSTEM_PROMPT + MANGA_PROMPT_ADDON


def main():
    scale = _setup_dpi()
    # License gate FIRST — free to download, paid to use. Its own throwaway Tk
    # root/mainloop, so the panel never appears before activation. If the user
    # declines/quits without a valid license, exit before building anything.
    if not ensure_license():
        return
    # Edition picker (first run only) — choose JARVIS vs JARVIS Manga.
    ensure_edition()
    # Consent uses its own throwaway Tk root, so the main panel never appears
    # (even briefly) before the user has decided.
    consented = ensure_consent()
    cfg = load_config()
    install_id = get_install_id(cfg) if consented else ""
    # Pick the heaviest installed deepseek before building the panel so the
    # welcome note shows the model name we'll actually use.
    _autodetect_deep_model()
    _autodetect_vision_model()
    # Wire up self-memory: reflect with the FAST model (cheap, frequent) but
    # store/recall from the per-machine config dir.
    if _self_mod is not None:
        try:
            _self_mod.configure(_config_dir, CHAT_URL, FAST_MODEL, NUM_CTX)
        except Exception:
            pass
    if _design_mem is not None:
        try:
            _design_mem.configure(_config_dir)
        except Exception:
            pass
    if _recipes_mod is not None:
        try:
            _recipes_mod.configure(_config_dir)
        except Exception:
            pass
    # Use TkinterDnD's Tk so dropping files onto the panel works on Windows.
    # Falls back to plain tk.Tk if the package failed to import.
    if HAS_DND:
        try:
            root = TkinterDnD.Tk()
        except Exception:
            root = tk.Tk()
    else:
        root = tk.Tk()
    JarvisChat(root, scale, consented=consented, install_id=install_id)
    root.mainloop()


if __name__ == "__main__":
    main()
