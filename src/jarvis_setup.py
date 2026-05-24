#!/usr/bin/env python3
"""
JARVIS Setup — first-run wizard.

Checks for Ollama, installs it if missing (downloads the official installer
from ollama.com), then lets the user pick a model pack and pulls the models.

Run standalone to re-do setup at any time:
    pythonw jarvis_setup.py

A marker file is written to %LOCALAPPDATA%\\JarvisChat\\.setup_done on success
so the launcher knows to skip the wizard next time.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

OLLAMA_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
OLLAMA_TAGS_URL      = "http://localhost:11434/api/tags"
OLLAMA_PULL_URL      = "http://localhost:11434/api/pull"

MARKER_DIR  = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "JarvisChat"
MARKER_FILE = MARKER_DIR / ".setup_done"

MODEL_PACKS = {
    "Lite":     {"models": ["qwen2.5:7b", "llava:7b"],
                 "size":   "~10 GB",
                 "desc":   "Fast chat + screen vision. Runs on most laptops.\n"
                          "No deep-reasoning model — everything routes to qwen2.5."},
    "Standard": {"models": ["qwen2.5:7b", "deepseek-r1:14b", "llava:7b"],
                 "size":   "~19 GB",
                 "desc":   "Fast + deep-reasoning (deepseek-r1:14b) + screen vision.\n"
                          "Recommended. Needs ~12 GB VRAM for deep model, or runs slow on CPU."},
    "Heavy":    {"models": ["qwen2.5:7b", "deepseek-r1:32b", "llava:7b"],
                 "size":   "~30 GB",
                 "desc":   "Maximum reasoning quality with deepseek-r1:32b.\n"
                          "Needs 24 GB+ VRAM for the deep model to be usable."},
}
DEFAULT_PACK = "Standard"

# --- Catppuccin Mocha (matches the chat panel) -----------------------------
BG     = "#1e1e2e"
PANEL  = "#181825"
FG     = "#cdd6f4"
MUTED  = "#6c7086"
ACCENT = "#cba6f7"
OK     = "#a6e3a1"
WARN   = "#f9e2af"
ERR    = "#f38ba8"


# --- Ollama detection ------------------------------------------------------
def ollama_exe() -> str | None:
    p = shutil.which("ollama")
    if p:
        return p
    for candidate in (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Ollama" / "ollama.exe",
        Path("C:\\Program Files\\Ollama\\ollama.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def ollama_running() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=3):
            return True
    except Exception:
        return False


def installed_models() -> set:
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=5) as r:
            data = json.load(r)
        return {m.get("name", "") for m in data.get("models", [])}
    except Exception:
        return set()


# --- Wizard ----------------------------------------------------------------
class SetupWizard:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cancelled = False
        self.pack_var = tk.StringVar(value=DEFAULT_PACK)

        root.title("JARVIS Setup")
        root.configure(bg=BG)
        root.geometry("560x520")
        root.resizable(False, False)

        tk.Label(root, text="JARVIS Chat — first-time setup",
                 bg=BG, fg=ACCENT, font=("Segoe UI", 16, "bold")
                 ).pack(pady=(18, 4))
        tk.Label(root, text="Sets up Ollama and downloads the AI models JARVIS uses.\n"
                            "Everything runs locally on your PC — no cloud, no accounts.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 10), justify="center"
                 ).pack(pady=(0, 14))

        pack_frame = tk.LabelFrame(root, text="  Choose a model pack  ",
                                   bg=BG, fg=FG, font=("Segoe UI", 10, "bold"),
                                   bd=0, padx=14, pady=10)
        pack_frame.pack(fill="x", padx=20, pady=(0, 12))
        for name, info in MODEL_PACKS.items():
            row = tk.Frame(pack_frame, bg=BG)
            row.pack(fill="x", pady=4)
            rb = tk.Radiobutton(
                row, text=f"{name}   ({info['size']})", variable=self.pack_var,
                value=name, bg=BG, fg=FG, selectcolor=PANEL,
                activebackground=BG, activeforeground=ACCENT,
                font=("Segoe UI", 10, "bold"), anchor="w")
            rb.pack(anchor="w")
            tk.Label(row, text="     " + info["desc"], bg=BG, fg=MUTED,
                     font=("Segoe UI", 9), justify="left", anchor="w"
                     ).pack(anchor="w", fill="x")

        self.status = tk.Label(root, text="Ready.", bg=BG, fg=FG,
                               font=("Segoe UI", 10), anchor="w",
                               wraplength=520, justify="left")
        self.status.pack(fill="x", padx=20, pady=(8, 4))

        self.bar = ttk.Progressbar(root, mode="determinate", maximum=1000)
        self.bar.pack(fill="x", padx=20, pady=(0, 10))

        btns = tk.Frame(root, bg=BG)
        btns.pack(fill="x", padx=20, pady=(0, 16))
        self.start_btn = tk.Button(
            btns, text="Install & download models", command=self._start,
            bg=ACCENT, fg=BG, activebackground=OK, activeforeground=BG,
            bd=0, relief="flat", font=("Segoe UI", 10, "bold"),
            padx=18, pady=8, cursor="hand2")
        self.start_btn.pack(side="right")
        self.skip_btn = tk.Button(
            btns, text="Skip — I'll set up myself", command=self._skip,
            bg=BG, fg=MUTED, activebackground=BG, activeforeground=FG,
            bd=0, relief="flat", font=("Segoe UI", 9), cursor="hand2")
        self.skip_btn.pack(side="right", padx=(0, 12))

    # --- progress helpers (called from worker via root.after) -------------
    def _set_status(self, text: str, color: str = FG):
        self.root.after(0, lambda: self.status.config(text=text, fg=color))

    def _set_progress(self, value: float):
        self.root.after(0, lambda: self.bar.config(value=value))

    def _set_indeterminate(self, on: bool):
        def _go():
            if on:
                self.bar.config(mode="indeterminate")
                self.bar.start(12)
            else:
                self.bar.stop()
                self.bar.config(mode="determinate", value=0)
        self.root.after(0, _go)

    # --- buttons ----------------------------------------------------------
    def _skip(self):
        if messagebox.askyesno(
            "Skip setup?",
            "Skip setup? JARVIS Chat needs Ollama running with the right models.\n\n"
            "You can re-run this wizard any time from the Start Menu "
            "(\"JARVIS Setup\")."):
            self.root.destroy()

    def _start(self):
        self.start_btn.config(state="disabled")
        self.skip_btn.config(state="disabled")
        pack = self.pack_var.get()
        threading.Thread(target=self._run, args=(pack,), daemon=True).start()

    # --- worker -----------------------------------------------------------
    def _run(self, pack_name: str):
        try:
            self._ensure_ollama_installed()
            self._ensure_ollama_running()
            self._pull_models(MODEL_PACKS[pack_name]["models"])
        except SetupError as e:
            self._set_indeterminate(False)
            self._set_status(f"Setup failed: {e}", ERR)
            self.root.after(0, lambda: messagebox.showerror("Setup failed", str(e)))
            self.root.after(0, lambda: self.start_btn.config(state="normal"))
            self.root.after(0, lambda: self.skip_btn.config(state="normal"))
            return
        except Exception as e:
            self._set_indeterminate(False)
            self._set_status(f"Unexpected error: {e}", ERR)
            self.root.after(0, lambda: self.start_btn.config(state="normal"))
            self.root.after(0, lambda: self.skip_btn.config(state="normal"))
            return

        try:
            MARKER_DIR.mkdir(parents=True, exist_ok=True)
            MARKER_FILE.write_text(f"setup_completed\npack={pack_name}\n", encoding="utf-8")
        except Exception:
            pass

        self._set_status("All done — JARVIS is ready to chat.", OK)
        self._set_progress(1000)
        self.root.after(0, lambda: self.start_btn.config(
            text="Launch JARVIS", state="normal", command=self._launch_chat))
        self.root.after(0, lambda: self.skip_btn.config(state="normal",
                                                        text="Close", command=self.root.destroy))

    def _launch_chat(self):
        here = Path(__file__).resolve().parent
        chat = here / "jarvis_chat.py"
        try:
            if chat.is_file():
                pyw = Path(sys.executable).with_name("pythonw.exe")
                exe = str(pyw) if pyw.is_file() else sys.executable
                subprocess.Popen([exe, str(chat)], close_fds=True)
            else:
                packaged = here / "JarvisChat.exe"
                if packaged.is_file():
                    subprocess.Popen([str(packaged)], close_fds=True)
        finally:
            self.root.destroy()

    # --- steps ------------------------------------------------------------
    def _ensure_ollama_installed(self):
        if ollama_exe():
            self._set_status("Ollama is already installed.", OK)
            return
        self._set_status("Downloading Ollama installer from ollama.com…")
        installer = Path(os.environ.get("TEMP", ".")) / "OllamaSetup.exe"
        try:
            with urllib.request.urlopen(OLLAMA_INSTALLER_URL, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                with open(installer, "wb") as f:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            self._set_progress(downloaded / total * 1000)
                            self._set_status(
                                f"Downloading Ollama… {downloaded // (1024*1024)} / "
                                f"{total // (1024*1024)} MB")
                        else:
                            self._set_status(
                                f"Downloading Ollama… {downloaded // (1024*1024)} MB")
        except Exception as e:
            raise SetupError(f"couldn't download Ollama installer: {e}")

        self._set_indeterminate(True)
        self._set_status("Running the Ollama installer — accept the UAC prompt and follow its window…")
        try:
            # Let Ollama show its own installer UI so the user can see/approve it.
            # (Running silently would still need admin and would suppress UAC visibility.)
            proc = subprocess.run([str(installer)], timeout=900)
            if proc.returncode != 0:
                raise SetupError(f"Ollama installer exited with code {proc.returncode}")
        except subprocess.TimeoutExpired:
            raise SetupError("Ollama installer didn't finish in 15 minutes")
        except Exception as e:
            raise SetupError(f"couldn't run Ollama installer: {e}")
        finally:
            self._set_indeterminate(False)

        if not ollama_exe():
            raise SetupError(
                "Ollama installer ran, but ollama.exe still isn't found. "
                "Try installing manually from ollama.com, then re-run this setup.")
        self._set_status("Ollama installed.", OK)

    def _ensure_ollama_running(self):
        if ollama_running():
            self._set_status("Ollama is running.", OK)
            return
        self._set_status("Starting Ollama service…")
        exe = ollama_exe()
        if not exe:
            raise SetupError("ollama.exe not found")
        try:
            # `ollama serve` is the daemon; spawn detached so it survives this wizard.
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            subprocess.Popen(
                [exe, "serve"],
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True)
        except Exception as e:
            raise SetupError(f"couldn't start ollama serve: {e}")

        self._set_indeterminate(True)
        for _ in range(30):
            if ollama_running():
                self._set_indeterminate(False)
                self._set_status("Ollama is running.", OK)
                return
            time.sleep(1)
        self._set_indeterminate(False)
        raise SetupError("Ollama didn't respond on localhost:11434 within 30s")

    def _pull_models(self, models: list[str]):
        already = installed_models()
        for i, model in enumerate(models, 1):
            if any(m == model or m.startswith(model + ":") for m in already):
                self._set_status(f"[{i}/{len(models)}] {model} — already installed", OK)
                continue
            self._pull_one(model, i, len(models))

    def _pull_one(self, model: str, idx: int, total: int):
        self._set_status(f"[{idx}/{total}] Pulling {model} — this can take a while…")
        self._set_progress(0)
        payload = json.dumps({"name": model, "stream": True}).encode()
        req = urllib.request.Request(
            OLLAMA_PULL_URL, data=payload,
            headers={"Content-Type": "application/json"})
        last_pct = -1
        try:
            with urllib.request.urlopen(req, timeout=None) as resp:
                for line in resp:
                    line = line.decode("utf-8", "ignore").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("error"):
                        raise SetupError(f"pulling {model}: {obj['error']}")
                    completed = obj.get("completed", 0)
                    total_bytes = obj.get("total", 0)
                    status = obj.get("status", "")
                    if total_bytes:
                        pct = int(completed / total_bytes * 1000)
                        if pct != last_pct:
                            self._set_progress(pct)
                            last_pct = pct
                        mb_done = completed // (1024 * 1024)
                        mb_total = total_bytes // (1024 * 1024)
                        self._set_status(
                            f"[{idx}/{total}] {model} — {status}: "
                            f"{mb_done} / {mb_total} MB")
                    else:
                        self._set_status(f"[{idx}/{total}] {model} — {status}")
        except urllib.error.URLError as e:
            raise SetupError(f"pulling {model}: {e.reason}")
        except SetupError:
            raise
        except Exception as e:
            raise SetupError(f"pulling {model}: {e}")
        self._set_progress(1000)
        self._set_status(f"[{idx}/{total}] {model} — done", OK)


class SetupError(Exception):
    pass


def main():
    root = tk.Tk()
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
        style.configure("Horizontal.TProgressbar",
                        background=ACCENT, troughcolor=PANEL,
                        bordercolor=PANEL, lightcolor=ACCENT, darkcolor=ACCENT)
    except Exception:
        pass
    SetupWizard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
