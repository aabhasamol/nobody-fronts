# Round 2 — answers to the eight questions (draft)

Opening: Planning the trip · Team: Aabhas, Aditi, Sayan (IIM Calcutta) · Closes Fri 25 Sep, 11:59 PM IST

Written outside the form on purpose: the Typeform has no save button. Check each answer against the
word limit the form shows before pasting. Lines marked **[verify]** name something we have not yet
confirmed in the rail's documentation or sandbox. Confirm them or cut them before submitting.

## 0. Working notes (not for the form)

### Where do we actually need to call someone?

We used one test: **call only when the other side cannot be reached by an API or a message in time, and
the answer changes what the agent does next.** Every place we had voice, checked against that test:

| Candidate | Channel that already works | Call? | Why |
|---|---|---|---|
| Asking members for dates, start/return city | WhatsApp DM | **No** | Async, not urgent, needs a written record |
| Reminding members before the vote or authorisation deadline | WhatsApp reminder, plus a UPI collect notification | **No** | A text does the job. Calling friends to chase them is exactly the "chasing" the product removes |
| Checking an OTA-listed hotel's facts | The listing / OTA API | **No** | The data is already there |
| Flight or train status | Carrier/OTA API | **No** | Machine to machine |
| **A stay with no online inventory** (homestay, small guesthouse, wedding-block rooms) | None. Phone only | **Yes** | The only way to learn availability, the rate for the group, the missing facts (refund terms, twin sharing), and to place a hold before we ask the group for money |
| **Telling a phone-only stay about a late arrival after a disruption** | None | **Yes** | Without it, the room goes to a walk-in at 2 am |
| **A disruption where the member must decide within minutes** (the re-book costs more than they authorised, or the options differ materially) | WhatsApp, which a sleeping or travelling person misses | **Yes, as escalation only** | Text first. Call only if no reply and the decision can't wait. A re-book inside the authorised amount needs only a text |

So voice ends up with **two jobs**, both driven by the counterparty rather than invented for the rail:

1. **Supplier calls:** reach stays that exist only on the phone. Before the vote: availability, group rate,
   missing facts, and a hold. On the travel day: late-arrival notice.
2. **Urgent-decision escalation:** reach a member when a live disruption needs their yes within minutes.

We dropped the deadline-nudge calls and the verification calls to hotels that are already listed online.
In the code this is one rule: **calls are for P0 things** — the other side can't be reached any other way in
time, and the answer changes what happens next. Three reasons are on the list (`Engine.P0_CALLS`) and the
engine refuses to dial for anything else; every event carries P0/P1/P2.

Gnani's API is text-to-speech and speech-to-text. That is what we use: each line the agent says is TTS,
each reply is STT, and we read the fields (rooms, rate, refund terms, hold; the option chosen) out of the
words. The phone line itself is a carrier (Exotel/Twilio) behind a three-method seam; the demo runs the real
Gnani loop on recorded replies.

### The flow (team decision)

Organiser gives a **rough budget** and a **maximum overshoot** → agent builds an **itinerary within that
budget** → **the group votes** on it → everyone who is in **authorises their share** → the agent makes the
**essential bookings** (travel legs and stays).

KP's points are still in it: trip and travel are planned separately, and the occasion sets the priorities.

**Decisions to confirm as a team** (the draft assumes the defaults in bold):

