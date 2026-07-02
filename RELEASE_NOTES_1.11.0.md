# JARVIS Chat — v1.11.0

Changes since **v1.9.2**.

## Highlights (new in 1.11.0)
- **New engine for the HUD — no more freezing.** The holographic HUD now renders
  on Qt's bundled Chromium (PySide6/QtWebEngine) instead of the system Edge
  WebView2 runtime that was intermittently hanging on startup. Same look, opens
  in ~1s. The old WebView2 HUD remains as an automatic fallback.
- **Free local, paid online.** Local model packs (Lite / Core / Max) are now
  **free** — download and run entirely on your own PC. A new **Online** option
  lets people with no GPU install a thin client and use our servers with a key.
- **Coworker passwords.** Coworkers get free online access with a password
  (validated server-side; not shipped in the app or the repo).
- **Online gateway hardened.** Per-user authentication (active subscription key
  *or* coworker password), a strict **one-request-at-a-time queue** so the GPU
  isn't thrashed, and **chat-only** access (no remote image generation).
- **JARVIS Finance app.** A dedicated window (bills pot, deposits/spend, bills
  with cadence, weekly "transfer $X" report, and a bills chart) plus a built-in
  **finance chat that remembers** past conversations and knows your real numbers.
  Bookkeeper only — it never moves money.
- **New mascot logo.** The animated arc-reactor is now the app/taskbar icon
  (replaces the feather); the taskbar shows "JARVIS," not Python.
- **Keyboard scrolling** in all chats (arrow keys / PageUp-Down).

## Reliability
- Deferred engine warmup + model preload so the window is responsive immediately
  and the first message is fast.
- Single-instance handling: duplicate launches exit silently instead of leaving a
  stray dialog.
- Online client now verifies the server's TLS certificate correctly (bundled an
  up-to-date CA set) so joiners don't hit cert errors.
- "Keep online open" startup service (engine + auth gate + public tunnel).

## From the 1.10.x line (also since 1.9.2)
- **Deep brain:** Qwen3-Next-80B-A3B "thinking" model as an on-demand heavy brain
  (Max pack); Ornith-1.0-35B as the default deep/agent/coder brain.
- **PROMETHEUS routing** wired in for smarter lane selection.
- **Anti-freeze:** one model resident at a time + a RAM guardrail.
- **:sim** — Exo-Suit simulations.
- **Phone web chat** and **/hwscan** hardware scan that auto-fits the model pack.
- **Council** upgraded (3-role debate + team piggyback); code-fix routing and
  modding-context fixes.
- **Combat AI** dataset generation + sensor protocol.

## Notes
- Local mode requires no account and no key.
- Online mode requires an active subscription key, or a coworker password.
