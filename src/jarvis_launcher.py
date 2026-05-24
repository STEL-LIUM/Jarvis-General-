#!/usr/bin/env python3
"""
JARVIS launcher — single entry point used by the installer's Start Menu shortcuts.

Behavior:
  jarvis_launcher.py          -> if setup marker missing, run setup wizard
                                 otherwise launch the chat panel
  jarvis_launcher.py --setup  -> always run the setup wizard
  jarvis_launcher.py --chat   -> always launch the chat panel (skip setup check)
"""
import sys
from pathlib import Path

from jarvis_setup import MARKER_FILE


def _run_setup():
    import jarvis_setup
    jarvis_setup.main()


def _run_chat():
    import jarvis_chat
    jarvis_chat.main()


def main():
    args = sys.argv[1:]
    if "--setup" in args:
        _run_setup()
        return
    if "--chat" in args:
        _run_chat()
        return
    if not MARKER_FILE.is_file():
        _run_setup()
    else:
        _run_chat()


if __name__ == "__main__":
    main()
