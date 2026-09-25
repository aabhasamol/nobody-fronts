"""Prove the voice rail is real for ₹2, without an outbound call.

    python scripts/speech_demo.py --dry-run                 # prints what each call would cost; makes none
    python scripts/speech_demo.py                           # synthesises the supplier call's four questions (Hindi) → cache/tts/*.wav
    python scripts/speech_demo.py --stt path/to/reply.wav   # transcribes a ≤60 s recorded "homestay owner" reply

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

# The supplier-call script (voice job 1), in Hindi: the questions the agent asks a stay that exists only on the
# phone. Q1 carries the party, dates and room count; Q2–Q4 are static and are synthesised once.
Q1 = ("नमस्ते, मैं क्वोरम की तरफ़ से बोल रही हूँ। {party} दोस्तों का एक ग्रुप {check_in} से {nights} रातों के लिए आपके यहाँ "
      "रुकना चाहता है। क्या उन तारीख़ों पर {rooms} ट्विन-शेयरिंग कमरे उपलब्ध हैं?")
Q2 = "ग्रुप के लिए एक कमरे का एक रात का किराया क्या होगा, और क्या उसमें नाश्ता शामिल है?"
Q3 = "अगर ग्रुप एक हफ़्ते पहले बुकिंग रद्द करे, तो रिफ़ंड की क्या शर्तें हैं?"
Q4 = "क्या आप कमरे अड़तालीस घंटे के लिए होल्ड कर सकते हैं, जब तक ग्रुप पक्का करता है? कब तक? धन्यवाद।"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stt", help="path to a ≤60 s wav/mp3 of the 'hotel' answering")
    ap.add_argument("--party", default="पाँच")
    ap.add_argument("--rooms", default="तीन")
    ap.add_argument("--check-in", dest="check_in", default="दो अक्टूबर")
    ap.add_argument("--nights", default="चार")
    args = ap.parse_args()

    lines = [Q1.format(party=args.party, check_in=args.check_in, nights=args.nights, rooms=args.rooms), Q2, Q3, Q4]
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
