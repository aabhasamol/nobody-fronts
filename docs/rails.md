# Rails — what exists, what we built on it, what we are asking for

The Ken's Round-2 brief: look at what Pine Labs, Delhivery and Gnani already offer, and picture the agent
running on them. This is that picture, with the seams marked.

## 1. Payments — Pine Labs Online (load-bearing)

### What exists today

Pine Labs Online documents a **UPI One-Time Mandate (OTM)** product, "OT direct execution":

1. Create a customer.
2. Create a no-plan OT subscription with `plan_details.amount` (the maximum debit) and `validity_days`.
3. Register the mandate against the returned `order_id` with `mandate_info.request_type = CREATE_MANDATE`
   over UPI intent. The payer approves in their UPI app; funds are blocked, not charged.
4. Subscription moves `CREATED → ACTIVE`.
5. Execute the debit later with a **Presentation** for any amount ≤ the maximum. If no presentation is made,
   the mandate lapses at the end of `validity_days`.

That is the IPO block-and-debit primitive, opened to merchants, one payer at a time. There is also
**UPI Reserve Pay** (single block, multiple debits) for running tabs.

### How the prototype uses it

`app/rails/pinelabs.py` maps our verbs straight onto that flow:

| Quorum verb | Pine Labs call | Notes |
|---|---|---|
| `create_block(member, amount, validity_days)` | customer → OT subscription (`amount` = share × (1 + overshoot), in paisa) → CREATE_MANDATE payment | One mandate **per yes-voter**, referenced to our plan id in `merchant_metadata`. Only people who voted yes get one |
| `refresh(auth)` | `GET /subscriptions/ot/{id}` | `ACTIVE` ⇒ member approved ⇒ `BLOCKED`; `REVOKED` ⇒ treated as a dropout |
| `capture(auth, amount)` | `POST /presentations` — the share at booking; later, a re-booking difference inside the headroom | Cumulative, never above the cap. **[verify]** a second presentation on one OT mandate |
| `refund(auth)` | `POST /refunds/{order_id}` **[verify path]** | Used when a later member's presentation fails and the successful ones must be returned |
| `release(auth)` | (no cancel documented) | We record `RELEASED`; the mandate expires on its own |

