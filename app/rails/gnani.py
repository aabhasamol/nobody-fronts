"""Gnani — the voice rail built from what Gnani's API is: text-to-speech and speech-to-text.

Gnani (Vachana) gives us two verbs, `tts(text) → audio` and `stt(audio) → text`, on the `vach_` key in
`app/keys.py`. It does not dial phones. So a P0 call is assembled from three parts:

    telephony dials the number → play the TTS of each line the agent says → record the other side → STT
    → read the fields out of the words (rooms, rate, refund terms, hold; or the option a member chose)

Telephony is its own seam (`Telephony`) because it is a different vendor (Exotel, Twilio, Knowlarity: a day
of work behind three methods). The default `FileTelephony` "plays" the agent's audio into
`cache/calls/<ref>/` and takes the reply from a recording in `cache/replies/<callee-slug>/` — one file per
question (`1.wav`, `2.wav`, …) or a single `reply.wav` answering everything; a `.txt` beside or instead of a
recording is used as a typed reply and skips STT. That is how the demo runs the real Gnani loop for about
₹2: a teammate records the homestay owner's answers on a phone, drops the files in, and the rail log shows
Gnani's transcript and the fields we read from it. No reply files ⇒ the call is honestly NO_ANSWER.

Which calls exist is decided in the engine (`Engine.P0_CALLS`), not here. This module only knows how to
speak, listen and read.
"""
from __future__ import annotations
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
import httpx
from ..clock import clock
from ..models import Member, Stay, CallRecord, new_id
from .base import VoiceRail
from .gnani_speech import GnaniSpeech, ROOT

HOLD_HOURS = 48
HINDI_MONTHS = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]

# ------------------------------------------------------------------ what the agent says
# Supplier call (Hindi). Line 1 carries the party, dates and room count; 2–4 are static and TTS'd once.
SUPPLIER_LINES = [
    "नमस्ते, मैं क्वोरम की तरफ़ से बोल रही हूँ। {party} दोस्तों का एक ग्रुप {check_in} से {nights} रातों के लिए आपके यहाँ "
    "रुकना चाहता है। क्या उन तारीख़ों पर {rooms} ट्विन-शेयरिंग कमरे उपलब्ध हैं?",
    "ग्रुप के लिए एक कमरे का एक रात का किराया क्या होगा, और क्या उसमें नाश्ता शामिल है?",
    "अगर ग्रुप एक हफ़्ते पहले बुकिंग रद्द करे, तो रिफ़ंड की क्या शर्तें हैं?",
    "क्या आप कमरे अड़तालीस घंटे के लिए होल्ड कर सकते हैं, जब तक ग्रुप पक्का करता है? कब तक? धन्यवाद।",
]
LATE_ARRIVAL_LINE = ("नमस्ते, क्वोरम से बोल रही हूँ, बुकिंग {booking_ref} के बारे में। हमारे एक मेहमान, {guest}, की फ़्लाइट "
                     "रद्द हो गई है; अब वे रात {eta} बजे पहुँचेंगे। कृपया कमरा रखा रहे और कोई उन्हें अंदर आने दे। ठीक है?")
ESCALATION_OPEN = "Hi {member}, this is Quorum. I'm calling because I couldn't reach you on WhatsApp. {question}"
ESCALATION_OPTION = "Option {i}: {option}."
ESCALATION_ASK = "Which one should I book? Say one or two. A UPI request for the difference will follow on WhatsApp."


def supplier_lines(party: int, check_in: date, nights: int, rooms: int) -> list[str]:
    ci = f"{check_in.day} {HINDI_MONTHS[check_in.month - 1]}"
    return [SUPPLIER_LINES[0].format(party=party, check_in=ci, nights=nights, rooms=rooms)] + SUPPLIER_LINES[1:]


# ------------------------------------------------------------------ reading fields out of words
# Whole words only ("ok" must not match inside "booked"); Devanagari kept intact by splitting on spaces.
_PUNCT = ".,!?;:…()\"'।-"


def _words(text: str) -> str:
    toks = [t.strip(_PUNCT) for t in text.lower().split()]
    return " " + " ".join(t for t in toks if t) + " "


def _last(words: str, phrases: tuple[str, ...]) -> int:
    return max((words.rfind(f" {p} ") for p in phrases), default=-1)


# "hai"/"hain" are copulas, not a yes; "koi dikkat nahi" is a yes that contains a no. Idioms are rewritten first.
_AFFIRMATIVE_IDIOMS = ("koi dikkat nahi", "koi problem nahi", "koi baat nahi", "dikkat nahi", "problem nahi", "no problem",
                       "no issue", "not a problem", "कोई दिक्कत नहीं", "कोई बात नहीं", "कोई समस्या नहीं")
_YES = ("haan", "haa", "han", "ji", "milenge", "mil jayenge", "mil jaayenge", "available", "yes", "theek", "thik", "sure",
        "ok", "okay", "kar denge", "rakh lenge", "rakh denge", "हाँ", "हां", "जी", "उपलब्ध", "मिल जाएंगे", "ठीक", "रख लेंगे")
