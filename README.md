# Quorum — a trip-planning agent for The Ken's *Great Rewiring*, Round 2

**Opening:** Planning the trip (07 of 16) · **Team:** Aabhas, Aditi, Sayan (IIM Calcutta)
**Rule the agent lives by:** the organiser never fronts money and never chases anyone.

Round 1 said what the agent does. This repository is the agent, assembled: the flow as a state machine,
three rail interfaces (voice / payments / logistics), a mock implementation of each so the whole thing runs
offline, and real adapters for **Gnani** (text-to-speech and speech-to-text, the two verbs a P0 call is built
from) and **Pine Labs Online** (UPI One-Time Mandates — block now, debit at booking, release otherwise). The eight Round-2 answers as submitted are in
`docs/round2-final.md` (source: `docs/Quorum_Round_2_Final.docx`); `docs/round2-answers.md` is the working
draft this code was built from. The final state flow is `docs/quorum-flow-final.webp`, with an interactive
walk-through in `docs/quorum-trip-flow.html`.

## The flow, in one line

The organiser gives a **rough budget** and a **maximum overshoot** → the agent builds **one itinerary inside
that budget** → **each payer votes on it privately**, and only the tally reaches the group → **the yes-voters
set the trip's budget** (the heads they pay for × the lowest per-head ceiling among them) and the plan is
**re-sized for exactly those heads** and must fit → **each payer blocks their own share × (1 + overshoot)**, by
UPI mandate or a payment link (a card is held, EMI if they like; pay later via LazyPay if the share is
inside its limit; anything that can't hold pays now into the pool) → the agent **captures each share into
Quorum's account and pays every supplier from it**, by the deadline the organiser set.

A member in the group is a **payer**, not necessarily one traveller: three families of three, four and five
are three payers and twelve heads. Gathering asks each payer privately how many people they are paying
for; rooms, seats, shares, the budget and the mandates all follow the head count, while the vote stays one
per payer.

**Two personas, no corporate.** Bachelors and families. The persona is deduced (anyone paying for three or
more, or a family occasion, means families) or set by the organiser, and it shapes defaults rather than
announcing itself: daytime flights and no club nights for families, cheapest fares for bachelors, who gets
asked about medical needs and pets. Fewest bottlenecks: six taps to build the plan; names, dates of birth,
food and medical only after someone says yes, because tickets need them and no-voters never do.

**The place does the convincing.** Every message that has to go anyway carries one line about the
destination, picked for the person (their interests, the persona) and never repeated to them: the film that
was shot at the fort, the road from every reel, the quiet beach for the parents, this week's weather. At
decision time the outs get one private nudge with their own number and one chance to flip in; from the day
it's booked every traveller gets one fact about the place a day, and a countdown text a week out and the day
before carries their PNR. `app/lore.py` is the
table; swap in a real source and nothing else changes.

**The group is where the trip is fun; the DM is where the decision is made.** People say yes only after
weighing money, dates, work and who else is going, so every DM gives them what they need to weigh it — their
own number, what it covers, what money moves when, what is refundable, the deadline, what silence means — and
then leaves them alone: one reminder, none if they said they need time (`member_takes_time`), never a call to
chase, no guilt, and a no is never named or questioned in the group. The agent wants the trip to happen inside
realistic boundaries; the deadline is the boundary, not the pressure.

Trip and travel are planned separately: the stay is shared and the occasion decides what it optimises
(a wedding ranks stays by distance to the venue and lands you before the first function; a leisure trip
ranks by price); travel is per member, outbound and return searched separately, from and back to *their*
cities. Aditi flies Bengaluru → Goa and back to Mumbai. Her share is hers; nobody's overspend is averaged
into anyone else's.

## Run it (offline, two minutes)

```bash
pip install -r requirements.txt
python demo.py                      # three transcripts: wedding (book + two disruptions), leisure (failed vote → revision → dropout), the payments wall
pytest -q                           # 53 tests: the loop, every unhappy turn, the pool, the instruments, the place, and the voice rail on files
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
| GATHERING | Private DM to each member: how many people they're paying for (names help), start city, return city, dates they can't do, their own per-head ceiling, must-haves. Reply deadline; silence = just them, home city both ways, at the organiser's figure | — |
| PLANNING | Trip side: stays ranked by the occasion's priority; stays with no online inventory get a **supplier call** (availability, group rate, refund terms, 48-hour hold; retry once; the call's price wins). Travel side: per member, out and back, searched separately. Total must fit budget × (1 + overshoot) per head | logistics, **voice** |
| VOTING | One itinerary DM'd to each member: their legs, the stay split, on leisure trips the one pre-booked activity the group shares (an essential) and the things to do on the day, their all-in against their own ceiling, what they'd authorise, and one line about the place. Yes/no with a reason, privately. One reminder text. Majority of members ⇒ passes; else up to two revisions built from the reasons; then the organiser decides. When it passes, everyone who said no or nothing gets one private nudge with their own number and 12 hours to flip in | — |
| AUTHORISING | The yes-voters set the budget: the heads they pay for × the lowest per-head ceiling among them. The plan is re-sized for exactly those heads (rooms re-split, seats per payer; the same hook is where a car or an activity would re-size) and must fit that budget, else it goes back to the group as a revision. Then every yes-voter blocks share × (1 + overshoot) their way: a UPI mandate (Reserve Pay keeps the headroom live; an OTM takes one capture), or a payment link (a card is held for one capture, EMI tenures quoted; a method that can't hold pays now and the money waits in the pool, refunded if the trip lapses). Yes-voters who don't block drop out and the rest are re-sized and re-checked: inside ⇒ proceed, over ⇒ back to the group. Nobody can revoke from their own app; they ask Quorum, which releases | **payments** |
| BOOKING | Re-price at live fares (inside the cap: absorbed, shown on the receipt; over: only that member is asked to top up). Re-hold an expired phone hold. Debit every share into **the pool, Quorum's merchant account**; **one failure refunds the rest and stops**. Book legs and stays and **pay each supplier from the pool**, logged per booking. The pool can never go negative: nobody fronts, Quorum included | payments, logistics, voice |
| BOOKED | Tickets and vouchers in DMs; the group gets one post. A cancelled leg's refund comes back into the pool and the re-booked ticket is paid from it, inside the member's cap (a late arrival gets the phone-only stay a call); if every alternative is over the cap: a text with options, then an **escalation call** after 20 minutes, then a top-up from that member. A **missed** departure is the member's: every way to still get there (later flights, a train, an Uber Outstation cab) goes to them soonest-first, no refund, paid from their headroom or a top-up. One fact about the place per traveller per day from booking to departure (P2, 09:00, never repeated); countdown texts with PNR at T-7 and T-1 | logistics, voice, payments |
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
| Payments | `MockPayments` — a block per member that the member fixes as **UPI Reserve Pay** (multi-debit), a **UPI one-time mandate** (one capture) or a **credit-card hold** (one capture, 7-day life, EMI tenures offered); cumulative `capture()`, `refund()`, `fail_capture_for` to bounce one debit, and **the pool**: captures settle into it, `pay_supplier()` draws on it and refuses to go negative | `PineLabsPayments` — one pre-authorised **payment link** per member (`pre_auth: "true"`, CARD + UPI, expiring at the deadline) → Capture Order / Cancel Order → Refunds; Offer Discovery for EMI tenures; Payouts for suppliers | Written against pinelabs.com/docs; needs UAT keys with Pay by Link + pre-authorization enabled. Every path is marked [verify] in the file |
| Logistics | `MockLogistics` — hand-written fares for Kolkata / Bengaluru / Delhi / Mumbai ↔ Goa, five stays (two phone-only), `drift` to move live fares | none yet | Delhivery's role in this opening is distances (ranking stays by km to the venue), not parcels; `km_to_venue` is hand-written today |

Switch with `QUORUM_VOICE=gnani` and `QUORUM_PAYMENTS=pinelabs` (see `.env.example`). **Keys:** the Gnani
speech key is committed in `app/keys.py` on purpose — private repository, one-month life, one holder, a key
that can only spend metered speech credit, and `CallBudget` refuses the 41st billable call per checkout.
Revoke it on the Gnani dashboard when the competition ends. Pine Labs keys stay in `.env`, which is
git-ignored.

## What we are asking each rail for

Short version — full argument in `docs/rails.md` and `docs/round2-answers.md` §4:

* **Pine Labs.** Quorum is the merchant of record: members' blocks (card holds, UPI mandates, Reserve Pay,
  prepaid links) settle into Quorum's account, the pool, and Quorum pays suppliers from it. Money prepaid
  through a link sits with Quorum until booking; the float is Quorum's to earn on and Quorum's to lose on,
  logged as an estimate in the rail log and marked as the idea it is. The ask is a **group order with
  escrow**: N blocks bound to one order, a shared expiry, an **atomic capture** into a per-order escrow, and
  settlement to the suppliers only when the order is secured. Pine Labs already has the two halves: split
  settlement holds one payer's money and releases it on a call, and P3P lets an agent spend inside one
  consumer's mandate. Today `Engine._capture_all` loops one capture per member and, when the fourth fails,
  refunds the three that went through. `python demo.py` shows it happening.
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
docs/round2-final.md    the eight Round-2 answers as submitted (Quorum_Round_2_Final.docx)
docs/round2-answers.md  the working draft this code implements
docs/quorum-flow-final.webp, docs/quorum-trip-flow.html  the final state flow: diagram and interactive walk-through
docs/rails.md      what exists, what we ask, where it breaks
HANDOFF.md         next tasks, in priority order, for whoever builds next
```
