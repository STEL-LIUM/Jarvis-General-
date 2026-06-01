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

# Blender — optional, only used by the design pipeline ("make a cube using blender").
# Bump this when Blender releases a new stable. If the URL ever 404s, the
# wizard falls back to opening the browser to the manual download page.
BLENDER_INSTALLER_URL = "https://download.blender.org/release/Blender5.1/blender-5.1.2-windows-x64.msi"
BLENDER_DOWNLOAD_PAGE = "https://www.blender.org/download/"

MARKER_DIR  = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "JarvisChat"
MARKER_FILE = MARKER_DIR / ".setup_done"

MODEL_PACKS = {
    "Lite":     {"models": ["qwen2.5:7b", "llava:7b"],
                 "size":   "~10 GB",
                 "desc":   "qwen2.5:7b + llava:7b. Works on most laptops."},
    "Standard": {"models": ["qwen2.5:7b", "deepseek-r1:14b", "llava:7b"],
                 "size":   "~19 GB",
                 "desc":   "Adds deepseek-r1:14b for deep reasoning."},
    "Heavy":    {"models": ["qwen2.5:7b", "deepseek-r1:32b", "llava:7b"],
                 "size":   "~30 GB",
                 "desc":   "Uses deepseek-r1:32b. Best on 24 GB+ VRAM. "
                          "Smaller cards work via CPU offload (slower)."},
}
DEFAULT_PACK = "Standard"

# --- Catppuccin Mocha (matches the chat panel) -----------------------------
BG       = "#1e1e2e"
PANEL    = "#181825"
SURFACE  = "#313244"   # subtle elevated surface for cards
FG       = "#cdd6f4"
MUTED    = "#6c7086"
ACCENT   = "#cba6f7"
ACCENT_2 = "#b4befe"   # accent hover
OK       = "#a6e3a1"
WARN     = "#f9e2af"
ERR      = "#f38ba8"


def _add_hover(btn: tk.Button, normal_bg: str, hover_bg: str) -> None:
    """Cheap rollover effect — Tk's activebackground only triggers on press."""
    btn.bind("<Enter>", lambda _e: btn.config(bg=hover_bg))
    btn.bind("<Leave>", lambda _e: btn.config(bg=normal_bg))


def _try_set_icon(root: tk.Tk) -> None:
    """Best-effort: set the title-bar icon to the bundled feather."""
    here = Path(__file__).resolve().parent
    candidates = [
        here / "icon.ico",
        Path(getattr(sys, "_MEIPASS", "")) / "icon.ico"
            if getattr(sys, "_MEIPASS", None) else None,
        Path(sys.executable).parent / "icon.ico",
        here.parent.parent / "build" / "icon.ico",
    ]
    for p in candidates:
        if p and p.is_file():
            try:
                root.iconbitmap(default=str(p))
                return
            except Exception:
                continue


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


# --- Blender detection -----------------------------------------------------
def find_blender() -> str | None:
    """Locate blender.exe on Windows (PATH, Program Files, LOCALAPPDATA)."""
    on_path = shutil.which("blender")
    if on_path:
        return on_path
    import glob
    pf  = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    candidates: list[str] = []
    candidates += glob.glob(os.path.join(pf,   "Blender Foundation", "Blender *", "blender.exe"))
    candidates += glob.glob(os.path.join(pf86, "Blender Foundation", "Blender *", "blender.exe"))
    if local:
        candidates += glob.glob(os.path.join(local, "Programs", "Blender Foundation",
                                              "Blender *", "blender.exe"))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


