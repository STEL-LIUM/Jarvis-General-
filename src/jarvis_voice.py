#!/usr/bin/env python3
"""
JARVIS Chat — voice subsystem (always-listening, wake-word, local STT + TTS).

Pipeline:
  Mic ─► VAD ─► Wake-word ("hey jarvis") ─► Streaming STT ─► chat.send()
                                                 │
                                                 ▼
                              LLM reply ─► sentence chunker ─► TTS playback

All three engines run locally:
  - openwakeword          → "hey jarvis" detector  (~3 MB onnx, ~1-3% CPU steady)
  - faster-whisper base.en → speech-to-text         (~150 MB, GPU if CUDA else CPU)
  - piper-tts en_US-ryan-high → neural text-to-speech (~60 MB, very fast)

Models auto-download on first start; cached under %LOCALAPPDATA%\\JarvisChat\\models.

This module is import-safe even when the deps aren't installed yet — every
heavy import is deferred to start(). Callers should treat VoiceEngine.start()
as "may raise; if it does, show the error and fall back to text-only".
"""
from __future__ import annotations

import io
import os
import queue
import re
import threading
import time
import urllib.request
import wave
from collections.abc import Callable
from pathlib import Path


# ---- public knobs ----------------------------------------------------------

WAKE_WORD       = "hey_jarvis"     # openwakeword model key
SAMPLE_RATE     = 16000            # 16 kHz mono is the universal voice rate
FRAME_MS        = 80               # audio frame size for wake-word feed
SILENCE_MS      = 2000             # silence AFTER speech that ends an utterance.
                                   # 2s tolerates natural mid-sentence pauses so
                                   # JARVIS doesn't cut you off when you breathe.
MAX_UTTERANCE_MS = 30000           # cap: record at most this long per turn, then
                                   # transcribe whatever we have. High enough that
                                   # long sentences finish; silence ends most turns
                                   # well before this.
# After JARVIS finishes speaking, keep listening for THIS many ms for the next
# turn without needing the wake word again — lets you hold a back-and-forth
# conversation. Falls back to wake-word listening when it elapses in silence.
FOLLOWUP_WINDOW_MS = 7000
WAKE_THRESHOLD     = 0.5           # openwakeword score threshold (0..1)
# Amplitude RMS is used ONLY as an optional EARLY-STOP hint (end sooner once a
# clear voice has clearly stopped). It is NOT used to decide whether you spoke
# — Whisper is the judge of that — so a low-gain mic still works; it just runs
# to the cap instead of stopping early.
VOICE_RMS_THRESHOLD = 120          # int16 RMS above this counts as "voice"
# Barge-in: while JARVIS is SPEAKING, sustained user voice cuts him off so you
# can correct him mid-sentence. Safe ONLY because the user is on headphones —
# the mic can't hear JARVIS's own TTS, so any voice it picks up is really the
# user. (On speakers this would make him interrupt himself; that needs echo
# cancellation.) Require several consecutive loud frames so a cough/click won't
# stop him, and ignore the first GRACE ms so playback can get going.
BARGE_IN_RMS       = int(os.getenv("JARVIS_BARGE_IN_RMS", str(VOICE_RMS_THRESHOLD)))
BARGE_IN_FRAMES    = 4             # consecutive frames above BARGE_IN_RMS (~320 ms)
BARGE_IN_GRACE_MS  = 500           # ignore mic this long after speech starts
PREROLL_MS         = 600           # audio kept BEFORE wake-fire (catches the
                                   # tail of "jarvis" plus the start of the
                                   # question when the user runs them together)

# Voice-clone dataset capture. While voice is on, the mic is continuously
# segmented by energy and every speech clip + its Whisper transcript is saved
# in LJSpeech layout (voice_dataset/wavs/*.wav + metadata.csv) so a TTS model
# can later be trained on the user's OWN voice. Capture is SUSPENDED whenever
# JARVIS is speaking, so his synthetic voice can never contaminate the clone
# dataset (a polluted dataset would teach the clone to sound like Piper).
DATASET_SILENCE_MS = 700           # trailing silence that closes a speech clip
DATASET_MIN_MS     = 1000          # discard clips shorter than this (blips/noise)
DATASET_MAX_MS     = 15000         # force-close a clip that runs this long

# Playback volume for JARVIS's TTS (1.0 = Piper's native level). 0.8 = 20%
# quieter; Piper output runs hot. Override with $JARVIS_TTS_VOLUME.
TTS_VOLUME = float(os.getenv("JARVIS_TTS_VOLUME", "0.8"))

# Model registry — small enough that hard-coding wins over a config file.
# tiny.en is ~5x faster than base.en on CPU and still nails everyday English
# at conversational speeds. Override with $JARVIS_WHISPER_MODEL_SIZE to swap.
WHISPER_MODEL_SIZE = os.getenv("JARVIS_WHISPER_MODEL_SIZE", "tiny.en")
PIPER_VOICE        = "en_US-ryan-high"