1. The organiser's budget is **per head, all-in (travel + stay)** and is the figure the proposal is built
   to; the overshoot is a % the organiser sets, **applied to each member's all-in share** and used as the
   mandate headroom. Each member privately gives **their own ceiling** when asked for their constraints
   (silence = the organiser's figure).
2. The vote is cast **privately by DM, and only the tally is posted**. This keeps our research finding that
   people don't say no in front of the group.
3. The plan passes on **a simple majority of members**. Those who voted yes are "in".
4. A failed vote leads to **up to two revisions**, built from the private "what would make it a yes"
   replies. After that, the organiser decides.
5. **The yes-voters set the budget.** When the vote closes, the trip's budget is their number × the lowest
   ceiling among them (10 in the group, 7 say yes: 7 × the lowest of those 7). The plan is re-sized for
   exactly those people — rooms re-split; two cars for ten become one SUV for seven — and must fit that
   budget, else it goes back to the group as a revision. Then the mandates go out: each member authorises
   **quoted share × (1 + overshoot %)**.
6. Bookings happen when **everyone who is in has authorised**. If someone who voted yes doesn't authorise
   by the deadline, they drop out, and the plan is re-sized and re-priced for the rest. If it still fits
   their budget and their caps, it goes ahead. If not, it goes back to the group.
7. **The money pools in Quorum's account.** Quorum is the merchant of record: the mandates settle into its
   account and it pays every supplier from there. The organiser is never in the money path.
8. **A member is a payer, not necessarily one traveller.** Gathering asks privately how many people they are
   paying for (three families of 3, 4 and 5 = three payers, twelve heads). Rooms, seats, shares and the budget
   (heads × lowest per-head ceiling) follow the head count; the vote is one per payer.
10. **Two personas, no corporate:** bachelors and families. Deduced from who pays for whom (or set by the
   organiser) and used to shape defaults, never announced. Fewest bottlenecks: six taps to build the plan;
   names, dates of birth, food and medical only after a yes.
11. **The place does the convincing.** Every message that has to go anyway carries one line about the
   destination, picked for the person and never repeated: the film shot at the fort, the road from every
   reel, the quiet beach for the parents. At decision time the outs get one private nudge with their own
   number and 12 hours to flip in; after booking, one fact about the place a day until departure, and a
   countdown text with their PNR a week out and the day before.
9. **Two ways to block, the payer's choice:** a UPI mandate (nothing moves until booking; Reserve Pay keeps
   the headroom live), or a payment link — a credit card is only held (one capture at booking, EMI if they
   like), and any method that can't hold pays now, the money waiting in Quorum's account and refunded in full
   if the trip doesn't happen. The float on prepaid money is Quorum's, gains and losses (an idea, not a
   promise).

---

## 1. Outcome (one sentence)

Get everyone who says yes to a group trip booked on an itinerary the group voted for, with travel from and
back to their own cities, within the organiser's budget plus the overshoot they allowed, with nobody fronting
money for anyone else.

## 2. Autonomy — L3

The most consequential thing Quorum does without asking is to **book, and later re-book, at a price
different from the one the group voted on, as long as it stays inside the overshoot limit the organiser set.**
Fares move between the vote and the booking. If a leg is cancelled, Quorum re-books it. In both cases it
charges each member's pre-authorised mandate and tells them afterwards. It never charges more than the
member authorised (quoted share + overshoot). It never books a plan the group didn't vote for. It never
decides who is in. Anything past those limits goes back to people. That is L3. It is not L4, because the
group, not the agent, decides whether the trip happens.

## 3. States: happy flow and unhappy flow

### Happy flow

```
INITIATED ─► GATHERING ─► PLANNING ─────────────► VOTING ─► AUTHORISING ─► BOOKING ─► BOOKED ─► TRAVELLING ─► DONE
 organiser:   private DM:   ┌ TRIP (shared)    ┐   plan +    each "yes"      debit      tickets    live legs
 destination  start city,   │ stays 1..n       │   per-head  member          each       + stay     watched
 dates        return city,  │ phone-only stays │   cost in   approves UPI    share,     vouchers
 occasion     date limits   │  → supplier call │   the DM,   mandate:        book       in the
 budget/head  must-haves    ├ TRAVEL (each)    ┤   private   share ×         legs +     group
 overshoot %                │ out + back,      │   yes/no    (1+overshoot)   stays
                            │ searched apart   │
                            └──────────────────┘
                              total ≤ budget × (1 + overshoot)
```

