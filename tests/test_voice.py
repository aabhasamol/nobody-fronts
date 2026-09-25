"""The Gnani rail without the network: reading fields out of words, and the TTS → line → STT loop on files."""
from datetime import date, datetime
from pathlib import Path
from app.models import Stay, Member
from app.rails.gnani import (GnaniSpeechVoice, FileTelephony, parse_yes_no, parse_amount, parse_choice, slug,
                             supplier_lines)


class FakeSpeech:
    """Stands in for Vachana: TTS returns bytes, STT returns the text stored next to the recording."""
    def __init__(self):
        self.tts_calls, self.stt_calls = [], []

    def tts(self, text, voice="sia", sample_rate=16000):
        self.tts_calls.append(text)
        return b"RIFF" + text.encode("utf-8")[:16]

    def stt(self, path, language_code="hi-IN", duration_s=None):
        self.stt_calls.append((Path(path).name, language_code))
        return Path(path).with_suffix(".transcript").read_text()


def stay():
    return Stay(name="Dona Maria Homestay, Assagao", city="Goa", area="Assagao", phone="0832600000",
                rate_per_room_night=2800, nights=4, phone_only=True)


def rail(tmp_path, replies: dict[str, str] | None, callee=None, as_audio=False):
    d = tmp_path / "replies" / slug(callee or stay().name)
    if replies is not None:
        d.mkdir(parents=True)
        for stem, text in replies.items():
            if as_audio:
                (d / f"{stem}.wav").write_bytes(b"RIFF fake audio")
                (d / f"{stem}.transcript").write_text(text)
            else:
                (d / f"{stem}.txt").write_text(text)
    speech = FakeSpeech()
    return GnaniSpeechVoice(speech=speech, telephony=FileTelephony(replies_dir=tmp_path / "replies", calls_dir=tmp_path / "calls")), speech


# ------------------------------------------------------------------ words → fields
def test_yes_no_last_verdict_wins_in_hindi_and_english():
    assert parse_yes_no("Haan, teen rooms hain, koi dikkat nahi") is True
    assert parse_yes_no("Sorry, poora booked hai un dates pe") is False
    assert parse_yes_no("haan… matlab nahi, ek shaadi ka block hai") is False
    assert parse_yes_no("हाँ जी, तीन कमरे उपलब्ध हैं") is True
    assert parse_yes_no("नहीं, पूरा बुक है") is False
    assert parse_yes_no("hmm, ek minute") is None and parse_yes_no("") is None and parse_yes_no(None) is None


def test_amounts_from_digits_and_units():
    assert parse_amount("₹3,200 per room per night, breakfast included") == 3200
    assert parse_amount("3200 rupaye lagenge") == 3200
    assert parse_amount("3 hazaar 2 sau per night") == 3200
    assert parse_amount("3 हज़ार") == 3000
    assert parse_amount("2 rooms hain") is None                       # a count, not a rate
    assert parse_amount("dekh ke batata hoon") is None


def test_choice_from_option_words():
    assert parse_choice("Option 1, book it.") == 1
    assert parse_choice("doosra wala theek hai") == 2
    assert parse_choice("two please") == 2
    assert parse_choice("option do") == 2
    assert parse_choice("पहला") == 1
    assert parse_choice("hmm let me think") is None


# ------------------------------------------------------------------ the loop on files
def test_supplier_call_reads_rooms_rate_refund_and_hold_from_typed_replies(tmp_path):
    v, speech = rail(tmp_path, {"1": "Haan ji, teen rooms hain un dates pe.",
                                "2": "3200 per room per night, breakfast included.",
                                "3": "72 ghante pehle tak full refund.",
                                "4": "Theek hai, 48 ghante hold kar denge."})
    rec = v.call_supplier(stay(), 5, date(2026, 10, 2), 4, "hi-IN")
    assert rec.disposition == "CONFIRMED" and rec.available and rec.rooms == 3
    assert rec.rate_per_room_night == 3200 and "72" in rec.refund_terms and rec.hold_until is not None
    assert len(speech.tts_calls) == 4 and speech.stt_calls == []                  # typed replies skip STT
    assert speech.tts_calls == supplier_lines(5, date(2026, 10, 2), 4, 3)
    assert sorted(p.name for p in (tmp_path / "calls").rglob("agent_*.wav")) == ["agent_01.wav", "agent_02.wav", "agent_03.wav", "agent_04.wav"]
    assert "[agent] नमस्ते" in rec.transcript and "[Dona Maria Homestay, Assagao] 3200" in rec.transcript


def test_recordings_go_through_stt_one_file_can_answer_everything(tmp_path):
    v, speech = rail(tmp_path, {"reply": "Haan, 3 rooms milenge. 3,200 per night. Refund 72 ghante pehle tak. 48 ghante hold theek hai."}, as_audio=True)
    rec = v.call_supplier(stay(), 5, date(2026, 10, 2), 4, "hi-IN")
    assert rec.disposition == "CONFIRMED" and rec.rate_per_room_night == 3200 and "72" in rec.refund_terms
    assert speech.stt_calls == [("reply.wav", "hi-IN")]                            # one STT, not four


def test_no_recordings_means_no_answer_and_a_no_means_unavailable(tmp_path):
    v, speech = rail(tmp_path, None)
    rec = v.call_supplier(stay(), 5, date(2026, 10, 2), 4, "hi-IN")
    assert rec.disposition == "NO_ANSWER" and speech.tts_calls == []             # nobody picked up: nothing spent
    v, _ = rail(tmp_path, {"1": "Sorry, poora booked hai, shaadi ka block."})
    assert v.call_supplier(stay(), 5, date(2026, 10, 2), 4, "hi-IN").disposition == "UNAVAILABLE"


def test_late_arrival_and_escalation(tmp_path):
    m = Member(name="Riya Sen", phone="9830044444", home_city="Delhi")
    v, _ = rail(tmp_path, {"1": "Haan, koi dikkat nahi, chowkidar jaga rahega."})
    s = stay(); s.booking_ref = "HOLD-1"
    assert v.notify_late_arrival(s, m, datetime(2026, 10, 2, 22, 36)).disposition == "CONFIRMED"
    v, speech = rail(tmp_path, {"1": "Option 2, the 11 o'clock one."}, callee=m.name)
    rec = v.escalate_member(m, "Your flight was cancelled.", ["Akasa 20:00, ₹9,120", "Vistara 11:00, ₹13,120"])
    assert rec.disposition == "CONFIRMED" and rec.choice == 2 and len(speech.tts_calls) == 4
    v, _ = rail(tmp_path, {"1": "Option 3"}, callee=m.name)
    assert v.escalate_member(m, "q", ["a", "b"]).choice is None                   # out of range is not a choice