# Download URLs — pin to specific releases so first-run downloads are stable.
WAKE_MODEL_URL = (
    "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
    "hey_jarvis_v0.1.onnx"
)
WAKE_MELSPEC_URL = (
    "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
    "melspectrogram.onnx"
)
WAKE_EMBED_URL = (
    "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
    "embedding_model.onnx"
)
PIPER_VOICE_ONNX_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "en/en_US/ryan/high/en_US-ryan-high.onnx?download=true"
)
PIPER_VOICE_JSON_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "en/en_US/ryan/high/en_US-ryan-high.onnx.json?download=true"
)

# Pretend to be a real browser — some of these CDNs reject the default UA.
_DOWNLOAD_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _models_dir() -> Path:
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    p = Path(base) / "JarvisChat" / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _wake_dir() -> Path:
    p = _models_dir() / "openwakeword"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _piper_dir() -> Path:
    p = _models_dir() / "piper"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _dataset_wav_dir() -> Path:
    """LJSpeech wavs/ dir for the voice-clone dataset. metadata.csv lives in
    the parent (voice_dataset/), mirroring the LJSpeech layout trainers expect."""
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    p = Path(base) / "JarvisChat" / "voice_dataset" / "wavs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _download(url: str, dest: Path, progress_cb: Callable[[str], None] | None = None) -> None:
    """Stream a URL to `dest`. Resumes-safe by writing to .part first."""
    if dest.exists() and dest.stat().st_size > 0:
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": _DOWNLOAD_UA})
    if progress_cb:
        progress_cb(f"downloading {dest.name}…")
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length", "0"))
        got = 0
        last_pct = -1
        chunk = 256 * 1024
        while True:
            buf = r.read(chunk)
            if not buf:
                break
            f.write(buf)
            got += len(buf)
            if progress_cb and total:
                pct = int(got * 100 / total)
                if pct != last_pct and pct % 5 == 0:
                    progress_cb(f"downloading {dest.name}… {pct}%")
                    last_pct = pct
    tmp.replace(dest)
    if progress_cb:
        progress_cb(f"got {dest.name}")


def _fetch_whisper_snapshot(progress_cb: Callable[[str], None] | None = None) -> None:
    """Pre-fetch the faster-whisper model snapshot, then replace HF Hub's
    symlinks with real file copies. ctranslate2 (Whisper's C++ backend) can
    fail to open files through Windows symlinks under some non-admin / non-
    DeveloperMode configurations, returning the misleading 'Unable to open
    file model.bin' error even though the symlink targets exist and are
    readable from Python. Forcing copies guarantees the C++ open() works.
    """
    import shutil
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub import constants as _hf_const
    except Exception:
        return  # huggingface_hub didn't bundle — let WhisperModel handle it

    repo_id = f"Systran/faster-whisper-{WHISPER_MODEL_SIZE}"
    cache_root = Path(_hf_const.HF_HUB_CACHE)
    repo_dir = cache_root / f"models--{repo_id.replace('/', '--')}"

    # Heal: drop any 0-byte real files in snapshot dirs so HF Hub will
    # re-create them from the blobs on next download call.
    if (repo_dir / "snapshots").is_dir():
        for snap in (repo_dir / "snapshots").iterdir():
            if not snap.is_dir():
                continue
            for f in snap.iterdir():
                try:
                    if f.is_file() and f.stat().st_size == 0 and not f.is_symlink():
                        if progress_cb:
                            progress_cb(f"healing 0-byte: {f.name}")
                        f.unlink()
                except Exception:
                    pass

    if progress_cb:
        progress_cb(f"downloading whisper {WHISPER_MODEL_SIZE}…")
    try:
        snapshot_download(repo_id=repo_id)
    except Exception as e:
        if progress_cb:
            progress_cb(f"whisper prefetch warning: {e}")

    # Materialize: walk the snapshot dir and replace each symlink with a copy
    # of the blob it points to. Idempotent — if it's already a real file, skip.
    if not (repo_dir / "snapshots").is_dir():
        return
    for snap in (repo_dir / "snapshots").iterdir():
        if not snap.is_dir():
            continue
        for f in snap.iterdir():
            try:
                # is_symlink() catches both file symlinks AND junctions; reparse
                # points need the extra check on Windows.
                if not f.is_symlink():
                    continue
                target = Path(os.readlink(f))
                if not target.is_absolute():
                    target = (f.parent / target).resolve()
                if not target.is_file():
                    continue
                if progress_cb:
                    progress_cb(f"materializing {f.name} ({target.stat().st_size // 1024} KB)")
                f.unlink()
                shutil.copy2(target, f)
            except Exception as e:
                if progress_cb:
                    progress_cb(f"materialize warning ({f.name}): {e}")


