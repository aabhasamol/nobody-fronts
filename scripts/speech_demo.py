"""Prove the voice rail is real for about ₹2, without a phone line.

    python scripts/speech_demo.py --dry-run                 # what each call would cost; makes none
    python scripts/speech_demo.py                           # TTS the supplier call's four Hindi questions → cache/tts/*.wav
    python scripts/speech_demo.py --stt reply.wav           # STT one ≤60 s recording of the "homestay owner"
    python scripts/speech_demo.py --call cache/replies/dona-maria-homestay-assagao
                                                            # the whole P0 supplier call: TTS each question, STT each reply
                                                            # (1.wav … 4.wav, or one reply.wav; a .txt is a typed reply),
                                                            # then the fields the engine would act on

The key is in app/keys.py (GNANI_SPEECH_KEY overrides it). Every billable call is counted in
.gnani_budget.json and refused past GNANI_CALL_BUDGET (default 40).
"""
from __future__ import annotations
import argparse
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.rails.gnani_speech import GnaniSpeech, CallBudget, estimate, CACHE_DIR   # noqa: E402
from app.rails.gnani import GnaniSpeechVoice, FileTelephony, supplier_lines, slug   # noqa: E402
from app.models import Stay                                                         # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stt", help="path to a ≤60 s wav/mp3 of the 'homestay owner' answering")
    ap.add_argument("--call", help="a replies directory (1.wav…4.wav or reply.wav, .txt allowed) or one recording, for a full supplier call")
    ap.add_argument("--party", type=int, default=5)
    ap.add_argument("--nights", type=int, default=4)
    args = ap.parse_args()

    lines = supplier_lines(args.party, date(2026, 10, 2), args.nights, (args.party + 1) // 2)
    chars = sum(len(l) for l in lines)
    print(f"TTS: {len(lines)} lines, {chars} chars → ₹{estimate('tts', chars):.2f} on first run, ₹0 after (cached)")
    if args.stt or args.call:
        print(f"STT: ≈ ₹{estimate('stt', 60):.2f} per 60 s of reply, at most")
    budget = CallBudget()
    print(f"Budget: {budget.remaining} of {budget.cap} calls left, ₹{budget.state['spent_inr']:.2f} spent so far")
    if args.dry_run:
        return

    g = GnaniSpeech(budget=budget)
    if args.call:
        stay = Stay(name="Dona Maria Homestay, Assagao", city="Goa", area="Assagao", phone="0832600000",
                    rate_per_room_night=2800, nights=args.nights, phone_only=True)
        src = Path(args.call)
        tmp = None
        if src.is_file():                                     # one recording answering everything
            tmp = Path(tempfile.mkdtemp()); d = tmp / slug(stay.name); d.mkdir()
            shutil.copy(src, d / f"reply{src.suffix}")
            replies_dir = tmp
        else:
            replies_dir = src.parent if src.name == slug(stay.name) else src
        voice = GnaniSpeechVoice(speech=g, telephony=FileTelephony(replies_dir=replies_dir))
        rec = voice.call_supplier(stay, args.party, date(2026, 10, 2), args.nights, "hi-IN")
        print("\n--- transcript ---"); print(rec.transcript)
        print("\n--- fields the engine acts on ---")
        for k in ("disposition", "available", "rooms", "rate_per_room_night", "twin_sharing", "refund_terms", "hold_until"):
            print(f"  {k:<20} {getattr(rec, k)}")
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        for i, line in enumerate(lines, 1):
            wav = g.tts(line)
            print(f"  Q{i}: {len(wav):,} bytes → {CACHE_DIR}")
        if args.stt:
            print(f"  transcript: {g.stt(args.stt, 'hi-IN')}")
    print(f"\nBudget now: {g.budget.remaining} left, ₹{g.budget.state['spent_inr']:.2f} spent")


if __name__ == "__main__":
    main()
