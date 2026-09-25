# Quorum — a trip-planning agent for The Ken's *Great Rewiring*, Round 2

**Opening:** Planning the trip (07 of 16) · **Team:** Aabhas, Aditi, Sayan (IIM Calcutta)
**Rule the agent lives by:** the organiser never fronts money and never chases anyone.

Round 1 said what the agent does. This repository is the agent, assembled: the flow as a state machine,
three rail interfaces (voice / payments / logistics), a mock implementation of each so the whole thing runs
offline, and real adapters for **Gnani** (outbound calls) and **Pine Labs Online** (UPI One-Time Mandates —
block now, debit at booking, release otherwise). The eight Round-2 answers are in `docs/round2-answers.md`;
this code is what they describe.

## The flow, in one line

The organiser gives a **rough budget** and a **maximum overshoot** → the agent builds **one itinerary inside
that budget** → **each member votes on it privately**, and only the tally reaches the group → **everyone who
said yes authorises their own share × (1 + overshoot)** by UPI mandate → the agent **debits each share and
books the legs and stays**.

Trip and travel are planned separately: the stay is shared and the occasion decides what it optimises
(a wedding ranks stays by distance to the venue and lands you before the first function; a leisure trip
ranks by price); travel is per member, outbound and return searched separately, from and back to *their*
cities. Aditi flies Bengaluru → Goa and back to Mumbai. Her share is hers; nobody's overspend is averaged
into anyone else's.

## Run it (offline, two minutes)

```bash
pip install -r requirements.txt
python demo.py                      # three transcripts: wedding (book + two disruptions), leisure (failed vote → revision → dropout), the payments wall
pytest -q                           # 22 tests on the loop and every unhappy turn
./run.sh                            # web UI at http://localhost:8000
```

In the UI: pick **wedding** or **leisure**, set the budget and the overshoot, press **Organiser adds Quorum to
the group**. Reply as each member from the DM tabs (one click: start city, return city, must-haves), or hit
**+24h** and watch silence default to home city both ways. The agent phones the homestay (no answer, retry,
confirmed at a rate the listing didn't have) and DMs each member the plan with *their* cost. Vote **Yes** or
**No** per tab: only the tally is posted. Approve the UPI block as each yes-voter and it books. Then
**Carrier cancels …** to see a re-book inside the cap (and the homestay called about the late arrival),
turn the **Fares from Delhi** knob to ×1.60 first to see the over-cap path: a text with two options, no reply,
**+30 min**, an escalation call, a top-up from that member only. **Make Riya's debit bounce** before approving
shows the wall: three debits refunded, nothing booked.

## The states

| State | What happens | Rail |
|---|---|---|
| INITIATED | Organiser adds Quorum to the group: destination, dates, occasion, rough budget per head, maximum overshoot | — |
| GATHERING | Private DM to each member: start city, return city, dates they can't do, must-haves. Reply deadline; silence = home city both ways on the stated dates | — |
| PLANNING | Trip side: stays ranked by the occasion's priority; stays with no online inventory get a **supplier call** (availability, group rate, refund terms, 48-hour hold; retry once; the call's price wins). Travel side: per member, out and back, searched separately. Total must fit budget × (1 + overshoot) per head | logistics, **voice** |
| VOTING | One itinerary DM'd to each member: their legs, the stay split, their all-in, what they'd authorise. Yes/no with a reason, privately. One reminder text. Majority of members ⇒ passes; else up to two revisions built from the reasons; then the organiser decides | — |
| AUTHORISING | Every yes-voter approves a UPI one-time mandate for share × (1 + overshoot). The stay re-splits among those who are in. Nothing charged. Yes-voters who don't approve drop out and the rest are re-priced: inside their caps ⇒ proceed, over ⇒ back to the group | **payments** |
| BOOKING | Re-price at live fares (inside the cap: absorbed, shown on the receipt; over: only that member is asked to top up). Re-hold an expired phone hold. Debit every share; **one failure refunds the rest and stops**. Book legs and stays | payments, logistics, voice |
| BOOKED | Tickets and vouchers in DMs; the group gets one post. A cancelled leg is re-booked inside the member's cap (a late arrival gets the phone-only stay a call); if every alternative is over the cap: a text with options, then an **escalation call** after 20 minutes, then a top-up from that member | logistics, voice, payments |
| LAPSED / CLOSED | Nothing booked, nothing charged | — |

Read `app/engine.py` top to bottom; it is the product. Every sentence the agent says is in `app/messages.py`.