def ensure_models(progress_cb: Callable[[str], None] | None = None) -> dict:
    """Make sure all model files are on disk. Returns a dict of paths.
    Safe to call repeatedly — only downloads what's missing."""
    wake_dir = _wake_dir()
    piper_dir = _piper_dir()

    wake_model_path  = wake_dir / "hey_jarvis_v0.1.onnx"
    melspec_path     = wake_dir / "melspectrogram.onnx"
    embed_path       = wake_dir / "embedding_model.onnx"
    piper_onnx_path  = piper_dir / f"{PIPER_VOICE}.onnx"
    piper_json_path  = piper_dir / f"{PIPER_VOICE}.onnx.json"

    _download(WAKE_MODEL_URL,       wake_model_path,  progress_cb)
    _download(WAKE_MELSPEC_URL,     melspec_path,     progress_cb)
    _download(WAKE_EMBED_URL,       embed_path,       progress_cb)
    _download(PIPER_VOICE_ONNX_URL, piper_onnx_path,  progress_cb)
    _download(PIPER_VOICE_JSON_URL, piper_json_path,  progress_cb)

    # Whisper: pre-materialize the snapshot ourselves. Doing this BEFORE
    # instantiating WhisperModel avoids the Windows symlink-fallback bug
    # that leaves the snapshot dir full of 0-byte files.
    _fetch_whisper_snapshot(progress_cb)
    return {
        "wake_model":   str(wake_model_path),
        "wake_melspec": str(melspec_path),
        "wake_embed":   str(embed_path),
        "piper_onnx":   str(piper_onnx_path),
        "piper_json":   str(piper_json_path),
    }


# ---- VoiceEngine -----------------------------------------------------------

