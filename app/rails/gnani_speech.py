"""Gnani Speech APIs (Vachana) — text-to-speech and speech-to-text, with a hard call budget.

This is the rail the `vach_…` key opens. It is NOT the Inya Agent Builder Platform API (outbound calls,
`https://api.inya.ai/platform`, `x-api-key` with `agents` permission) that `gnani.py` targets. Two products,
two keys:

    vach_… key  →  https://api.vachana.ai        STT (Prisma v2.5) and TTS (Timbre v2)      ← this file
    agents key  →  https://api.inya.ai/platform   agent config + trigger_call + call stats   ← gnani.py

Pricing (Gnani API dashboard, 16 Sep 2026): STT ₹27 per audio-hour, TTS ₹27 per 10,000 characters,
rate limit 60 requests/minute. Cheap per call, but the account credit is finite, so every call goes through
`CallBudget`, and every TTS result is cached by content so a static sentence is synthesised once.

Endpoints (docs.gnani.ai quick start):
    POST https://api.vachana.ai/api/v1/tts/inference   JSON  {text, voice, model, audio_config}   → audio bytes
    POST https://api.vachana.ai/stt/v3                  multipart {audio_file, language_code}      → {success, transcript}
Auth header on both: X-API-Key-ID: <key>

Environment:
    GNANI_SPEECH_KEY         the vach_ key (never commit it; .env is git-ignored)
    GNANI_CALL_BUDGET=40     hard cap on billable calls for this checkout (counter kept in .gnani_budget.json)
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import httpx

TTS_URL = "https://api.vachana.ai/api/v1/tts/inference"
STT_URL = "https://api.vachana.ai/stt/v3"
ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "cache" / "tts"
BUDGET_FILE = ROOT / ".gnani_budget.json"

TTS_RATE_PER_CHAR = 27 / 10_000          # ₹
STT_RATE_PER_SEC = 27 / 3600             # ₹


class BudgetExceeded(RuntimeError):
    pass


class CallBudget:
    """A counter on disk. Refuses the (n+1)th billable call. Survives restarts; delete the file to reset."""

    def __init__(self, cap: int | None = None, path: Path = BUDGET_FILE):
        self.cap = cap if cap is not None else int(os.environ.get("GNANI_CALL_BUDGET", "40"))
        self.path = path
        self.state = {"used": 0, "spent_inr": 0.0, "log": []}
        if path.exists():
            self.state = json.loads(path.read_text())

    @property
    def remaining(self) -> int:
        return self.cap - self.state["used"]

    def charge(self, kind: str, units: float, note: str = "") -> None:
        if self.remaining <= 0:
            raise BudgetExceeded(f"Gnani call budget exhausted ({self.cap}). Delete {self.path.name} to reset, deliberately.")
        cost = units * (TTS_RATE_PER_CHAR if kind == "tts" else STT_RATE_PER_SEC)
        self.state["used"] += 1
        self.state["spent_inr"] = round(self.state["spent_inr"] + cost, 4)
        self.state["log"].append({"kind": kind, "units": units, "inr": round(cost, 4), "note": note})
        self.path.write_text(json.dumps(self.state, indent=1, ensure_ascii=False))


def estimate(kind: str, units: float) -> float:
    """₹ for a call you have not made yet. kind: 'tts' (units = characters) or 'stt' (units = seconds)."""
    return units * (TTS_RATE_PER_CHAR if kind == "tts" else STT_RATE_PER_SEC)


class GnaniSpeech:
    def __init__(self, key: str | None = None, budget: CallBudget | None = None):
        self.key = key or os.environ["GNANI_SPEECH_KEY"]
        self.budget = budget or CallBudget()
        self.http = httpx.Client(timeout=60, headers={"X-API-Key-ID": self.key})
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ TTS
    def tts(self, text: str, voice: str = "sia", sample_rate: int = 16000) -> bytes:
        """Synthesise `text` to 16 kHz mono WAV. Cached by (text, voice): repeats cost nothing."""
        key = hashlib.sha1(f"{voice}|{sample_rate}|{text}".encode()).hexdigest()[:16]
        cached = CACHE_DIR / f"{key}.wav"
        if cached.exists():
            return cached.read_bytes()
        self.budget.charge("tts", len(text), note=text[:40])
        r = self.http.post(TTS_URL, json={
            "text": text, "voice": voice, "model": "vachana-voice-v2",
            "audio_config": {"sample_rate": sample_rate, "num_channels": 1, "sample_width": 2,
                             "encoding": "linear_pcm", "container": "wav"}})
        r.raise_for_status()
        cached.write_bytes(r.content)
        return r.content

    # ------------------------------------------------------------ STT
    def stt(self, audio_path: str | Path, language_code: str = "hi-IN", duration_s: float | None = None) -> str:
        """Transcribe a clip ≤ 60 s. Pass duration_s if you know it, for accurate cost accounting."""
        p = Path(audio_path)
        if duration_s is None:
            duration_s = _wav_seconds(p) or 60.0
        self.budget.charge("stt", duration_s, note=p.name)
        with p.open("rb") as f:
            r = self.http.post(STT_URL, files={"audio_file": (p.name, f)}, data={"language_code": language_code})
        r.raise_for_status()
        body = r.json()
        if not body.get("success", True):
            raise RuntimeError(f"STT failed: {body}")
        return body.get("transcript", "")


def _wav_seconds(p: Path) -> float | None:
    try:
        import wave
        with wave.open(str(p), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return None
