# Quorum — a trip-planning agent for The Ken's *Great Rewiring*, Round 2

**Opening:** Planning the trip (07 of 16) · **Team:** Aabhas, Aditi, Sayan (IIM Calcutta)
**Rule the agent lives by:** the organiser never fronts money and never chases anyone.

Round 1 said what the agent does. This repository is the agent, assembled: the flow as a state machine,
three rail interfaces (voice / payments / logistics), a mock implementation of each so the whole thing runs
offline, and real adapters for **Gnani** (text-to-speech and speech-to-text, the two verbs a P0 call is built
from) and **Pine Labs Online** (UPI One-Time Mandates — block now, debit at booking, release otherwise). The eight Round-2 answers are in `docs/round2-answers.md`;
this code is what they describe.

## The flow, in one line

The organiser gives a **rough budget** and a **maximum overshoot** → the agent builds **one itinerary inside
that budget** → **each member votes on it privately**, and only the tally reaches the group → **the yes-voters
set the trip's budget** (their number × the lowest ceiling among them) and the plan is **re-sized for exactly
them** and must fit → **each authorises their own share × (1 + overshoot)** by UPI mandate → the agent
**debits each share into Quorum's account and pays every supplier from it**.

Trip and travel are planned separately: the stay is shared and the occasion decides what it optimises
(a wedding ranks stays by distance to the venue and lands you before the first function; a leisure trip
ranks by price); travel is per member, outbound and return searched separately, from and back to *their*
cities. Aditi flies Bengaluru → Goa and back to Mumbai. Her share is hers; nobody's overspend is averaged
into anyone else's.

## Run it (offline, two minutes)