class VoiceEngine:
    """Always-listening voice loop. Public API:
       - start(on_voice_text, progress_cb=None) → begins listening
       - stop()                                  → tears everything down
       - speak(text)                             → enqueue TTS playback
       - state                                   → 'idle' | 'listening' | 'speaking' | 'off'
    """

    STATE_OFF       = "off"
    STATE_IDLE      = "idle"        # listening for the wake word
    STATE_LISTENING = "listening"   # wake word fired, recording utterance
    STATE_SPEAKING  = "speaking"    # TTS playing
    STATE_FOLLOWUP  = "followup"    # just spoke; listening for next turn w/o wake

    def __init__(self):
        self.state = self.STATE_OFF
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._on_voice_text: Callable[[str], None] | None = None
        self._status_cb: Callable[[str], None] | None = None
        self._tts_queue: queue.Queue[str | None] = queue.Queue()
        self._audio_buffer: list[bytes] = []   # for the active utterance
        # Rolling pre-roll: keep the last PREROLL_MS of audio at all times so
        # when the wake-word fires we don't lose the first beat of speech.
        self._preroll: list[bytes] = []
        self._preroll_frames = max(1, PREROLL_MS // FRAME_MS)
        self._utterance_started_ms: float = 0
        self._last_voice_ms: float = 0
        self._heard_voice = False   # has the user actually started talking yet?
        self._followup_deadline_ms: float = 0   # convo window end (STATE_FOLLOWUP)
        self._speaking_started_ms: float = 0     # when current TTS playback began
        self._barge_in_count = 0                 # consecutive loud frames while speaking
        self._barge_in = False                   # user interrupted current playback
        self._end_conversation = False           # goodbye said: drop to wake-word after speaking
        # Conversation mode (opt-in). OFF (default): every turn needs the wake
        # word "hey jarvis" — robust in noisy / multi-person rooms, he never
        # reacts to ambient chatter or other people. ON: after he replies he
        # listens for a wake-word-free follow-up and any voice can interrupt —
        # natural back-and-forth, but only safe in a quiet single-speaker setup.
        self.convo_mode = False
        # Voice-clone dataset capture (continuous, energy-segmented).
        self.dataset_enabled = True              # set from config by the chat app
        self._whisper_lock = threading.Lock()    # serialize STT (command + dataset)
        self._ds_buffer: list[bytes] = []        # current speech clip
        self._ds_in_speech = False
        self._ds_started_ms: float = 0
        self._ds_last_voice_ms: float = 0
        self._engines_loaded = False
        # Deferred handles
        self._sd = None
        self._np = None
        self._wake = None
        self._whisper = None
        self._piper = None
        self._mic_stream = None

    # ---- lifecycle --------------------------------------------------------

    def start(self,
              on_voice_text: Callable[[str], None],
              status_cb: Callable[[str], None] | None = None) -> None:
        if self.state != self.STATE_OFF:
            return
        self._on_voice_text = on_voice_text
        self._status_cb     = status_cb
        self._stop_event.clear()
        self._status("loading voice models…")
        self._load_engines()   # downloads any missing models, then loads engines
        self.state = self.STATE_IDLE
        t1 = threading.Thread(target=self._mic_loop,  name="voice-mic",  daemon=True)
        t2 = threading.Thread(target=self._tts_loop,  name="voice-tts",  daemon=True)
        self._threads = [t1, t2]
        for t in self._threads:
            t.start()
        self._status("voice ready — say 'hey jarvis'")

    def stop(self) -> None:
        if self.state == self.STATE_OFF:
            return
        self._stop_event.set()
        self._tts_queue.put(None)   # poison pill
        try:
            if self._mic_stream is not None:
                self._mic_stream.close()
        except Exception:
            pass
        for t in self._threads:
            t.join(timeout=1.5)
        self._threads = []
        self.state = self.STATE_OFF
        self._status("voice off")

    def speak(self, text: str) -> None:
        """Queue a string for TTS playback. Long strings get split into
        sentences so playback can start before the rest is synthesized.
        No-ops silently if the engine is off or there's nothing speakable."""
        if self.state == self.STATE_OFF or not text:
            return
        cleaned = self._clean_for_tts(text)
        if not cleaned.strip():
            return
        for sentence in self._split_sentences(cleaned):
            if sentence.strip():
                self._tts_queue.put(sentence.strip())

    # ---- internals --------------------------------------------------------

    def _status(self, msg: str) -> None:
        if self._status_cb:
            try:
                self._status_cb(msg)
            except Exception:
                pass

    def _load_engines(self) -> None:
        if self._engines_loaded:
            return
        # All heavy imports deferred to here so the main app can start without
        # voice deps installed.
        import numpy as np
        import sounddevice as sd
        from openwakeword.model import Model as OWWModel
        from faster_whisper import WhisperModel
        try:
            from piper import PiperVoice
        except ImportError:
            # Older piper-tts API
            from piper.voice import PiperVoice   # type: ignore

        self._np  = np
        self._sd  = sd

        paths = ensure_models(self._status)

        # Wake-word — openwakeword expects model paths in `wakeword_models`
        # and locates melspec / embedding via its package data unless we
        # override. We pre-download them so even an offline first-run works.
        try:
            self._wake = OWWModel(
                wakeword_models=[paths["wake_model"]],
                melspec_model_path=paths["wake_melspec"],
                embedding_model_path=paths["wake_embed"],
                inference_framework="onnx",
            )
        except TypeError:
            # Older openwakeword signature
            self._wake = OWWModel(wakeword_models=[paths["wake_model"]])

        # Whisper — try GPU first, gracefully fall back to CPU. If we hit
        # the Windows "Unable to open file 'model.bin'" snapshot-link bug,
        # heal the cache and retry once.
        def _warmup(m):
            # ctranslate2 loads cuBLAS/cuDNN lazily on the FIRST encode, so a
            # successful constructor does NOT prove CUDA works. Force a tiny
            # encode here so a missing cublas64_12.dll fails now — letting us
            # fall back to CPU — instead of throwing on every real utterance.
            segs, _ = m.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32),
                                   language="en", beam_size=1)
            for _ in segs:
                pass

        def _make_whisper():
            try:
                m = WhisperModel(WHISPER_MODEL_SIZE, device="cuda",
                                 compute_type="float16")
                _warmup(m)
                return m
            except Exception:
                m = WhisperModel(WHISPER_MODEL_SIZE, device="cpu",
                                 compute_type="int8")
                _warmup(m)
                return m
        try:
            self._whisper = _make_whisper()
        except Exception as e:
            if "model.bin" in str(e) or "Unable to open file" in str(e):
                self._status("whisper cache broken — healing and retrying…")
                _fetch_whisper_snapshot(self._status)
                self._whisper = _make_whisper()
            else:
                raise

        # Piper — PiperVoice.load reads the .onnx and .onnx.json side-by-side.
        self._piper = PiperVoice.load(paths["piper_onnx"])

        self._engines_loaded = True

    def _mic_loop(self) -> None:
        """Continuously read audio. Two sub-modes:
           - IDLE: every frame goes to the wake-word recognizer.
           - LISTENING: frames are buffered, watched for end-of-utterance.
        """
        np = self._np
        sd = self._sd
        frame_samples = int(SAMPLE_RATE * FRAME_MS / 1000)

        def callback(indata, frames, time_info, status):
            if self._stop_event.is_set():
                raise sd.CallbackStop()
            # int16 mono — what every voice engine wants
            audio_int16 = (indata[:, 0] * 32767).astype(np.int16)
            self._process_frame(audio_int16)

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=frame_samples,
                callback=callback,
            ) as stream:
                self._mic_stream = stream
                while not self._stop_event.wait(0.1):
                    pass
        except Exception as e:
            self._status(f"voice mic error: {e}")
            self.state = self.STATE_OFF

    def _process_frame(self, audio_int16) -> None:
        np = self._np
        now_ms = time.time() * 1000

        # Continuous voice-clone capture runs alongside the command state
        # machine. Skip it while SPEAKING so JARVIS's TTS never lands in the
        # dataset (on headphones the mic can't hear him anyway, but this makes
        # it true regardless of audio setup).
        if self.dataset_enabled and self.state != self.STATE_SPEAKING:
            self._feed_dataset(audio_int16, now_ms)

        if self.state == self.STATE_IDLE:
            # Always keep the last PREROLL_MS of audio in a rolling buffer —
            # cheap, and saves us when the user runs the wake-word straight
            # into the question without a pause.
            self._preroll.append(audio_int16.tobytes())
            if len(self._preroll) > self._preroll_frames:
                self._preroll = self._preroll[-self._preroll_frames:]
            # Feed the wake-word model. It wants float32 in [-1, 1] OR int16.
            try:
                scores = self._wake.predict(audio_int16)
            except Exception:
                return
            score = max(scores.values()) if scores else 0.0
            if score >= WAKE_THRESHOLD:
                self._status("listening…")
                self.state = self.STATE_LISTENING
                # Seed the utterance buffer with the pre-roll so we capture
                # any speech that ran right into the wake word.
                self._audio_buffer = list(self._preroll)
                self._preroll = []
                self._utterance_started_ms = now_ms
                self._last_voice_ms = now_ms
                self._heard_voice = False
                self._status("listening… (go ahead)")

        elif self.state == self.STATE_FOLLOWUP:
            # JARVIS just finished talking. For a short window, let the user
            # reply WITHOUT the wake word so it feels like a real conversation.
            # Keep the pre-roll rolling and still honor "hey jarvis".
            self._preroll.append(audio_int16.tobytes())
            if len(self._preroll) > self._preroll_frames:
                self._preroll = self._preroll[-self._preroll_frames:]
            try:
                scores = self._wake.predict(audio_int16)
            except Exception:
                scores = {}
            score = max(scores.values()) if scores else 0.0
            rms = float(np.sqrt(np.mean(audio_int16.astype(np.float32) ** 2)))
            if score >= WAKE_THRESHOLD or rms >= VOICE_RMS_THRESHOLD:
                # User started talking (or re-waked) → capture this turn.
                self.state = self.STATE_LISTENING
                self._audio_buffer = list(self._preroll)
                self._preroll = []
                self._utterance_started_ms = now_ms
                self._last_voice_ms = now_ms
                self._heard_voice = rms >= VOICE_RMS_THRESHOLD
                self._status("listening… (go ahead)")
            elif now_ms >= self._followup_deadline_ms:
                self.state = self.STATE_IDLE
                self._preroll = []
                self._status("listening for 'hey jarvis'…")

        elif self.state == self.STATE_SPEAKING:
            # Barge-in. Keep the pre-roll rolling so the first words of the
            # interruption aren't lost.
            self._preroll.append(audio_int16.tobytes())
            if len(self._preroll) > self._preroll_frames:
                self._preroll = self._preroll[-self._preroll_frames:]
            if now_ms - self._speaking_started_ms < BARGE_IN_GRACE_MS:
                return
            # The wake word ALWAYS interrupts — that's how you reclaim the floor
            # no matter who else is talking. Raw-volume barge-in is opt-in
            # (convo_mode) because otherwise ambient chatter / other people cut
            # JARVIS off constantly.
            interrupt = False
            try:
                scores = self._wake.predict(audio_int16)
            except Exception:
                scores = {}
            if (max(scores.values()) if scores else 0.0) >= WAKE_THRESHOLD:
                interrupt = True
            if self.convo_mode:
                rms = float(np.sqrt(np.mean(audio_int16.astype(np.float32) ** 2)))
                if rms >= BARGE_IN_RMS:
                    self._barge_in_count += 1
                else:
                    self._barge_in_count = 0
                if self._barge_in_count >= BARGE_IN_FRAMES:
                    interrupt = True
            if interrupt:
                # User reclaimed the floor → cut JARVIS off and capture the
                # correction immediately. The TTS loop sees _barge_in and bails
                # out of its finally (won't drag us back to FOLLOWUP).
                self._barge_in_count = 0
                self._barge_in = True
                # _process_frame runs inside PortAudio's input callback; calling
                # the (blocking) sd.stop() here could deadlock the audio thread,
                # so cut playback from a throwaway thread instead.
                threading.Thread(target=self._stop_playback, daemon=True).start()
                self.state = self.STATE_LISTENING
                self._audio_buffer = list(self._preroll)
                self._preroll = []
                self._utterance_started_ms = now_ms
                self._last_voice_ms = now_ms
                self._heard_voice = True
                self._status("listening… (go ahead)")

        elif self.state == self.STATE_LISTENING:
            self._audio_buffer.append(audio_int16.tobytes())
            rms = float(np.sqrt(np.mean(audio_int16.astype(np.float32) ** 2)))
            if rms >= VOICE_RMS_THRESHOLD:
                self._heard_voice = True       # optional early-stop hint only
                self._last_voice_ms = now_ms

            elapsed = now_ms - self._utterance_started_ms
            silence_gap = now_ms - self._last_voice_ms

            # End the recording when EITHER:
            #   • we clearly heard voice and it's been quiet for SILENCE_MS
            #     (fast early-stop on a good mic), OR
            #   • we hit the cap (low-gain mic where RMS never tripped — we
            #     still record the window and let Whisper find the speech).
            early_stop = self._heard_voice and silence_gap >= SILENCE_MS
            if early_stop or elapsed >= MAX_UTTERANCE_MS:
                pcm = b"".join(self._audio_buffer)
                self._audio_buffer = []
                self.state = self.STATE_IDLE
                threading.Thread(
                    target=self._transcribe_and_dispatch,
                    args=(pcm,),
                    daemon=True,
                ).start()

    # ---- voice-clone dataset capture --------------------------------------

    def _feed_dataset(self, audio_int16, now_ms: float) -> None:
        """Energy-segment the mic into speech clips for the clone dataset.
        Runs on the audio callback thread — keep it cheap (RMS + buffering);
        the expensive transcribe/save is offloaded to a worker on close."""
        np = self._np
        rms = float(np.sqrt(np.mean(audio_int16.astype(np.float32) ** 2)))
        if rms >= VOICE_RMS_THRESHOLD:
            if not self._ds_in_speech:
                self._ds_in_speech = True
                self._ds_started_ms = now_ms
                self._ds_buffer = []
            self._ds_buffer.append(audio_int16.tobytes())
            self._ds_last_voice_ms = now_ms
        elif self._ds_in_speech:
            # Trailing silence — keep a little so word tails aren't clipped.
            self._ds_buffer.append(audio_int16.tobytes())
            if now_ms - self._ds_last_voice_ms >= DATASET_SILENCE_MS:
                self._close_dataset_segment()
                return
        if self._ds_in_speech and now_ms - self._ds_started_ms >= DATASET_MAX_MS:
            self._close_dataset_segment()

    def _close_dataset_segment(self) -> None:
        pcm = b"".join(self._ds_buffer)
        self._ds_buffer = []
        self._ds_in_speech = False
        dur_ms = len(pcm) / 2 / SAMPLE_RATE * 1000
        if dur_ms < DATASET_MIN_MS:
            return
        threading.Thread(target=self._save_dataset_clip,
                         args=(pcm,), daemon=True).start()

    def _save_dataset_clip(self, pcm_bytes: bytes) -> None:
        """Transcribe a captured clip and, if it's real speech, write the
        WAV + an LJSpeech metadata row. Clips that transcribe to nothing are
        dropped so the dataset stays clean."""
        np = self._np
        try:
            audio_i16 = np.frombuffer(pcm_bytes, dtype=np.int16)
            audio_f32 = audio_i16.astype(np.float32) / 32767.0
            peak = float(np.abs(audio_f32).max())
            if 0.0 < peak < 0.5:
                audio_f32 = audio_f32 * (0.9 / peak)
            with self._whisper_lock:
                segments, _info = self._whisper.transcribe(
                    audio_f32, language="en", beam_size=1,
                    vad_filter=False, no_speech_threshold=0.4)
                text = " ".join(s.text.strip() for s in segments).strip()
            if len(text) < 2:
                return   # noise / silence → don't pollute the dataset
            stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time()*1000)%1000:03d}"
            clip_id = f"clip_{stamp}"
            wav_dir = _dataset_wav_dir()
            with wave.open(str(wav_dir / f"{clip_id}.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(pcm_bytes)
            safe = text.replace("|", " ").replace("\n", " ").strip()
            meta = wav_dir.parent / "metadata.csv"
            with open(meta, "a", encoding="utf-8") as f:
                f.write(f"{clip_id}|{safe}|{safe}\n")
        except Exception:
            pass

    def dataset_status(self) -> str:
        """Human-readable status line for the /voice record command."""
        wav_dir = _dataset_wav_dir()
        meta = wav_dir.parent / "metadata.csv"
        n = 0
        if meta.exists():
            try:
                with open(meta, encoding="utf-8") as f:
                    n = sum(1 for ln in f if ln.strip())
            except Exception:
                pass
        state = "ON" if self.dataset_enabled else "OFF"
        return f"{state} · {n} clips saved · {wav_dir.parent}"

    def _stop_playback(self) -> None:
        """Abort any in-flight TTS playback. Called off the audio callback
        thread (sd.stop can block, which is illegal inside a PortAudio cb)."""
        try:
            self._sd.stop()
        except Exception:
            pass

    def _transcribe_and_dispatch(self, pcm_bytes: bytes) -> None:
        """Run Whisper on raw PCM and fire the on_voice_text callback."""
        np = self._np
        t0 = time.time()
        try:
            secs = len(pcm_bytes) / 2 / SAMPLE_RATE
            self._status(f"transcribing {secs:.1f}s of audio…")
            audio_i16 = np.frombuffer(pcm_bytes, dtype=np.int16)
            audio_f32 = audio_i16.astype(np.float32) / 32767.0
            rms = float(np.sqrt(np.mean(audio_f32 ** 2))) * 32767.0
            # Peak-normalize quiet recordings so Whisper hears them. Low-gain
            # mics produce tiny amplitudes; scaling the peak up to ~0.9 makes
            # quiet speech transcribe reliably without affecting loud audio.
            peak = float(np.abs(audio_f32).max())
            if 0.0 < peak < 0.5:
                audio_f32 = audio_f32 * (0.9 / peak)
            # Whisper's built-in VAD filter is aggressive — on quiet mics it
            # strips the entire utterance. We do our own silence detection
            # upstream, so let Whisper see the full audio.
            with self._whisper_lock:
                segments, _info = self._whisper.transcribe(
                    audio_f32,
                    language="en",
                    beam_size=1,
                    vad_filter=False,
                    no_speech_threshold=0.4,
                )
                text = " ".join(s.text.strip() for s in segments).strip()
            elapsed = time.time() - t0
            if text and self._is_goodbye(text):
                # User dismissed JARVIS — acknowledge briefly and drop back to
                # wake-word-only so he stops listening for a reply. Never sent
                # to the LLM.
                self._status("goodbye — say 'hey jarvis' to talk again")
                self._end_conversation = True
                self.speak("Goodbye.")
                return
            if text and self._on_voice_text:
                self._status(f"heard: {text[:60]}  ({elapsed:.1f}s)")
                self._on_voice_text(text)
                return
            elif rms < 80:
                self._status("(empty — mic was quiet. Try speaking up or "
                             "moving closer.)")
            else:
                self._status("(didn't catch that — please try again.)")
        except Exception as e:
            self._status(f"transcription failed: {e}")

    def _tts_loop(self) -> None:
        """Pull sentences off the queue, synthesize via Piper, play via sd."""
        np = self._np
        sd = self._sd
        # Piper 1.x API: synthesize_wav(text, wave_writer) writes a complete
        # WAV stream to the given wave.Wave_write. Older versions used
        # synthesize(text, wav) — fall back to that if synthesize_wav is
        # missing so the module survives a future API rename.
        synth = (getattr(self._piper, "synthesize_wav", None)
                 or getattr(self._piper, "synthesize", None))
        if synth is None:
            self._status("tts unavailable: no synthesize method on Piper")
            return
        while not self._stop_event.is_set():
            try:
                first = self._tts_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if first is None:    # poison pill
                break
            # Drain every sentence already queued for this reply and play them
            # as ONE continuous buffer. Per-sentence sd.play() calls open/close
            # the device each time, which clips each clip's tail (the "sudden
            # cut-off") and inserts audible gaps at sentence seams.
            batch = [first]
            stop_after = False
            while True:
                try:
                    nxt = self._tts_queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is None:
                    stop_after = True
                    break
                batch.append(nxt)
            try:
                self._barge_in = False
                self._barge_in_count = 0
                self._speaking_started_ms = time.time() * 1000
                self.state = self.STATE_SPEAKING
                self._status(f"speaking: {batch[0][:60]}…")
                rate = None
                parts = []
                for s in batch:
                    buf = io.BytesIO()
                    with wave.open(buf, "wb") as wav:
                        synth(s, wav)
                    buf.seek(0)
                    with wave.open(buf, "rb") as wav:
                        rate = wav.getframerate()
                        parts.append(np.frombuffer(
                            wav.readframes(wav.getnframes()), dtype=np.int16))
                if rate and parts:
                    gap  = np.zeros(int(rate * 0.06), dtype=np.int16)  # seam
                    tail = np.zeros(int(rate * 0.15), dtype=np.int16)  # anti-clip
                    joined = []
                    for i, p in enumerate(parts):
                        joined.append(p)
                        if i < len(parts) - 1:
                            joined.append(gap)
                    joined.append(tail)
                    audio = np.concatenate(joined)
                    if TTS_VOLUME != 1.0:
                        # Scale in float to avoid int16 wraparound, then clip.
                        audio = np.clip(audio.astype(np.float32) * TTS_VOLUME,
                                        -32768, 32767).astype(np.int16)
                    sd.play(audio, samplerate=rate, blocking=True)
            except Exception as e:
                self._status(f"tts error: {e}")
            finally:
                if self._barge_in:
                    # User cut JARVIS off. Discard the rest of this reply; the
                    # mic thread already flipped us to LISTENING to capture the
                    # correction. Leave state alone.
                    self._barge_in = False
                    while True:
                        try:
                            if self._tts_queue.get_nowait() is None:
                                stop_after = True
                        except queue.Empty:
                            break
                elif self._end_conversation and self._tts_queue.empty():
                    # Goodbye just finished playing — go quiet (wake-word only),
                    # don't open a follow-up window.
                    self._end_conversation = False
                    self._preroll = []
                    self.state = self.STATE_IDLE
                    self._status("listening for 'hey jarvis'…")
                elif self._tts_queue.empty() and self.state == self.STATE_SPEAKING:
                    self._preroll = []
                    if self.convo_mode:
                        # Conversation mode: listen for a wake-word-free reply.
                        self._followup_deadline_ms = (time.time() * 1000
                                                      + FOLLOWUP_WINDOW_MS)
                        self.state = self.STATE_FOLLOWUP
                        self._status("listening… (just reply, or say 'hey jarvis')")
                    else:
                        # Default: back to wake word — don't react to ambient talk.
                        self.state = self.STATE_IDLE
                        self._status("listening for 'hey jarvis'…")
            if stop_after:
                break

    # ---- text utilities ---------------------------------------------------

    _CODE_FENCE_RE  = re.compile(r"```.*?```", re.DOTALL)
    _INLINE_CODE_RE = re.compile(r"`[^`]+`")
    _MARKDOWN_RE    = re.compile(r"[*_#>`]+")
    _URL_RE         = re.compile(r"https?://\S+")
    _SENTENCE_RE    = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")

    # Whole-utterance goodbye phrases that end the conversation. Matched against
    # the transcript stripped to letters/spaces with a trailing "jarvis" removed,
    # so it only fires on a bare farewell ("bye", "ok thanks bye jarvis") and not
    # when "bye" appears inside a real request.
    _GOODBYE_PHRASES = frozenset({
        "bye", "byebye", "bye bye", "goodbye", "good bye", "good night",
        "goodnight", "see you", "see ya", "see you later", "thats all",
        "that is all", "thats it", "go to sleep", "stop listening", "stop",
        "im done", "i am done", "were done", "we are done", "nevermind",
        "never mind", "dismissed", "okay bye", "ok bye", "thanks bye",
        "thank you bye", "ok thanks", "okay thanks", "thanks thats all",
        "thank you thats all",
    })

    @classmethod
    def _is_goodbye(cls, text: str) -> bool:
        t = re.sub(r"[^a-z\s]", "", text.lower())
        t = re.sub(r"\bjarvis\b", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t in cls._GOODBYE_PHRASES

    # Math symbols → spoken words, so Piper says "pi times pi" instead of
    # choking on the glyphs. Applied after markdown stripping.
    _MATH_SPEAK = {
        "×": " times ", "÷": " divided by ", "·": " times ", "±": " plus or minus ",
        "≈": " approximately ", "≠": " not equal to ", "≤": " less than or equal to ",
        "≥": " greater than or equal to ", "≡": " equals ", "∞": " infinity ",
        "√": " square root of ", "π": " pi ", "τ": " tau ", "θ": " theta ",
        "φ": " phi ", "α": " alpha ", "β": " beta ", "γ": " gamma ",
        "δ": " delta ", "Δ": " delta ", "λ": " lambda ", "μ": " mu ",
        "σ": " sigma ", "ω": " omega ", "→": " to ", "⇒": " implies ",
        "²": " squared ", "³": " cubed ", "⁰": " to the zero", "⁴": " to the fourth",
        "⁺": " plus ", "⁻": " minus ",
    }

    @classmethod
    def _clean_for_tts(cls, text: str) -> str:
        text = cls._CODE_FENCE_RE.sub(" [code block omitted] ", text)
        text = cls._INLINE_CODE_RE.sub(lambda m: m.group(0).strip("`"), text)
        text = cls._URL_RE.sub(" link ", text)
        text = cls._MARKDOWN_RE.sub("", text)
        for sym, word in cls._MATH_SPEAK.items():
            text = text.replace(sym, word)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()

    @classmethod
    def _split_sentences(cls, text: str) -> list[str]:
        out = cls._SENTENCE_RE.split(text)
        # Cap individual sentences at ~600 chars so a 5-paragraph reply
        # doesn't lock the TTS thread for 30s on one synthesize() call.
        result: list[str] = []
        for s in out:
            while len(s) > 600:
                cut = s.rfind(" ", 0, 600)
                if cut < 200:
                    cut = 600
                result.append(s[:cut])
                s = s[cut:].lstrip()
            if s.strip():
                result.append(s)
        return result
