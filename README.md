# Quorum — a trip-planning agent for The Ken's *Great Rewiring*, Round 2

**Opening:** Planning the trip (07 of 16) · **Team:** Aabhas, Aditi, Sayan (IIM Calcutta)
**Rule the agent lives by:** the organiser never fronts money and never chases anyone.

Round 1 said what the agent does. This repository is the agent, assembled: the six-step loop as a
state machine, three rail interfaces (voice / payments / logistics), a mock implementation of each so the
whole thing runs offline, and real adapters for **Gnani** (outbound hotel-verification calls) and
**Pine Labs Online** (UPI One-Time Mandates — block now, debit at quorum, release otherwise).

## Run it (offline, two minutes)

```bash
pip install -r requirements.txt
python demo.py                      # full transcript: a booking, a lapse, and a re-booking after a cancelled flight
pytest -q                           # six tests on the quorum logic
./run.sh                            # web UI at http://localhost:8000
```

In the UI: press **Organiser adds Quorum to the group**, reply as each member from the DM tabs (one click
each — canned replies), watch the agent phone-verify hotels and post two bundles, approve the UPI block as
four of the five, then hit **+48h**. It books. Do it again with two approvals and it lapses with nothing
charged. After a booking, **Simulate: carrier cancels …** shows the re-book inside the already-authorised cap.

## The loop

| # | Step | Rail | Where in code |
|---|------|------|---------------|
| 1 | Organiser adds the agent to the group, names dates and a rough budget | — | `Engine.trigger` |
| 2 | Agent loads what it knows per member: home city, ceiling, past trips | — | `Member` |
| 3 | Private DM to each member for constraints, with a reply deadline; silence = default | — | `Engine.capture` / `close_capture` |
| 4 | Two bundles from live fares, per-head from each member's own city | logistics | `Engine.build` |
| 5 | Phone-verify every hotel before it appears; swap on mismatch | **voice (Gnani)** | `Engine._first_verified` |
| 6 | Post both bundles, one marked default, 48-hour clock | — | `Engine.post` |
| 7 | Each member blocks their own share by UPI mandate (share + 10% re-booking headroom) | **payments (Pine Labs)** | `Engine.post` → `PaymentsRail.create_block` |
| 8 | At the deadline: quorum → debit each share and book; else release every block | payments + logistics | `Engine.decide` / `book` / `lapse` |
| later | A carrier cancels a leg → re-book inside the member's authorised cap, no new approval | logistics | `Engine.disrupt` |

Read `app/engine.py` top to bottom; it is the product.

## Real vs mock

| Rail | Mock (default) | Real adapter | Status |
|------|----------------|--------------|--------|
| Voice | `MockVoice` — "Palm Grove" always fails the call, everything else verifies | `GnaniVoice` (outbound call via Inya Agent Builder API) and `GnaniSpeech` (STT/TTS via Vachana) | Speech client is ready to run with the `vach_` key we hold (`scripts/speech_demo.py`). Outbound calls need a *different* key — an Inya platform key with `agents` permission — plus a whitelisted "hotel" number |
| Payments | `MockPayments` — in-memory ledger with `approve()` | `PineLabsPayments` — customer → OT subscription → CREATE_MANDATE → presentation | Adapter written against docs; needs UAT keys with OTM enabled |
| Logistics | `MockLogistics` — hand-written fares for Kolkata / Bengaluru / Delhi / Mumbai → Goa | none yet | Delhivery is not touched in this opening (no parcels); a fare API is a later swap |

Switch with `QUORUM_VOICE=gnani` and `QUORUM_PAYMENTS=pinelabs` (see `.env.example`).

**Gnani has two products and two keys.** The `vach_…` key opens the Speech APIs (STT ₹27/audio-hour,
TTS ₹27/10k chars, 60 requests/minute). The agent/outbound-call API at `api.inya.ai/platform` needs its own
key. Every speech call in this repo goes through a hard budget (`GNANI_CALL_BUDGET`, default 40) and TTS is
cached by content, so re-running the demo costs nothing. `python scripts/speech_demo.py --dry-run` prints the
cost of anything before it is spent. Keys live in `.env`, which is git-ignored — never paste one into chat,
a commit, or a screenshot.

## What we are asking each rail for

Short version — full argument in `docs/rails.md`:

* **Pine Labs.** OTM gives one payer a block-then-debit mandate. We need N mandates bound to one merchant
  order with a quorum rule, an expiry and an **atomic capture**. Today `Engine.book` loops `capture()` five
  times; if the fourth fails, three people are charged for a trip that cannot be booked. That is the wall.
* **Gnani.** Outbound verification works as designed. The gap is structured extraction: we want the four
  answers back as fields, not prose, and a disposition the merchant (us) defines, not the platform.
* **Delhivery.** No role here, and we say so.

## Layout

```
app/
  engine.py        the state machine — start here
  models.py        Trip, Member, Bundle, Authorisation, Event …
  messages.py      everything the agent says
  clock.py         hand-cranked time for the demo
  scenario.py      the five friends
  main.py          FastAPI surface + webhook/pre-call hooks for the real rails
  rails/
    base.py        the three interfaces
    mock.py        offline implementations
    gnani.py       Inya Agent Builder Platform API adapter (outbound calls — needs an agents-scoped key)
    gnani_speech.py Vachana STT/TTS client with call budget + cache (works with the vach_ key)
    pinelabs.py    Pine Labs Online UPI OTM adapter
scripts/speech_demo.py  synthesise the verifier's Hindi questions / transcribe a hotel reply, ~₹2
static/index.html  demo UI
demo.py            terminal transcript
tests/             quorum logic
docs/rails.md      what exists, what we ask, where it breaks
HANDOFF.md         next tasks, in priority order, for whoever builds next
```
