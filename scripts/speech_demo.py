"""Prove the voice rail is real for ₹2, without an outbound call.

    python scripts/speech_demo.py --dry-run                 # prints what each call would cost; makes none
    python scripts/speech_demo.py                           # synthesises the verifier's four questions (Hindi) → cache/tts/*.wav
    python scripts/speech_demo.py --stt path/to/reply.wav   # transcribes a ≤60 s recorded "hotel" reply

Needs GNANI_SPEECH_KEY in the environment (put it in .env; .env is git-ignored). Every billable call is
counted in .gnani_budget.json and refused past GNANI_CALL_BUDGET (default 40).
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.rails.gnani_speech import GnaniSpeech, CallBudget, estimate, CACHE_DIR   # noqa: E402

# The verifier script, in Hindi. Q1 carries the listing's claim; Q2–Q4 are static and are synthesised once.
Q1 = ("नमस्ते, मैं क्वोरम की तरफ़ से बोल रही हूँ। पाँच दोस्तों का एक ग्रुप आपके यहाँ {nights} रातें रुकने की सोच रहा है। "
      "आपकी लिस्टिंग में लिखा है — {claim}। क्या कमरा अभी उपलब्ध है, और क्या वह वैसा ही है जैसा फ़ोटो में दिखाया गया है?")
Q2 = "क्या आपके होटल तक की सड़क टैक्सी के लिए ठीक है, बारिश में भी?"
Q3 = "अगर ग्रुप एक हफ़्ते पहले बुकिंग रद्द करे, तो रिफ़ंड की क्या शर्तें हैं?"
Q4 = "क्या आपके पास ट्विन-शेयरिंग कमरे उपलब्ध हैं? धन्यवाद।"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stt", help="path to a ≤60 s wav/mp3 of the 'hotel' answering")
    ap.add_argument("--claim", default="सी-फ़ेसिंग कमरे, पूल, बीच से दो सौ मीटर")
    ap.add_argument("--nights", default="चार")
    args = ap.parse_args()

    lines = [Q1.format(nights=args.nights, claim=args.claim), Q2, Q3, Q4]
    chars = sum(len(l) for l in lines)
    print(f"TTS: {len(lines)} lines, {chars} chars → ₹{estimate('tts', chars):.2f} on first run, ₹0 after (cached)")
    if args.stt:
        print(f"STT: {args.stt} → ≈ ₹{estimate('stt', 60):.2f} at most (60 s cap)")
    budget = CallBudget()
    print(f"Budget: {budget.remaining} of {budget.cap} calls left, ₹{budget.state['spent_inr']:.2f} spent so far")
    if args.dry_run:
        return
    if not os.environ.get("GNANI_SPEECH_KEY"):
        sys.exit("GNANI_SPEECH_KEY not set — add it to .env and `export $(cat .env | xargs)`")

    g = GnaniSpeech(budget=budget)
    for i, line in enumerate(lines, 1):
        wav = g.tts(line)
        print(f"  Q{i}: {len(wav):,} bytes → {CACHE_DIR}")
    if args.stt:
        text = g.stt(args.stt, "hi-IN")
        print(f"  transcript: {text}")
    print(f"Budget now: {g.budget.remaining} left, ₹{g.budget.state['spent_inr']:.2f} spent")


if __name__ == "__main__":
    main()
