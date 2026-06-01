# JARVIS Chat 1.6.0

The biggest update yet — JARVIS can now **see better, remember himself, and improve his own 3D designs over time.**

## 🧠 A real personality, with continuity
- **Self-memory** — JARVIS keeps private notes about himself across sessions: opinions he's formed, predictions he's made, things he changed his mind on. He builds on past conversations instead of starting blank every time.
- **Opinions with a spine** — he'll disagree with you when he has a real reason, change his mind out loud when you make a better argument, and surface genuine internal uncertainty instead of faking confidence.

## 👁 Much better at seeing
- **New vision model (qwen2.5-VL)** — understands *scenes and relationships*, not just object lists. Send a photo of someone holding a coffee and he gets "a person holding a cup of coffee," not "coffee, hand, cup." (Auto-falls back to LLaVA if the new model isn't installed.)

## 🛠 Self-improving 3D design
- **Visual self-correction** — after generating a model, JARVIS *looks at the render* and reshapes it if it doesn't actually look like what you asked for. A sword with no blade gets fixed, not shipped.
- **Edits that verify themselves** — say "make it bigger" or "add a handle" and he edits the previous model, then compares before/after to confirm the change really happened.
- **Design memory** — every model that passes the visual check is remembered; similar future requests build on your best past results.
- **Idle self-play** (opt-in, `/idle on`) — when your PC sits idle, JARVIS practices 3D designs and banks the best ones, getting better over time.
- **Far more reliable scripts** — rebuilt the Blender design guidance from analysis of hundreds of real generations; common failures (missing imports, wrong API calls, bad geometry) are largely gone.

## ✨ Polish
- Animated "JARVIS is thinking…" indicator in the chat panel.
- New `/idle on|off|status` command.
- Code cleanup and reliability fixes throughout the design pipeline.

---
*Local-first as always — your data stays on your machine. Self-memory and design memory are plain local files you can read or clear anytime.*
