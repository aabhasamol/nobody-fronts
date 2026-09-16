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

`app/rails/pinelabs.py` maps our three verbs straight onto that flow:

| Quorum verb | Pine Labs call | Notes |
|---|---|---|
| `create_block(member, amount, validity_days)` | customer → OT subscription (`amount` = share + 10 % headroom, in paisa) → CREATE_MANDATE payment | One mandate **per member**, referenced to our bundle id in `merchant_metadata` |
| `refresh(auth)` | `GET /subscriptions/ot/{id}` | `ACTIVE` ⇒ member approved ⇒ our status `BLOCKED` |
| `capture(auth, share)` | `POST /presentations` for the share only | Headroom stays on the mandate for re-booking |
| `release(auth)` | (no cancel documented) | We record `RELEASED`; the mandate expires on its own |

The 10 % headroom is a product decision the OTM design allows: block more than you intend to debit.
It is what lets the agent re-book a cancelled 6 am flight without going back to the group.

### The ask

A **group mandate**: N UPI one-time mandates bound to **one** merchant order, carrying

* a quorum rule (`min_payers`, set by the merchant at order creation),
* a shared expiry,
* an **atomic** capture: when the merchant presents, either every active mandate is debited or none is,
* and a single webhook: `GROUP_ORDER_SECURED` when quorum is reached, `GROUP_ORDER_LAPSED` when it is not.

Framed for Pine Labs: you already sit between the merchant and the gateway, and the order object already
holds one mandate. Let it hold five, with a rule.

### The wall — what breaks today

`Engine.book` loops `capture()` once per member. In production a presentation can fail (bank timeout,
insufficient funds after a parallel debit, mandate revoked in-app). If the fourth of five fails, three
people have paid for a trip that cannot be booked at the quoted group rate, and the merchant is now running
a refund workflow with three counterparties. Nothing in the documented API lets us express "all or nothing"
across mandates. We can approximate it with careful ordering and compensating refunds; we cannot make it
atomic. That is the sentence we would put in front of Pine Labs.

Two smaller walls to confirm in the sandbox:

* whether OTM validity can be as short as the 48-hour decision window (docs mention "approved validity window");
* whether there is a merchant-initiated cancel for an `ACTIVE` OT subscription, or only expiry.

## 2. Voice — Gnani / Inya (verification)

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

* `app/rails/gnani_speech.py` — runs today with our key. `scripts/speech_demo.py` synthesises the verifier's
  four Hindi questions (439 characters ≈ ₹1.19, cached forever after) and transcribes a recorded "hotel" reply
  (a two-minute answer ≈ ₹0.90). That proves the rail is real and the questions are answerable, for ₹2.
* `app/rails/gnani.py` — the full outbound loop, for when an agents-scoped key and a whitelisted number arrive.
  Until then it is documented, not demonstrated.

A third path exists if the agents key never comes: Gnani publishes Pipecat and LiveKit plugins for the
Speech APIs, so a self-hosted voice agent (Pipecat + a Twilio number) could run the same script. That is a
week of work, not a day; it is the fallback, not the plan.

### How the prototype uses it

`app/rails/gnani.py`:

1. `setup_agent()` pushes the verifier prompt once (four questions: room as pictured, road motorable, refund
   terms, twin-sharing) and a disposition prompt that classifies `VERIFIED / MISMATCH / NO_ANSWER`.
2. `verify_property(stay)` registers the hotel's listing claim and party size under a `clientReferenceId`,
   triggers the call, polls the conversation logs for that reference, reads stats, and returns a
   `VerificationRecord` the engine acts on: `MISMATCH` ⇒ drop the property and call the next one.
3. `app/main.py` serves `GET /gnani/precall?ref=…` so the agent's pre-call hook can pull the variables.

For the sandbox demo the "hotel" is a teammate's whitelisted phone answering as the property. That is
honest: the point is the structured outcome, not the phone network.

### The ask

Merchant-defined structured extraction. The agent can be prompted to collect four answers, but we get them
back inside a transcript or a free-text disposition. We want a schema on the agent — `{room_as_pictured:
bool, road_motorable: bool, refund_terms: string, twin_sharing: bool}` — filled by the platform and returned
as fields, so the engine never parses prose. Close to what Gnani's "actions and variables" already do for
CRM pushes; we want it on the read path.

### The wall

Whitelisting. Outbound calls only reach registered numbers, so the prototype cannot cold-call a real hotel
in the sandbox. Fine for Round 2; it is the production question to raise with Gnani (consent and DND rules
for B2B verification calls to businesses).

## 3. Logistics — Delhivery (no role, and why)

Delhivery moves parcels. Nothing in this opening is a parcel: the "logistics" of a group trip is seat
inventory and re-booking on cancellation, which the agent treats as execution. The `LogisticsRail`
interface exists so a fare API (or an OTA's B2B feed) can be dropped in later; `MockLogistics` fills it for
now. We would rather say "not touched" than invent a luggage-forwarding feature to tick a box.

## 4. Where value moves (for the strategy write-up)

* **Drains from:** the OTA checkout. MakeMyTrip's funnel monetises one card, one click, and the indecision
  before it (fare locks, price alerts, pay-later). A 48-hour default-and-clock with five payers is a
  different funnel.
* **Drains from:** the organiser's credit-card float and the reward points that came with fronting
  ₹80,000. Interviews should test whether some organisers *like* fronting for that reason; it is the
  adoption cost of "nobody fronts".
* **Pools at:** the payment rail that owns the group mandate — whoever holds the block holds the decision.
  That is why the primitive is worth more than the travel vertical it starts in.