| State | What happens | Rail |
|---|---|---|
| INITIATED | Organiser adds Quorum to the WhatsApp group: destination, dates, occasion, rough budget per head, maximum overshoot (e.g. ₹20,000, 10 %) | chat |
| GATHERING | Private DM to each member: where they start, where they return to, dates they can't do, must-haves. There is a reply deadline, and silence = travel from their home city on the stated dates | chat |
| PLANNING (trip) | Shared part. The occasion decides the number of stays and what gets optimised (table below). Stays with no online inventory get a **supplier call** for availability, group rate, missing facts and a 48-hour hold | Delhivery Maps (distances), Gnani (supplier call) |
| PLANNING (travel) | Per member, outbound and return searched **separately**. For example, Aditi goes Bengaluru → Goa and back to Mumbai. Arrivals are aligned to the first shared event | fares (4th rail) |
| VOTING | One itinerary is DM'd to each member: day by day, their own per-head cost, and the overshoot band. They reply yes or no with a reason. Only the tally goes to the group. Majority ⇒ passes | chat |
| AUTHORISING | The yes-voters set the budget (their number × the lowest ceiling among them); the plan is re-sized for exactly them and must fit it. Each yes-voter then approves a UPI one-time mandate for share × (1 + overshoot). Nothing is charged yet | Pine Labs |
| BOOKING | Once everyone who is in has authorised: re-price at live fares. If it's still inside each member's authorised amount, debit each share into Quorum's account (the pool) and pay each supplier from it: the legs, the stays, the phone-only hold converted with a payment | Pine Labs, fares, Gnani (confirm hold) |
| BOOKED → TRAVELLING | Tickets and vouchers go in the group, and legs are watched. From the day it's booked, every traveller gets one fact about the place a day (the film shot at the fort, the road from the reels, the quiet beach for the parents), picked for their interests and never repeated; a week out and the day before, a countdown with their PNR. If a leg is cancelled, Quorum re-books inside the authorised amount and sends a text. A phone-only stay is **called** about the late arrival | fares, Gnani |

How the occasion sets priorities:

| Occasion | Fixed | Optimise first | Stays |
|---|---|---|---|
| Wedding | Dates, venue, arrive before the first function | Travel time and arrival buffer, then price | Near the venue (often the family's room block, phone-only); optional extension after |
| Leisure | Rough dates only | Price per head, then stay quality | Often two or more (split the destination) |
| Business offsite | Dates, venue | Door-to-door travel time, fewest connections | One, near the venue |
| Pilgrimage / family | Dates around the ritual | Comfort for elders (direct legs, trains over red-eyes) | One, walking distance |

### Unhappy flow

| # | Failure | Where | What Quorum does |
|---|---|---|---|
| 1 | No itinerary fits budget × (1 + overshoot) | PLANNING | Tells the organiser what the cheapest plan costs and what drives it (e.g. Delhi fares on those dates). The organiser raises the budget, shifts the dates, or drops a stay. The agent never quietly exceeds the limit |
| 2 | One member's travel alone breaks the per-head limit (a far-off start city) | PLANNING | Shows that member privately what their leg costs. Trains or other dates are offered. Their overspend is never averaged into everyone else's share without the vote showing it |
| 3 | Phone-only stay: no answer, or no availability | PLANNING | Retry once, then move to the next stay. The plan says which stays were confirmed by phone |
| 4 | Phone-only stay's answer differs from the listing (rate, rooms, refund terms) | PLANNING | Takes the price from the call, not the listing. If it's worse, drop it |
| 5 | Vote fails | VOTING | Collects privately what would make it a yes, then revises (cheaper stay, other dates). After two revisions, the organiser decides |
| 6 | Member doesn't vote | VOTING | One reminder text, then counted as not in. No call. When the plan passes, the outs and the silent get one private nudge — their own number with them in, a line about the place — and 12 hours to flip in on the same terms |
| 7 | Yes-voter doesn't authorise by the deadline | AUTHORISING | Drops out, and the plan is re-priced for the rest. Within the overshoot limit ⇒ proceed. Outside it ⇒ back to the group |
| 8 | Fare moves between the vote and booking, within the authorised amount | BOOKING | Absorbed. The difference is shown on the receipt |
| 9 | Fare moves beyond the authorised amount | BOOKING | Asks only the affected members to top up, on a short clock. Otherwise they're offered the next leg that fits |
| 10 | Supplier's hold expired before booking | BOOKING | Calls again to re-hold. If the room is gone, the next stay goes to a quick yes/no, only if it's more expensive |
| 11 | Member revokes the mandate in their UPI app | AUTHORISING / BOOKING | Treated like #7 |
| 12 | One debit fails after others succeeded | BOOKING | Refund the successful debits, stop, and tell the group. This is the wall in Q4 (Pine Labs) |
| 13 | Carrier cancels a leg, and the re-book fits inside the authorised amount | TRAVELLING | Re-book and send a text. Call the phone-only stay about the late arrival |
| 13a | Member misses the departure | TRAVELLING | No refund, and it's their money. Every way to still get there today — later flights, the train, an Uber Outstation cab — goes to them soonest-first with the price; headroom first, a top-up for the rest; escalation call after 20 minutes if the text goes unanswered |
| 14 | Carrier cancels, and the re-book exceeds the authorised amount, or the member must choose, with little time | TRAVELLING | Text with options. If there's no reply within N minutes, an **escalation call** to that member asks for their choice by voice |
| 15 | Member drops out after booking | BOOKED | Their non-refundable legs stay theirs. The stay split is re-priced for the rest and put to them. This goes to humans |

Voice failures: **fill these from the Gnani playground before submitting.** Script the agent as the
supplier caller ("do you have 3 rooms, twin sharing, 2–6 Oct, for 5 people, and can you hold them 48
hours?"). Have a teammate play a Goa homestay owner who switches between Konkani/Hindi/English mid-sentence,
quotes a price and then changes it, says "haan… matlab nahi", goes silent, or talks over the agent.

| # | What we did on the call | What broke | What the agent does |
|---|---|---|---|
| V1 | | | |
| V2 | | | |
| V3 | | | |

## 4. Rails: what exists, what must be built

### Gnani (voice): two jobs, both where no other channel works

Voice is not the interface; WhatsApp is. We call only when the other side has no API and no reliable
message channel, or when a decision can't wait for a text to be read.

| Job | Leverages (exists) | Needs built |
|---|---|---|
| **Supplier call** to phone-only stays: availability, group rate, missing facts, hold. Later, the late-arrival notice | Gnani Speech APIs: **text-to-speech** (Hindi and Indian-English voices, ₹27 per 10k characters) speaks the four questions, once, cached; **speech-to-text** (`language_code` per request, ₹27 per audio-hour) transcribes each reply. We hold the key and the loop runs today on recorded replies (`app/rails/gnani.py`, `scripts/speech_demo.py --call`) | **The phone line** — Gnani does not dial; a carrier (Exotel/Twilio) sits behind a three-method seam. **Normalised entities in the transcript**: amounts, dates, yes/no, so "teen hazaar do sau" comes back as 3200 and "haan… matlab nahi" as a no; today we read fields with rules and number-words defeat them. **Streaming STT/TTS** so the call is live, not clip-by-clip |
| **Escalation call** to a member during a disruption | The same two verbs: TTS reads the options in English, STT hears "one"/"two"/"doosra" | **Ordinal/choice normalisation** in the transcript, and **code-switch robustness** (Konkani/Hindi/English mid-sentence) |

Wall: Gnani gives us the voice, not the phone line. Placing the call, and the consent and DND rules for
calls to businesses and transactional calls to members, are the carrier's and ours. A hold agreed on a call
is only as good as the recording and transcript we keep of it.

### Pine Labs (payments and authorisation, load-bearing)

| Leverages (exists) | Needs built |
|---|---|
| **Payment links** (`POST /api/pay/v1/paymentlink`): one per member for share × (1 + overshoot), `pre_auth: "true"`, `allowed_payment_methods: ["CARD","UPI"]`, `expire_by` = the deadline. On the hosted page the member picks a **credit card** (a pre-authorisation: one capture within 5–7 days, **EMI** tenures via Offer Discovery, Quorum settled in full) or **UPI** (a one-time mandate: blocked in the account, one capture, ₹1 lakh / 60 days; or Reserve Pay for multiple debits). Capture Order at booking, Cancel Order to release. Captures settle into Quorum's merchant account — the pool. **Payouts** API pays a homestay from it; split settlement with `on_hold` can hold a sub-merchant's share until release. **P3P**, their agent-payments protocol, already lets an agent spend inside one consumer's mandate | **Group order with escrow:** N blocks — card holds and UPI mandates alike — bound to one merchant order, with a shared expiry, **atomic capture** (all or none) into a per-order escrow, settlement to the suppliers only when the order is secured, and one webhook `GROUP_ORDER_SECURED` / `GROUP_ORDER_LAPSED`. Put differently: P3P for N consumers on one plan. Also: headroom on a card hold — today one capture releases the rest, so a re-booking on a card is a fresh tap |

What breaks without it: we loop one capture per member. If the fourth of five fails, three people are
charged for a trip that can't be booked, and we run refunds with three counterparties, card refunds taking
days. And credit — which is how people actually pay for trips — brings its own wall: a card hold takes one
capture, so the overshoot headroom that makes re-booking automatic on UPI needs a fresh tap on a card.

### Delhivery (logistics: trip side, not parcels)

| Leverages (exists) | Needs built |
|---|---|
| Maps reference: geocoding of stays and venues, distance / travel time between points **[verify exact endpoints]**. Used to (a) rank stays by distance to the venue, (b) plan the transfer when the trip has two stays, (c) check airport/station → stay time so arrival buffers are real | **Last-mile road accessibility** (can a cab or tempo traveller reach this homestay?). Today that's a question on the supplier call. Wedding logistics (gifts or outfits shipped to the venue) is a natural parcel use we are *not* claiming for Round 2 |

## 5. A fourth rail

**A group fare-and-inventory hold.** The plan the group votes on is priced at one moment. Between the vote
and the last authorisation, every fare and room rate can move, and five people from four cities means five
separate fare searches with no way to hold any of them. Today the overshoot limit absorbs that drift. A hold
rail would remove it: hold N seats across different origin → destination legs, and M rooms, for a fixed
window at a quoted price, released automatically when the payments rail reports `GROUP_ORDER_LAPSED`. Money
is blocked and inventory is held on the same clock.

Who should build it: **TBO Tek**, which already distributes flight and hotel inventory B2B, so a hold is an
extension of its supplier contracts. Alternative: ixigo, for trains.

## 6. How a human interacts with it

* **Organiser, in the WhatsApp group:** adds Quorum as a contact and sends one message with destination,
  dates, occasion, budget per head and maximum overshoot (e.g. "Goa, 2–6 Oct, leisure, 20k, 10 %"). Later
  commands: `revise`, `raise budget to 22k`, `close trip`.
* **Members, in private WhatsApp DMs:** answer the gathering questions (text or voice note) — including how
  many people they're paying for — receive the itinerary with their own cost, and vote yes/no with a reason.
  Nobody sees anyone else's vote, reason or party, only the tally.
* **Members, in their UPI app or on a payment link:** approving the mandate, or holding a card, is the "I'm
  in". No transfer to the organiser: the money settles into Quorum's account and Quorum pays the suppliers.
* **The group chat:** gets four posts: kickoff, the tally, the booking confirmation with tickets, and any
  disruption.
* **Phone:** members get a call only in a live disruption that needs their choice within minutes. Stays
  that exist only on the phone get called by the agent. Members never hear those calls.
* **Paying later:** a member whose share is inside LazyPay's limit can choose pay-later on the payment link;
  the lender fronts it, Quorum is settled in full at booking, and they repay LazyPay after the trip on its
  terms. Quorum never lends and never charges interest; the same goes for card EMI.

## 7. Name

**Quorum**: the trip happens when enough people commit, not when one person pays.

## 8. Which Indian company is best placed to build this

**MakeMyTrip.** It owns both halves of the plan: travel inventory from every origin, and stays at the
destination. It already sells group bookings **[verify: what its groups product covers]**, and it has the
checkout traffic. What it would have to give up is the one-card checkout its funnel is built on. That's
why a payments company holding the group mandate (Pine Labs) is the threat, and why MakeMyTrip should build
it first.