# --- Wizard ----------------------------------------------------------------
class SetupWizard:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cancelled = False
        self.pack_var = tk.StringVar(value=DEFAULT_PACK)
        self._pack_cards: dict[str, tk.Frame] = {}

        root.title("JARVIS Setup")
        root.configure(bg=BG)
        root.geometry("660x720")
        root.resizable(False, False)
        _try_set_icon(root)

        # --- Header band (feather + title + subtitle) ----------------------
        header = tk.Frame(root, bg=PANEL, height=110)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        tk.Label(header, text="\U0001FAB6", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI Emoji", 30)
                 ).pack(side="left", padx=(28, 14), pady=24)
        title_box = tk.Frame(header, bg=PANEL)
        title_box.pack(side="left", anchor="w", pady=24)
        tk.Label(title_box, text="JARVIS Chat", bg=PANEL, fg=FG,
                 font=("Segoe UI", 18, "bold"), anchor="w"
                 ).pack(anchor="w")
        tk.Label(title_box, text="First-time setup", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 10), anchor="w"
                 ).pack(anchor="w")

        # --- Body ---------------------------------------------------------
        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=28, pady=(18, 0))

        tk.Label(body,
                 text="Sets up Ollama (the local AI engine) and downloads the "
                      "models JARVIS uses. Everything runs locally on this PC — "
                      "no cloud, no accounts, no API keys.",
                 bg=BG, fg=FG, font=("Segoe UI", 10), justify="left",
                 wraplength=600, anchor="w"
                 ).pack(fill="x", pady=(0, 16))

        tk.Label(body, text="CHOOSE A MODEL PACK", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 9, "bold"), anchor="w"
                 ).pack(fill="x", pady=(0, 8))

        for name, info in MODEL_PACKS.items():
            self._build_pack_card(body, name, info)

        # --- Status + progress + buttons ----------------------------------
        bottom = tk.Frame(root, bg=BG)
        bottom.pack(fill="x", side="bottom", padx=28, pady=(12, 18))

        self.status = tk.Label(bottom, text="Ready when you are.",
                               bg=BG, fg=MUTED, font=("Segoe UI", 10),
                               anchor="w", wraplength=600, justify="left")
        self.status.pack(fill="x", pady=(0, 6))

        self.bar = ttk.Progressbar(bottom, mode="determinate", maximum=1000)
        self.bar.pack(fill="x", pady=(0, 14))

        btns = tk.Frame(bottom, bg=BG)
        btns.pack(fill="x")
        self.skip_btn = tk.Button(
            btns, text="Skip — I'll set up myself", command=self._skip,
            bg=BG, fg=MUTED, activebackground=BG, activeforeground=FG,
            bd=0, relief="flat", font=("Segoe UI", 9), cursor="hand2")
        self.skip_btn.pack(side="left")
        self.start_btn = tk.Button(
            btns, text="  Install & download models  ", command=self._start,
            bg=ACCENT, fg=BG, activebackground=ACCENT_2, activeforeground=BG,
            bd=0, relief="flat", font=("Segoe UI", 10, "bold"),
            padx=18, pady=10, cursor="hand2")
        self.start_btn.pack(side="right")
        _add_hover(self.start_btn, ACCENT, ACCENT_2)

        self._update_selection(DEFAULT_PACK)

    # --- Pack-card helpers ------------------------------------------------
    def _build_pack_card(self, parent: tk.Frame, name: str, info: dict) -> None:
        """One clickable card per model pack, with selected-state border."""
        card = tk.Frame(parent, bg=SURFACE, bd=0,
                        highlightthickness=2, highlightbackground=SURFACE)
        card.pack(fill="x", pady=4)

        row = tk.Frame(card, bg=SURFACE)
        row.pack(fill="x", padx=14, pady=(10, 2))

        name_lbl = tk.Label(row, text=name, bg=SURFACE, fg=FG,
                            font=("Segoe UI", 11, "bold"))
        name_lbl.pack(side="left")

        children = [card, row, name_lbl]
        if name == DEFAULT_PACK:
            badge = tk.Label(row, text=" RECOMMENDED ", bg=OK, fg=BG,
                             font=("Segoe UI", 8, "bold"))
            badge.pack(side="left", padx=(8, 0))
            children.append(badge)

        size_lbl = tk.Label(row, text=info["size"], bg=SURFACE, fg=MUTED,
                            font=("Segoe UI", 10))
        size_lbl.pack(side="right")
        children.append(size_lbl)

        desc = tk.Label(card, text=info["desc"], bg=SURFACE, fg=MUTED,
                        font=("Segoe UI", 9), justify="left", anchor="w",
                        wraplength=580)
        desc.pack(fill="x", padx=14, pady=(0, 10))
        children.append(desc)

        self._pack_cards[name] = card
        for w in children:
            w.config(cursor="hand2")
            w.bind("<Button-1>", lambda _e, n=name: self._update_selection(n))

    def _update_selection(self, name: str) -> None:
        self.pack_var.set(name)
        for n, card in self._pack_cards.items():
            card.config(highlightbackground=ACCENT if n == name else SURFACE)

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
            # Optional Blender for the design pipeline (user is asked first).
            self._maybe_install_blender()
        except SetupError as e:
            # Bind the text now: `e` is cleared when the except block exits, but
            # the messagebox lambda runs later via root.after → would NameError.
            msg = str(e)
            self._set_indeterminate(False)
            self._set_status(f"Setup failed: {msg}", ERR)
            self.root.after(0, lambda: messagebox.showerror("Setup failed", msg))
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
        """Launch JarvisChat.exe (frozen build) or jarvis_chat.py (source)."""
        target = None
        args: list[str] = []

        if getattr(sys, "frozen", False):
            # Installed build: JarvisChat.exe sits next to JarvisSetup.exe
            candidate = Path(sys.executable).parent / "JarvisChat.exe"
            if candidate.is_file():
                target = str(candidate)
                args = ["--chat"]   # bypass the marker check defensively
        if target is None:
            # Dev / source: invoke jarvis_chat.py with pythonw
            chat_py = Path(__file__).resolve().parent / "jarvis_chat.py"
            if chat_py.is_file():
                pyw = Path(sys.executable).with_name("pythonw.exe")
                target = str(pyw if pyw.is_file() else sys.executable)
                args = [str(chat_py)]

        if target:
            try:
                DETACHED  = getattr(subprocess, "DETACHED_PROCESS", 0)
                NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                subprocess.Popen(
                    [target, *args],
                    creationflags=DETACHED | NEW_GROUP,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
            except Exception as e:
                # If launch fails, surface it before closing so the user knows
                messagebox.showwarning(
                    "Couldn't launch JARVIS",
                    f"Setup finished, but JarvisChat.exe didn't start: {e}\n\n"
                    "Open it from the Start Menu instead.")
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

    # --- Optional Blender install ----------------------------------------
    def _maybe_install_blender(self) -> None:
        """If Blender isn't on the system, ask whether to install it now.
        Blender is only needed for the design pipeline ('make a cube using
        blender'); skipping is fine and the rest of JARVIS works without it."""
        if find_blender():
            self._set_status("Blender is already installed.", OK)
            return

        # asksyesno blocks the wizard thread (we're in the background _run worker).
        # Bounce the prompt back onto the Tk main loop and wait for the answer.
        answer_holder: dict[str, bool] = {}
        done = threading.Event()

        def _prompt() -> None:
            answer_holder["yes"] = messagebox.askyesno(
                "Install Blender?",
                "JARVIS Chat can use Blender to generate 3D models when you "
                "ask things like 'make a cube using blender'.\n\n"
                "Install Blender now? (~250 MB download, ~500 MB on disk.)\n\n"
                "You can skip this and install later from blender.org — the "
                "rest of JARVIS works without it.",
                parent=self.root, icon="question", default="yes",
            )
            done.set()
        self.root.after(0, _prompt)
        done.wait(timeout=180)
        if not answer_holder.get("yes"):
            self._set_status("Skipped Blender. Design pipeline will be disabled "
                             "until you install it from blender.org.", WARN)
            return

        # --- Download the MSI ---
        self._set_status("Downloading Blender installer from blender.org…")
        installer = Path(os.environ.get("TEMP", ".")) / "blender-installer.msi"
        try:
            # blender.org's CDN 403's the default Python-urllib UA. Send a
            # real-browser-looking User-Agent so the download goes through.
            req = urllib.request.Request(
                BLENDER_INSTALLER_URL,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "*/*",
                },
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
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
                                f"Downloading Blender… {downloaded // (1024*1024)} "
                                f"/ {total // (1024*1024)} MB")
        except Exception as e:
            # Fall back to opening the browser so the user can install manually
            self._set_status(
                f"Couldn't download the installer ({e}). Opening the Blender "
                "download page in your browser.", WARN)
            try:
                os.startfile(BLENDER_DOWNLOAD_PAGE)   # type: ignore[attr-defined]
            except Exception:
                pass
            return

        # --- Run the installer (silent, elevated via PowerShell + UAC) ---
        self._set_indeterminate(True)
        self._set_status("Running Blender installer — accept the UAC prompt when it appears…")
        ps_cmd = (
            f"Start-Process msiexec.exe -ArgumentList "
            f"'/i','\"{installer}\"','/quiet','/qn','/norestart' "
            f"-Verb RunAs -Wait"
        )
        try:
            DETACHED = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                timeout=600,
                creationflags=DETACHED,
            )
            self._set_indeterminate(False)
            if proc.returncode != 0:
                self._set_status(
                    f"Blender installer returned exit code {proc.returncode}. "
                    "It may have been cancelled. Re-run setup to try again.", WARN)
                return
        except subprocess.TimeoutExpired:
            self._set_indeterminate(False)
            self._set_status("Blender install didn't finish in 10 minutes. "
                             "Check Add/Remove Programs to see if it's there.", WARN)
            return
        except Exception as e:
            self._set_indeterminate(False)
            self._set_status(f"Couldn't run the Blender installer: {e}", ERR)
            return

        if find_blender():
            self._set_status("Blender installed.", OK)
        else:
            self._set_status(
                "Blender installer finished but blender.exe wasn't found at "
                "standard paths. Try restarting JARVIS — it may show up after.",
                WARN)


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
