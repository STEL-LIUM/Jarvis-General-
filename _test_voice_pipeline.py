#!/usr/bin/env python3
"""
Standalone integration test for the voice -> UI pipeline.

Wires:    jarvis_voice_commands.handle(text)
             -> registered opener/hider
             -> jarvis_ui_terminal / jarvis_ui_hud open_window/hide_window

Runs a scripted sequence of utterances on the Tk thread (via after()), so we
can verify the whole chain end-to-end without a microphone. Closes itself
after 6 seconds.

NOT shipped — lives next to the modules during development. Delete or
exclude from the spec before the next build.
"""
from __future__ import annotations

import sys
import tkinter as tk

import jarvis_voice_commands as vc
import jarvis_ui_terminal as term
import jarvis_ui_hud as hud


def main() -> int:
    root = tk.Tk()
    root.title('voice pipeline test')
    root.geometry('420x180+40+40')
    root.configure(bg='#1e1e2e')

    log = tk.Text(root, bg='#181825', fg='#cdd6f4',
                  font=('Consolas', 10), height=10, bd=0, padx=8, pady=6)
    log.pack(side='top', fill='both', expand=True, padx=8, pady=8)

    def say(msg: str) -> None:
        log.insert('end', msg + '\n')
        log.see('end')
        print(msg, flush=True)

    # Status callback so the matcher can narrate.
    vc.register_status_cb(say)

    # Wire windows: opener/hider that marshal onto Tk thread.
    vc.register_window(
        'terminal',
        opener=lambda: root.after(0, lambda: term.open_window(root)),
        hider =lambda: root.after(0, term.hide_window),
    )
    vc.register_window(
        'hud',
        opener=lambda: root.after(0, lambda: hud.open_window(root)),
        hider =lambda: root.after(0, hud.hide_window),
    )

    # Scripted utterance sequence: each fires after `delay_ms`.
    sequence = [
        (   500, 'JARVIS, open the terminal'),
        ( 1_800, 'show the HUD'),
        ( 4_000, 'put it away'),
        ( 5_500, 'show yourself'),
        ( 7_500, '__exit__'),
    ]

    say('--- voice pipeline test ---')
    for delay, utt in sequence:
        if utt == '__exit__':
            root.after(delay, root.destroy)
            continue
        def _fire(u=utt):
            say(f'>> "{u}"')
            took = vc.handle(u)
            m = vc.last_match()
            tag = f"[{m.intent}@{m.confidence:.2f}]" if m else "[no match]"
            say(f'   {tag}  handled={took}')
        root.after(delay, _fire)

    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