```bash
pip install -r requirements.txt
python demo.py                      # three transcripts: wedding (book + two disruptions), leisure (failed vote → revision → dropout), the payments wall
pytest -q                           # 36 tests: the loop, every unhappy turn, the pool, and the voice rail on files
./run.sh                            # web UI at http://localhost:8000
python scripts/speech_demo.py --dry-run          # what the real Gnani loop would cost (≈ ₹1.13 once, then ₹0)
QUORUM_VOICE=gnani python demo.py                # the same demo with real TTS/STT; replies from cache/replies/<callee>/
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
| GATHERING | Private DM to each member: start city, return city, dates they can't do, their own ceiling, must-haves. Reply deadline; silence = home city both ways at the organiser's figure | — |
| PLANNING | Trip side: stays ranked by the occasion's priority; stays with no online inventory get a **supplier call** (availability, group rate, refund terms, 48-hour hold; retry once; the call's price wins). Travel side: per member, out and back, searched separately. Total must fit budget × (1 + overshoot) per head | logistics, **voice** |
| VOTING | One itinerary DM'd to each member: their legs, the stay split, their all-in against their own ceiling, what they'd authorise. Yes/no with a reason, privately. One reminder text. Majority of members ⇒ passes; else up to two revisions built from the reasons; then the organiser decides | — |
| AUTHORISING | The yes-voters set the budget: their number × the lowest ceiling among them. The plan is re-sized for exactly them (rooms re-split; the same hook is where a car or an activity would re-size) and must fit that budget, else it goes back to the group as a revision. Then every yes-voter approves a UPI one-time mandate for share × (1 + overshoot). Nothing charged. Yes-voters who don't approve drop out and the rest are re-sized and re-checked: inside ⇒ proceed, over ⇒ back to the group | **payments** |
| BOOKING | Re-price at live fares (inside the cap: absorbed, shown on the receipt; over: only that member is asked to top up). Re-hold an expired phone hold. Debit every share into **the pool, Quorum's merchant account**; **one failure refunds the rest and stops**. Book legs and stays and **pay each supplier from the pool**, logged per booking. The pool can never go negative: nobody fronts, Quorum included | payments, logistics, voice |
| BOOKED | Tickets and vouchers in DMs; the group gets one post. A cancelled leg's refund comes back into the pool and the re-booked ticket is paid from it, inside the member's cap (a late arrival gets the phone-only stay a call); if every alternative is over the cap: a text with options, then an **escalation call** after 20 minutes, then a top-up from that member | logistics, voice, payments |
| LAPSED / CLOSED | Nothing booked, nothing charged | — |

Read `app/engine.py` top to bottom; it is the product. Every sentence the agent says is in `app/messages.py`.

## Calls are for P0 things

One test: call only when the other side cannot be reached by an API or a message in time, **and** the answer
changes what the agent does next. That is a P0. Three things pass, and the list is code
(`Engine.P0_CALLS`; the only way the engine dials is `Engine._call`, which refuses any other reason):

| P0 | Why a text won't do |
|---|---|
| A stay with no online inventory, before the plan can go to the vote or the booking | There is no listing and no API; without rooms, a rate and a hold there is no plan |
| That stay on the travel day, when a guest's re-booked flight lands late | Otherwise the room goes to a walk-in at 2 am |
| A member whose leg was cancelled, whose options text went unanswered for 20 minutes | The alternatives cost more than they authorised and the fare will not wait |

Everything else is a text: gathering, the plan, the vote, the UPI request, reminders before the vote and the
authorisation deadline, receipts, a failed debit. Members are never called to chase, listed hotels are never
called to check the listing. Every event carries a priority — P0 a call, P1 a text now, P2 a text that can
wait — and the UI badges the calls.

## Real vs mock

| Rail | Mock (default) | Real adapter | Status |
|------|----------------|--------------|--------|
| Voice | `MockVoice` — Dona Maria Homestay doesn't pick up once, then confirms at ₹400/night over the figure we had; Fisherman's Rest is full; escalation calls reach the member, who takes option 1 | `GnaniSpeechVoice` — Gnani TTS speaks each line, Gnani STT transcribes each reply, rules read the fields (rooms, rate, refund terms, hold; the option chosen). The phone line is a separate three-method seam, `Telephony`; the default `FileTelephony` writes the agent's audio to files and takes replies from recordings | **Runs today** with the key in `app/keys.py`: record the homestay owner's answers on a phone into `cache/replies/dona-maria-homestay-assagao/`, run `python scripts/speech_demo.py --call cache/replies/dona-maria-homestay-assagao`, read the fields. A carrier (Exotel, Twilio) behind `Telephony` is a day's work |
| Payments | `MockPayments` — in-memory OT mandates with `approve()`, `revoke()`, cumulative `capture()`, `refund()`, `fail_capture_for` to bounce one debit, and **the pool**: captures settle into it, `pay_supplier()` draws on it and refuses to go negative | `PineLabsPayments` — customer → OT subscription → CREATE_MANDATE → presentation(s) → refund; supplier payouts are recorded as instructions until a B2B travel wallet or bank transfer is wired | Written against docs; needs UAT keys with OTM enabled. To verify in the sandbox: a second presentation on the same OT mandate, and the refund endpoint |
| Logistics | `MockLogistics` — hand-written fares for Kolkata / Bengaluru / Delhi / Mumbai ↔ Goa, five stays (two phone-only), `drift` to move live fares | none yet | Delhivery's role in this opening is distances (ranking stays by km to the venue), not parcels; `km_to_venue` is hand-written today |

Switch with `QUORUM_VOICE=gnani` and `QUORUM_PAYMENTS=pinelabs` (see `.env.example`). **Keys:** the Gnani
speech key is committed in `app/keys.py` on purpose — private repository, one-month life, one holder, a key
that can only spend metered speech credit, and `CallBudget` refuses the 41st billable call per checkout.
Revoke it on the Gnani dashboard when the competition ends. Pine Labs keys stay in `.env`, which is
git-ignored.

## What we are asking each rail for

Short version — full argument in `docs/rails.md` and `docs/round2-answers.md` §4:

* **Pine Labs.** Quorum is the merchant of record: members' mandates settle into Quorum's account, the
  pool, and Quorum pays suppliers from it. The ask is a **group order with escrow**: N UPI one-time mandates
  bound to one order, a shared expiry, an **atomic capture** into a per-order escrow, and settlement to the
  suppliers (or to Quorum's wallet) only when the order is secured. Today `Engine._capture_all` loops one
  presentation per member and, when the fourth fails, refunds the three that went through. That is a
  compensating rollback with three counterparties, not atomicity. `python demo.py` shows it happening.
* **Gnani.** Its API is text-to-speech and speech-to-text, and that is what we use. Three asks: (1)
  **normalised entities in the transcript** — amounts, dates, yes/no — so "teen hazaar do sau" comes back as
  3200 and "haan… matlab nahi" as a no; today `app/rails/gnani.py` reads fields with rules and number-words
  defeat them; (2) **streaming, duplex STT/TTS** so a live call does not wait for whole clips; (3)
  **code-switch robustness** for Konkani/Hindi/English mid-sentence. Dialling is not Gnani's, and we don't
  ask it to be.
* **Delhivery.** Maps: geocoding and distances, so `km_to_venue` is real. No parcels, and we say so.

## Deploy it (a public URL to test on)

The demo is one long-lived process with trips in memory, so it wants a host that runs a container, not
serverless functions. `Dockerfile` and `render.yaml` are included.

* **Render** (free): sign in with GitHub → New → Blueprint → this repo. It reads `render.yaml`, builds the
  Dockerfile, health-checks `/clock`, and redeploys on every push to the branch. The free plan sleeps after
  15 idle minutes and wakes in about 30 seconds; trips reset then.
* **Hugging Face Spaces** (Docker) or **Fly.io / Railway**: same Dockerfile; set `PORT` (Spaces wants 7860).
* **A laptop for a live pitch**: `./run.sh` plus a Cloudflare or ngrok tunnel.

Set `QUORUM_DEMO_PASSWORD` on the host and the URL asks for it (any username). Keep `QUORUM_VOICE=mock` and
`QUORUM_PAYMENTS=mock` on anything public: the mock rails cost nothing, and the Gnani key never leaves the
server in any case.

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
    gnani.py       the voice rail from TTS + STT: scripts, the Telephony seam, FileTelephony, field parsers
    gnani_speech.py Vachana TTS/STT client with call budget + cache
    pinelabs.py    Pine Labs Online UPI OTM adapter
  keys.py          the Gnani speech key, committed on purpose (see above)
Dockerfile, render.yaml  one-container deploy (Render one-click; Spaces / Fly / Railway with the same image)
scripts/speech_demo.py  cost dry-run · TTS the questions · STT a reply · or the whole supplier call on recordings, ≈ ₹2
static/index.html  demo UI
demo.py            three terminal transcripts (docs/demo_transcript.txt is its output)
tests/             the loop and every unhappy turn against the mocks; the voice rail's parsers and file loop
docs/round2-answers.md  the eight Round-2 answers this code implements
docs/rails.md      what exists, what we ask, where it breaks
HANDOFF.md         next tasks, in priority order, for whoever builds next
```