The overshoot the organiser sets is the mandate headroom: one number governs both the plan ("nothing above
budget × (1 + overshoot) a head") and the money ("block share × (1 + overshoot), debit the share"). It is what
lets the agent absorb a fare that moved since the vote, or re-book a cancelled flight, without going back to
the group. Anything beyond it is a **top-up mandate** asked of that member alone (`Engine._ask_top_up`).

### The ask

A **group mandate**: N UPI one-time mandates bound to **one** merchant order, carrying

* a quorum rule (`min_payers`, set by the merchant at order creation),
* a shared expiry,
* an **atomic** capture: when the merchant presents, either every active mandate is debited or none is,
* and a single webhook: `GROUP_ORDER_SECURED` when quorum is reached, `GROUP_ORDER_LAPSED` when it is not.

Framed for Pine Labs: you already sit between the merchant and the gateway, and the order object already
holds one mandate. Let it hold five, with a rule.

### The wall — what breaks today

`Engine._capture_all` loops `capture()` once per member who is in. In production a presentation can fail
(bank timeout, insufficient funds after a parallel debit, mandate revoked in-app). If the fourth of four
fails, three people have paid for a trip that cannot be booked at the quoted group rate. The engine does the
only thing it can: refund the three, release the rest, tell the group, and offer a re-run. `python demo.py`
shows it (the third transcript). That is a compensating rollback with three counterparties and three
refund timelines, not atomicity. Nothing in the documented API lets us express "all or nothing" across
mandates. That is the sentence we would put in front of Pine Labs.

Two smaller walls to confirm in the sandbox:

* whether OTM validity can be as short as the 48-hour decision window (docs mention "approved validity window");
* whether there is a merchant-initiated cancel for an `ACTIVE` OT subscription, or only expiry.

## 2. Voice — Gnani / Inya (two jobs)

### Where we call, and where we don't

One test, applied to every place a call was tempting: **call only when the other side cannot be reached by
an API or a message in time, and the answer changes what the agent does next.** Members' constraints,
reminders before a deadline, facts about a listed hotel, flight status: all fail the test (WhatsApp or an API
already does it, and calling friends to chase them is the chasing the product removes). Two things pass:

1. **Supplier calls.** A homestay, a small guesthouse, a wedding's room block: no online inventory, phone
   only. Before the vote: rooms for the party, the group rate, refund terms, and a 48-hour hold. On the
   travel day: a late-arrival notice so the room does not go to a walk-in at 2 am.
2. **Escalation.** A member's flight is cancelled and every alternative costs more than they authorised.
   Text first, with the options. If there is no reply in 20 minutes, call, read the options, take the choice.

`VoiceRail` has exactly those methods (`call_supplier`, `notify_late_arrival`, `escalate_member`) and the
engine calls them in exactly those places (`Engine._supplier_call`, `_rebook`, `_escalate`).

### What exists today

The Inya Agent Builder Platform API (`https://api.inya.ai/platform`, header `x-api-key`) lets us
configure an agent (system prompt with Jinja variables, disposition prompt, language), place an
**outbound call** to a whitelisted number with `POST /v1/agents/{botId}/trigger_call`, and read the outcome
from `GET /v1/conversations/{id}/stats` (call status, disposition, transcript) or a post-call webhook.
Pre-call / dynamic-variable APIs let the agent fetch per-call context from our server.

### Two keys, one rail

Gnani sells two things. The **Speech APIs** (brand: Vachana; key prefix `vach_`) are STT and TTS over
REST/WebSocket — ₹27 per audio-hour, ₹27 per 10,000 characters, 60 requests a minute. The **Inya Agent
Builder** is the platform that places calls and runs the conversation; its API key carries an `agents`
permission and is issued separately. The competition credits we hold are for the first. So the prototype
keeps both paths:

* `app/rails/gnani_speech.py` — runs today with our key. `scripts/speech_demo.py` synthesises the supplier
  call's four Hindi questions (427 characters ≈ ₹1.15, cached forever after) and transcribes a recorded
  "homestay owner" reply (a one-minute answer ≈ ₹0.45). That proves the rail is real and the questions are
  answerable, for under ₹2.
* `app/rails/gnani.py` — the full outbound loop for all three calls, for when an agents-scoped key and a
  whitelisted number arrive. One Jinja prompt branches on `{{ purpose }}` (AVAILABILITY / LATE_ARRIVAL /
  ESCALATION). Until then it is documented, not demonstrated.

A third path exists if the agents key never comes: Gnani publishes Pipecat and LiveKit plugins for the
Speech APIs, so a self-hosted voice agent (Pipecat + a Twilio number) could run the same script. That is a
week of work, not a day; it is the fallback, not the plan.

### How the prototype uses it

1. `setup_agent()` pushes the prompt and a disposition prompt that classifies `CONFIRMED / UNAVAILABLE /
   NO_ANSWER`.
2. `call_supplier(stay, party, check_in, nights)` registers the party, dates and room count under a
   `clientReferenceId`, triggers the call, polls the conversation logs for that reference, reads stats, and
   returns a `CallRecord` the engine acts on: `NO_ANSWER` ⇒ retry once; `UNAVAILABLE` ⇒ next stay, never call
   this one again; `CONFIRMED` ⇒ take the rate *from the call*, not the listing, and record the hold.
3. `escalate_member(member, question, options)` triggers the call with the options as variables and reads
   `choice` back. The engine then asks that member, and only that member, for a top-up mandate.
4. `app/main.py` serves `GET /gnani/precall?ref=…` so the agent's pre-call hook can pull the variables.

For the sandbox demo the "homestay" is a teammate's whitelisted phone. That is honest: the point is the
structured outcome, not the phone network.

### The ask

**Structured extraction on the read path.** We define the schema — `{available: bool, rate_per_room_night:
int, rooms: int, twin_sharing: bool, refund_terms: string, hold_until: datetime}` for a supplier call,
`{choice: int}` for an escalation — and the platform fills it and returns fields, so the engine never parses
prose. Close to what Gnani's "actions and variables" already do for CRM pushes; we want it on the read path.
Second: a **commitment record**. A hold agreed on a call needs to come back as something the supplier can be
held to — an SMS confirmation sent from the call, say. Third: **priority calling** for transactional
emergencies (the escalation), with consent and DND handled for us.

### The wall

Whitelisting. Outbound calls only reach registered numbers, so the prototype cannot cold-call a real
homestay in the sandbox. Fine for Round 2; it is the production question to raise with Gnani (consent and
DND rules for B2B calls to businesses and transactional calls to members).

## 3. Logistics — Delhivery (distances, not parcels)

Delhivery moves parcels. Nothing in this opening is a parcel: the "logistics" of a group trip is seat
inventory and re-booking on cancellation, which the agent treats as execution behind `LogisticsRail`
(`MockLogistics` fills it until a fare API or an OTA's B2B feed is dropped in).

What Delhivery does have that the plan needs is **Maps**: geocoding and distances. The occasion decides how
stays are ranked — a wedding or an offsite ranks by distance to the venue, a pilgrimage by walking distance,
and the arrival buffer from airport to stay has to be real for "arrive before the first function" to mean
anything. `Stay.km_to_venue` carries that number; today it is hand-written. That is the one integration we
would ask for. Last-mile road accessibility (can a cab reach this homestay?) is today a question on the
supplier call. Wedding logistics — outfits or gifts shipped to the venue — is a natural parcel use we are
*not* claiming for Round 2.

## 4. Where value moves (for the strategy write-up)

* **Drains from:** the OTA checkout. MakeMyTrip's funnel monetises one card, one click, and the indecision
  before it (fare locks, price alerts, pay-later). A private vote and five separate mandates on one plan is
  a different funnel.
* **Drains from:** the organiser's credit-card float and the reward points that came with fronting
  ₹80,000. Interviews should test whether some organisers *like* fronting for that reason; it is the
  adoption cost of "nobody fronts".
* **Pools at:** the payment rail that owns the group mandate — whoever holds the block holds the decision.
  That is why the primitive is worth more than the travel vertical it starts in.
