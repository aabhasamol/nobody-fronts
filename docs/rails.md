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

The overshoot the organiser sets is the mandate headroom: one number governs both the proposal ("nothing
above the rough budget × (1 + overshoot) a head") and the money ("block share × (1 + overshoot), debit the
share"). It is what lets the agent absorb a fare that moved since the vote, or re-book a cancelled flight,
without going back to the group. Anything beyond it is a **top-up mandate** asked of that member alone
(`Engine._ask_top_up`). After the vote the binding number is the yes-voters' own: their count × the lowest
ceiling among them is the trip's budget, and the plan re-sized for exactly them must fit it.

### The pool

Quorum is the merchant of record. Every mandate is created by Quorum, so every presentation settles into
Quorum's merchant account: that account is the pool. From it Quorum pays each supplier (`Engine._pay`,
one logged payout per booking: the stay's rooms × rate × nights, each ticket, each re-booked ticket). A
carrier's refund on a cancelled leg comes back into the pool and the replacement ticket is paid from it.
The pool can never go negative — `pay_supplier` refuses — so nobody fronts, Quorum included. The organiser
is never in the money path; pooling in a friend's account would lose block-then-debit, hit UPI limits, and
move the trust problem rather than remove it. Paying suppliers is not a Pine Labs collection API: flights
and listed hotels go through a B2B travel wallet topped up from the settlement account, a homestay gets UPI
or a bank transfer.

### The ask

A **group order with escrow**: N UPI one-time mandates bound to **one** merchant order, carrying

* a quorum rule (`min_payers`, set by the merchant at order creation),
* a shared expiry,
* an **atomic** capture: when the merchant presents, either every active mandate is debited or none is,
* settlement into a per-order escrow, released to the suppliers (or to the merchant's wallet) only when the
  order is secured, back to every payer when it is not,
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

Gnani's API is two verbs. The **Speech APIs** (brand: Vachana; key prefix `vach_`): **text-to-speech**
(Timbre v2, `POST /api/v1/tts/inference`, Hindi and Indian-English voices) and **speech-to-text** (Prisma
v2.5, `POST /stt/v3`, `language_code` per request), over REST, with WebSocket variants for streaming.
Pricing: ₹27 per audio-hour transcribed, ₹27 per 10,000 characters synthesised, 60 requests a minute. It does
not dial phones, run a conversation, or extract fields. We hold this key (`app/keys.py`).

### One key, two verbs, and a phone line that is not Gnani's

A P0 call is assembled in `app/rails/gnani.py`:

    telephony dials → play the TTS of each line the agent says → record the other side → STT
    → read the fields out of the words

* **Speak.** Each line the agent says is synthesised once and cached by content (`gnani_speech.py`). The
  supplier script is four Hindi questions (419 characters ≈ ₹1.13 the first time, ₹0 after); the late-arrival
  notice is one line; the escalation script is English with the options read out.
* **Listen.** Each reply is transcribed (`language_code` hi-IN for suppliers, en-IN for members). A
  one-minute answer ≈ ₹0.45.
* **Read.** Rules in `gnani.py` turn words into the fields the engine acts on: yes/no with the last verdict
  winning ("haan… matlab nahi" is a no, "koi dikkat nahi" is a yes), rupee amounts from digits and
  hazaar/sau, refund terms as the sentence that mentions a refund, a 48-hour hold unless refused, and
  option one/two/ek/do/pehla/doosra for an escalation. Number-words ("teen hazaar") defeat them today.
* **The phone line** is a separate seam, `Telephony` (dial → play → listen → hang up), because it is a
  different vendor: Exotel or Twilio, a day of work. The default `FileTelephony` writes the agent's audio to
  `cache/calls/<ref>/` and takes the reply from recordings in `cache/replies/<callee>/` (one per question, or
  one for the whole call; a `.txt` is a typed reply). No recordings ⇒ the call is honestly NO_ANSWER.

That is how the demo runs the real Gnani loop for about ₹2 without a carrier: a teammate records the homestay
owner's answers on a phone, drops the files in, `python scripts/speech_demo.py --call …` prints Gnani's
transcript and the fields, and `QUORUM_VOICE=gnani python demo.py` runs the whole product on it. Every
billable call goes through `CallBudget` (40 per checkout) because the key is committed.

### How the engine uses it

Only through `Engine._call`, which takes one of three `P0_CALLS` reasons and refuses anything else:
`SUPPLIER_AVAILABILITY` (retry once on no answer; UNAVAILABLE ⇒ next stay and never call this one again;
CONFIRMED ⇒ the rate from the call replaces the listing and the hold is recorded), `LATE_ARRIVAL`, and
`DISRUPTION_UNANSWERED` (the choice comes back as a number; the engine then asks that member alone for a
top-up mandate). Every call is a P0 event in the transcript; nothing else is.

### The ask

1. **Normalised entities in the transcript.** Amounts, dates, times, yes/no, ordinal choices, returned as
   values alongside the words — so "teen hazaar do sau" arrives as 3200 and "haan… matlab nahi" as a no —
   instead of the rules we maintain in `gnani.py`. This is the structured-extraction ask, sized to the API
   Gnani actually sells.
2. **Streaming, duplex STT/TTS** on the WebSocket endpoints, so a live call does not wait for whole clips
   and the agent can be interrupted.
3. **Code-switch robustness** for Konkani/Hindi/English in one sentence, and a per-request hint that the
   speaker is a small-business owner quoting prices.

### The wall

Gnani gives us the voice, not the phone line. Placing the call, and the consent and DND rules for calls to
businesses and transactional calls to members, sit with the carrier and with us. That is fine for Round 2 —
the demo is honest about it — and it is the production question to raise: a hold agreed on a call is only as
good as the recording and the transcript we keep of it.

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
