#!/usr/bin/env python3
"""
JARVIS Chat — licensing (Gumroad subscription gate).

Business model: the app is FREE to download, but using JARVIS requires an active
$25/month Gumroad subscription. Coworkers get free access via comp keys.

    user enters key -> verify against Gumroad License API -> cache result
    -> app runs only while a valid/active license (or comp key, or grace) holds

Gumroad product: stellium6.gumroad.com/l/zigluo  (permalink "zigluo").
Verify endpoint (no secret needed, safe in a public repo):
    POST https://api.gumroad.com/v2/licenses/verify
        product_permalink=zigluo  license_key=<key>  increment_uses_count=false
    -> {"success": true, "purchase": { ...subscription fields... }}

SAFETY / philosophy (matches jarvis_router / jarvis_voice):
- Never hard-crash. Network errors fall back to the cached offline-grace token so
  paying users aren't locked out when Gumroad is unreachable.
- This is a LOCAL app, so the check is inherently bypassable by a determined
  user. That's accepted — the goal is honest gating for normal users + coworkers,
  not unbreakable DRM. Keep no secrets in the binary.

Honesty note: offline grace is stored in plain config (a savvy user could edit
it). Acceptable for this threat model. If stronger enforcement is ever needed,
move inference server-side (host the model, charge for API) — a bigger change.
"""
from __future__ import annotations

import os
import json
import time
import urllib.parse
import urllib.request
import urllib.error

# Gumroad product — permalink is NOT secret.
GUMROAD_VERIFY_URL = "https://api.gumroad.com/v2/licenses/verify"
PRODUCT_PERMALINK = os.environ.get("JARVIS_GUMROAD_PERMALINK", "zigluo")

# How long a successful verification is trusted before we re-check online.
GRACE_DAYS = float(os.environ.get("JARVIS_LICENSE_GRACE_DAYS", "5"))

# Comp keys for coworkers — free access, checked BEFORE any network call so it
# works fully offline and costs nothing. Add coworker codes here (or via the
# JARVIS_COMP_KEYS env var, comma-separated). These are intentionally readable;
# they grant free use, not anything sensitive.
COMP_KEYS = {
    k.strip().upper()
    for k in os.environ.get("JARVIS_COMP_KEYS", "").split(",")
    if k.strip()
}
# Built-in comp codes (hand these to coworkers). Format is free-form; kept
# distinct from Gumroad's XXXXXXXX-... so they can't collide.
COMP_KEYS |= {
    "JARVIS-TEAM-STELLIUM",
}


def _config_dir() -> str:
    """Same location jarvis_chat uses for config (per-machine, never in the exe)."""
    override = os.getenv("JARVIS_CHAT_CONFIG_DIR")
    if override:
        return override
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "JarvisChat")


def _lic_path() -> str:
    return os.path.join(_config_dir(), "license.json")