_NO = ("nahi", "nahin", "nai", "no", "not", "booked", "full", "poora", "pura", "sorry", "band",
       "नहीं", "पूरा", "बुक", "फुल", "बंद")


def parse_yes_no(text: Optional[str]) -> Optional[bool]:
    """The last verdict in the sentence wins: 'haan… matlab nahi' is a no; 'haan, koi dikkat nahi' is a yes."""
    if not text or not text.strip():
        return None
    t = text.lower()
    for idiom in _AFFIRMATIVE_IDIOMS:
        t = t.replace(idiom, " theek ")
    w = _words(t)
    y, n = _last(w, _YES), _last(w, _NO)
    if y < 0 and n < 0:
        return None
    return y > n


_NUM_WORDS = {"hazaar": 1000, "hazar": 1000, "हज़ार": 1000, "हजार": 1000, "sau": 100, "सौ": 100}


def parse_amount(text: Optional[str]) -> Optional[int]:
    """Rupees from '₹3,200', '3200 rupaye', '3 hazaar 2 sau'. Digits only; number-words are not read."""
    if not text:
        return None
    t = text.replace(",", "")
    total, found = 0, False
    for m in re.finditer(r"(\d+)\s*(hazaar|hazar|हज़ार|हजार|sau|सौ)?", t):
        n, unit = int(m.group(1)), m.group(2)
        if unit:
            total += n * _NUM_WORDS[unit]; found = True
        elif n >= 100:
            total += n; found = True
    return total if found else None


# An explicit "option two" beats a bare "one" used as a pronoun ("the 11 o'clock one").
_EXPLICIT = [(1, ("option 1", "option one", "option ek", "number one", "number 1", "pehla option", "first option")),
             (2, ("option 2", "option two", "option do", "number two", "number 2", "doosra option", "second option"))]
_BARE = [(1, ("1", "one", "ek", "pehla", "pehle", "first", "एक", "पहला")),
         (2, ("2", "two", "do", "doosra", "dusra", "second", "दो", "दूसरा"))]