## Where voice is used, and where it is not

One test: call only when the other side cannot be reached by an API or a message in time, and the answer
changes what the agent does next. That leaves voice **two jobs**:

1. **Supplier calls** to stays that exist only on the phone — before the vote (rooms, rate, refund terms,
   hold) and on the travel day (late arrival).
2. **Escalation** to a member during a live disruption, after the text went unanswered.

Members are never called to chase a vote or an approval, and listed hotels are never called to check the
listing. `VoiceRail` has exactly those three methods.

## Real vs mock

| Rail | Mock (default) | Real adapter | Status |
|------|----------------|--------------|--------|
| Voice | `MockVoice` — Dona Maria Homestay doesn't pick up once, then confirms at ₹400/night over the figure we had; Fisherman's Rest is full; escalation calls reach the member, who takes option 1 | `GnaniVoice` (Inya Agent Builder: one Jinja prompt branching on purpose, disposition + extracted variables) and `GnaniSpeech` (STT/TTS via Vachana) | Speech client runs with the `vach_` key we hold (`scripts/speech_demo.py` synthesises the supplier call's four Hindi questions for ≈ ₹1.15). Outbound calls need an Inya key with `agents` permission plus a whitelisted "homestay" number |
| Payments | `MockPayments` — in-memory OT mandates with `approve()`, `revoke()`, cumulative `capture()`, `refund()`, and `fail_capture_for` to bounce one debit | `PineLabsPayments` — customer → OT subscription → CREATE_MANDATE → presentation(s) → refund | Written against docs; needs UAT keys with OTM enabled. Two things to verify in the sandbox: a second presentation on the same OT mandate, and the refund endpoint |
| Logistics | `MockLogistics` — hand-written fares for Kolkata / Bengaluru / Delhi / Mumbai ↔ Goa, five stays (two phone-only), `drift` to move live fares | none yet | Delhivery's role in this opening is distances (ranking stays by km to the venue), not parcels; `km_to_venue` is hand-written today |

Switch with `QUORUM_VOICE=gnani` and `QUORUM_PAYMENTS=pinelabs` (see `.env.example`). Keys live in `.env`,
which is git-ignored — never paste one into chat, a commit, or a screenshot.

## What we are asking each rail for

Short version — full argument in `docs/rails.md` and `docs/round2-answers.md` §4:

* **Pine Labs.** A **group mandate**: N UPI one-time mandates bound to one merchant order, a shared expiry,
  and an **atomic capture**. Today `Engine._capture_all` loops one presentation per member and, when the
  fourth fails, refunds the three that went through. That is a compensating rollback with three
  counterparties, not atomicity. `python demo.py` shows it happening.
* **Gnani.** Structured extraction on the read path: a schema on the agent (`available`,
  `rate_per_room_night`, `rooms`, `twin_sharing`, `refund_terms`, `hold_until`; `choice` for escalations)
  filled by the platform and returned as fields, so the engine never parses prose. And a commitment record:
  a hold agreed on a call should come back as something the supplier can be held to.
* **Delhivery.** Maps: geocoding and distances, so `km_to_venue` is real. No parcels, and we say so.

## Layout

```
app/
  engine.py        the state machine — start here
  models.py        Trip, Member, Constraint, Plan, Stay, Leg, Vote, Authorisation, Decision, Event …
  messages.py      everything the agent says
  clock.py         hand-cranked time for the demo
  scenario.py      the five friends, the wedding and leisure presets, their canned replies
  main.py          FastAPI surface + webhook/pre-call hooks for the real rails + two demo knobs
  rails/
    base.py        the three interfaces (VoiceRail has the two jobs and nothing else)
    mock.py        offline implementations
    gnani.py       Inya Agent Builder adapter — supplier call, late-arrival notice, escalation call
    gnani_speech.py Vachana STT/TTS client with call budget + cache (works with the vach_ key)
    pinelabs.py    Pine Labs Online UPI OTM adapter
scripts/speech_demo.py  synthesise the supplier call's Hindi questions / transcribe a homestay reply, ≈ ₹2
static/index.html  demo UI
demo.py            three terminal transcripts (docs/demo_transcript.txt is its output)
tests/             the loop and every unhappy turn, against the mocks
docs/round2-answers.md  the eight Round-2 answers this code implements
docs/rails.md      what exists, what we ask, where it breaks
HANDOFF.md         next tasks, in priority order, for whoever builds next
```