def _load() -> dict:
    try:
        with open(_lic_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save(d: dict) -> None:
    try:
        os.makedirs(_config_dir(), exist_ok=True)
        with open(_lic_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except OSError:
        pass


def _is_comp(key: str) -> bool:
    return key.strip().upper() in COMP_KEYS


# ── Gumroad verification ──────────────────────────────────────────────────────
def _gumroad_verify(key: str) -> tuple[bool, str, dict]:
    """Call Gumroad. Returns (active, reason, purchase).

    Raises ONLY on a true connection failure (no HTTP response) so the caller can
    fall back to offline grace. IMPORTANT: Gumroad returns HTTP 404 with a valid
    JSON body for an invalid/nonexistent key — that is a DEFINITIVE negative, not
    a network error, so we parse the error body instead of letting HTTPError
    bubble up (otherwise an invalid key would be mistaken for 'offline')."""
    data = urllib.parse.urlencode({
        "product_permalink": PRODUCT_PERMALINK,
        "license_key": key.strip(),
        "increment_uses_count": "false",
    }).encode()
    req = urllib.request.Request(GUMROAD_VERIFY_URL, data=data,
                                 headers={"Content-Type":
                                          "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            obj = json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        # 4xx with a JSON body = Gumroad answered (definitive), not a network down.
        try:
            obj = json.loads(e.read().decode("utf-8", "ignore"))
        except Exception:
            # No parseable body -> treat as a real failure so grace can apply.
            raise urllib.error.URLError(f"HTTP {e.code}")
    if not obj.get("success"):
        return False, obj.get("message", "invalid license key"), {}
    p = obj.get("purchase", {}) or {}
    # Subscription health: any of these set => no longer active.
    if p.get("refunded") or p.get("chargebacked"):
        return False, "purchase refunded", p
    if p.get("subscription_ended_at") or p.get("subscription_cancelled_at") \
            or p.get("subscription_failed_at"):
        return False, "subscription is no longer active", p
    return True, "active", p


def check_key(key: str) -> tuple[bool, str]:
    """Validate a key the user just entered. Comp keys pass instantly. Real keys
    go to Gumroad; on success we cache an offline-grace token. Network failure on
    a brand-new key (never verified before) fails closed; for an already-cached
    key it falls back to grace (handled in is_licensed). Returns (ok, message)."""
    key = (key or "").strip()
    if not key:
        return False, "Enter your license key."
    if _is_comp(key):
        _save({"key": key, "comp": True, "verified_at": int(time.time()),
               "status": "comp"})
        return True, "Comp access activated. Welcome aboard!"
    try:
        active, reason, _ = _gumroad_verify(key)
    except urllib.error.URLError:
        return False, ("Couldn't reach Gumroad to verify. Check your internet "
                       "and try again.")
    except Exception as e:                       # noqa: BLE001
        return False, f"Verification error: {e}"
    if active:
        _save({"key": key, "comp": False, "verified_at": int(time.time()),
               "status": "active"})
        return True, "License verified — thanks for subscribing to JARVIS!"
    return False, f"License not active: {reason}"


def is_licensed() -> tuple[bool, str]:
    """Called on launch. Returns (allowed, status_text). Order:
    1) comp key cached -> allow.
    2) cached active key -> re-verify online; on success refresh grace; on
       network failure allow within GRACE_DAYS of last success.
    3) nothing cached -> not licensed."""
    d = _load()
    key = d.get("key", "")
    if not key:
        return False, "no license"
    if d.get("comp") or _is_comp(key):
        return True, "comp"
    # Try a fresh online check.
    try:
        active, reason, _ = _gumroad_verify(key)
        if active:
            d["verified_at"] = int(time.time())
            d["status"] = "active"
            _save(d)
            return True, "active"
        # Definitive negative from Gumroad (cancelled/refunded) -> revoke.
        d["status"] = reason
        _save(d)
        return False, reason
    except Exception:
        # Offline / Gumroad unreachable -> grace window from last good check.
        last = float(d.get("verified_at", 0))
        if last and (time.time() - last) < GRACE_DAYS * 86400:
            left = GRACE_DAYS - (time.time() - last) / 86400
            return True, f"offline grace ({left:.1f}d left)"
        return False, "offline and grace expired — reconnect to verify"


def current_status() -> str:
    """Human-readable license status for the /license command."""
    d = _load()
    if not d.get("key"):
        return "No license active. Subscribe at stellium6.gumroad.com/l/zigluo"
    if d.get("comp"):
        return "Comp access (free). Thanks for being on the team!"
    ok, status = is_licensed()
    masked = d["key"][:4] + "…" + d["key"][-4:] if len(d["key"]) > 8 else "set"
    return f"License {masked}: {status}"


def sign_out() -> None:
    _save({})


def selftest() -> None:
    # Comp key works offline, no network.
    ok, msg = check_key("JARVIS-TEAM-STELLIUM")
    assert ok, msg
    allowed, status = is_licensed()
    assert allowed and status == "comp", (allowed, status)
    print(f"LICENSE SELFTEST OK  comp -> allowed={allowed} status={status}")
    print(f"  status line: {current_status()}")
    sign_out()
    allowed2, status2 = is_licensed()
    print(f"  after sign_out -> allowed={allowed2} status={status2!r} (should be False/no license)")
    assert not allowed2


if __name__ == "__main__":
    selftest()