def parse_choice(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    w = _words(text)
    for table in (_EXPLICIT, _BARE):
        hits = [(w.rfind(f" {p} "), n) for n, phrases in table for p in phrases if f" {p} " in w]
        if hits:
            return max(hits)[1]
    return None


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ------------------------------------------------------------------ the phone line (not Gnani's)
@dataclass
class Reply:
    path: Optional[Path] = None              # a recording to send to STT
    text: Optional[str] = None               # a typed reply: skips STT


class CallSession(ABC):
    answered: bool

    @abstractmethod
    def play(self, audio: bytes) -> None: ...

    @abstractmethod
    def listen(self, max_seconds: float = 60) -> Optional[Reply]: ...

    @abstractmethod
    def hangup(self) -> None: ...


class Telephony(ABC):
    """Dial a number and get a session you can play audio into and record from. Exotel / Twilio go here."""

    @abstractmethod
    def dial(self, phone: str, name: str, ref: str) -> CallSession: ...


class FileTelephony(Telephony):
    """The default: agent audio goes to `calls_dir/<ref>/agent_NN.wav`; replies come from
    `replies_dir/<slug(name)>/` as `N.wav|mp3|m4a|txt` per question, or one `reply.*` for the whole call."""

    def __init__(self, replies_dir: Optional[Path] = None, calls_dir: Optional[Path] = None):
        self.replies_dir = Path(replies_dir or os.environ.get("QUORUM_REPLIES_DIR") or ROOT / "cache" / "replies")
        self.calls_dir = Path(calls_dir or os.environ.get("QUORUM_CALLS_DIR") or ROOT / "cache" / "calls")

    def dial(self, phone: str, name: str, ref: str) -> CallSession:
        return _FileSession(self.calls_dir / ref, self.replies_dir / slug(name))


class _FileSession(CallSession):
    _EXT = (".txt", ".wav", ".mp3", ".m4a")

    def __init__(self, out: Path, replies: Path):
        self.out, self.replies, self.n, self.heard, self.used_single = out, replies, 0, 0, False
        self.answered = replies.is_dir() and any(p.suffix in self._EXT for p in replies.iterdir())
        if self.answered:
            out.mkdir(parents=True, exist_ok=True)

    def _find(self, stem: str) -> Optional[Path]:
        for ext in self._EXT:
            p = self.replies / f"{stem}{ext}"
            if p.exists():
                return p
        return None

    def play(self, audio: bytes) -> None:
        self.n += 1
        if self.answered:
            (self.out / f"agent_{self.n:02d}.wav").write_bytes(audio)

    def listen(self, max_seconds: float = 60) -> Optional[Reply]:
        self.heard += 1                                     # the nth reply, however many lines the agent said first
        p = self._find(str(self.heard))
        if p is None and not self.used_single:
            p = self._find("reply")
            self.used_single = p is not None
        if p is None:
            return None
        return Reply(text=p.read_text().strip()) if p.suffix == ".txt" else Reply(path=p)

    def hangup(self) -> None:
        pass


# ------------------------------------------------------------------ the rail
class GnaniSpeechVoice(VoiceRail):
    def __init__(self, speech: Optional[GnaniSpeech] = None, telephony: Optional[Telephony] = None):
        self.speech = speech or GnaniSpeech()
        self.tel = telephony or FileTelephony()

    # -- speak / listen
    def _say(self, s: CallSession, text: str, log: list[str]) -> None:
        s.play(self.speech.tts(text))
        log.append(f"[agent] {text}")

    def _hear(self, s: CallSession, lang: str, who: str, log: list[str]) -> Optional[str]:
        r = s.listen()
        if r is None:
            log.append(f"[{who}] (no reply)")
            return None
        text = r.text if r.text is not None else self.speech.stt(r.path, lang)
        log.append(f"[{who}] {text}")
        return text

    def _record(self, **kw) -> CallRecord:
        return CallRecord(call_ref=kw.pop("ref"), called_at=clock.now(), **kw)

    # -- P0: a stay with no online inventory
    def call_supplier(self, stay: Stay, party_size: int, check_in: date, nights: int, language: str) -> CallRecord:
        ref, rooms, log = new_id("call"), Stay.rooms_for(party_size), []
        base = dict(to=stay.name, phone=stay.phone, language=language, purpose="AVAILABILITY", ref=ref)
        s = self.tel.dial(stay.phone, stay.name, ref)
        if not s.answered:
            return self._record(disposition="NO_ANSWER", transcript="[ring] no answer", **base)
        answers: list[Optional[str]] = []
        try:
            for line in supplier_lines(party_size, check_in, nights, rooms):
                self._say(s, line, log)
                answers.append(self._hear(s, language, stay.name, log))
        except httpx.HTTPError as e:
            log.append(f"[rail] Gnani error: {e}")
            return self._record(disposition="NO_ANSWER", transcript="\n".join(log), **base)
        finally:
            s.hangup()
        a1, a2, a3, a4 = (answers + [None] * 4)[:4]
        joined = " ".join(a for a in answers if a)
        if not joined.strip():
            return self._record(disposition="NO_ANSWER", transcript="\n".join(log), **base)
        available = parse_yes_no(a1 or joined)
        if available is False:
            return self._record(disposition="UNAVAILABLE", available=False, transcript="\n".join(log), **base)
        if available is None:                                        # words, but no decision in them
            return self._record(disposition="NO_ANSWER", transcript="\n".join(log), **base)
        refund = a3 or next((sent for sent in re.split(r"[.।]", joined) if re.search(r"refund|wapas|रिफ़ंड|वापस", sent, re.I)), None)
        held = parse_yes_no(a4) if a4 else None
        return self._record(disposition="CONFIRMED", available=True, rooms=rooms,
                            rate_per_room_night=parse_amount(a2 or joined), twin_sharing=True,
                            refund_terms=(refund or "").strip() or None,
                            hold_until=clock.now() + timedelta(hours=HOLD_HOURS) if held is not False else None,
                            transcript="\n".join(log), **base)

    # -- P0: a guest will arrive late at a phone-only stay
    def notify_late_arrival(self, stay: Stay, member: Member, eta: datetime) -> CallRecord:
        ref, log = new_id("call"), []
        base = dict(to=stay.name, phone=stay.phone, language="hi-IN", purpose="LATE_ARRIVAL", ref=ref)
        s = self.tel.dial(stay.phone, stay.name, ref)
        if not s.answered:
            return self._record(disposition="NO_ANSWER", transcript="[ring] no answer", **base)
        try:
            self._say(s, LATE_ARRIVAL_LINE.format(booking_ref=stay.booking_ref or "", guest=member.first, eta=eta.strftime("%H:%M")), log)
            reply = self._hear(s, "hi-IN", stay.name, log)
        except httpx.HTTPError as e:
            log.append(f"[rail] Gnani error: {e}")
            reply = None
        finally:
            s.hangup()
        ok = parse_yes_no(reply)
        return self._record(disposition="NO_ANSWER" if reply is None else "UNAVAILABLE" if ok is False else "CONFIRMED",
                            transcript="\n".join(log), **base)

    # -- P0: a member in a live disruption whose text went unanswered
    def escalate_member(self, member: Member, question: str, options: list[str]) -> CallRecord:
        ref, log = new_id("call"), []
        base = dict(to=member.name, phone=member.phone, language="en-IN", purpose="ESCALATION", ref=ref)
        s = self.tel.dial(member.phone, member.name, ref)
        if not s.answered:
            return self._record(disposition="NO_ANSWER", transcript="[ring] no answer", **base)
        try:
            self._say(s, ESCALATION_OPEN.format(member=member.first, question=question), log)
            for i, o in enumerate(options, 1):
                self._say(s, ESCALATION_OPTION.format(i=i, option=o), log)
            self._say(s, ESCALATION_ASK, log)
            reply = self._hear(s, "en-IN", member.first, log)
        except httpx.HTTPError as e:
            log.append(f"[rail] Gnani error: {e}")
            reply = None
        finally:
            s.hangup()
        choice = parse_choice(reply)
        if choice is not None and choice > len(options):
            choice = None
        return self._record(disposition="CONFIRMED" if choice else "NO_ANSWER", choice=choice,
                            transcript="\n".join(log), **base)
