# Round 2 — answers to the eight questions (draft)

Opening: Planning the trip · Team: Aabhas, Aditi, Sayan (IIM Calcutta) · Closes Fri 25 Sep, 11:59 PM IST

Written outside the form on purpose: the Typeform has no save button. Check each answer against the
word limit the form shows before pasting. Lines marked **[verify]** name something we have not yet
confirmed in the rail's documentation or sandbox — confirm or cut before submitting.

What changed after the call with KP:

1. **Trip and travel are two problems.** Travel is per member (each person's own origin, and the return
   can end somewhere else). The trip is shared (stays, dates, the occasion). The agent plans them
   separately and joins them at the destination.
2. **Purpose comes first.** Wedding, leisure, business offsite, pilgrimage. The occasion decides what gets
   optimised — travel time, price, one stay or several — before any fare is searched.
3. **Voice does three P0 jobs, not one.** Call a property only when its listing is missing what we need;
   call a member when a deadline is close and they have not acted; call everyone when a booked leg breaks.

---

## 1. Outcome (one sentence)

Every member of a group trip is booked on travel from their own city and back, into stays that fit the
occasion, inside the ceiling they privately set, by the deadline — without anyone fronting money or chasing
anyone.

## 2. Autonomy — L3

The most consequential thing Quorum does without asking: **it debits each member's blocked UPI mandate and
books the trip once quorum is reached, and re-books a cancelled leg inside that member's pre-authorised cap.**
Both happen inside limits the humans set beforehand — each member's ceiling, the mandate amount (share + 10 %
headroom), the quorum, the 48-hour clock. Anything outside those limits (a re-book above the cap, a
below-quorum group that wants to go anyway, a member dropping out after booking) goes back to people. That is
L3. It is not L4: it does not decide *whether* the group travels, and it is not judged on the trip having
happened if the group lets the clock run out.

## 3. States — happy flow and unhappy flow

### Happy flow

```
TRIGGERED ─► PURPOSE ─► CAPTURING ─► PLANNING ─────────────► POSTED ─► BOOKED ─► TRAVELLING ─► DONE
  organiser   occasion    private DMs   ┌ TRIP  (shared) ┐    two options,  quorum    live legs
  adds agent  sets the    per member:   │ stays, 1..n    │    one default,  met:
  names       priority    dates,        │ verify gaps    │    48h clock,    debit +
  destination weights     ceiling,      ├ TRAVEL (each)  ┤    each member   book
  + occasion              origin AND    │ out: home→dest │    blocks own
                          return city   │ back: dest→any │    share (UPI)
                                        └────────────────┘
```

| State | What happens | Rail |
|---|---|---|
| TRIGGERED | Organiser adds Quorum to the WhatsApp group, names destination, rough dates, rough budget | — |
| PURPOSE | Organiser picks the occasion. It sets the priority weights (table below) | — |
| CAPTURING | Private DM to each member: dates, ceiling, **where they start and where they return to**, must-haves. Reply deadline; silence = default | chat |
| PLANNING — trip | Shared part: stay(s) for the group. Occasion decides one stay or several (e.g. 2 nights North Goa + 2 nights South Goa; for a wedding, near the venue + an optional extension) | logistics (distances); voice (only for gaps) |
| PLANNING — travel | Per member, outbound and return searched **separately**: Aditi flies Bengaluru → Goa but returns Goa → Mumbai. Arrival windows are aligned to the trip, not to each other | fares (4th rail) |
| POSTED | Two bundles in the group, one marked default, 48-hour clock. Each member approves a UPI one-time mandate for their share + 10 % headroom. Nothing is charged | payments |
| Deadline − 6h | Anyone who has not approved gets a **deadline call** | voice |
| BOOKED | Quorum met ⇒ present each mandate for the share, book every leg and stay | payments + fares |
| TRAVELLING | Legs are watched. Disruption ⇒ re-book inside the cap ⇒ **emergency call** to the affected member | voice + fares |

How the occasion sets priorities:

| Occasion | Fixed | Optimise first | Stays |
|---|---|---|---|
| Wedding | Dates, venue, arrive before the first function | Travel time and arrival buffer, then price | Near the venue; optional extension stay after |
| Leisure | Rough dates only | Price per head, then stay quality | Often two or more (split the destination) |
| Business offsite | Dates, venue | Door-to-door travel time, fewest connections | One, near the venue |
| Pilgrimage / family | Dates around the ritual | Comfort for elders (direct legs, trains over red-eyes) | One, walking distance |

### Unhappy flow

| # | Failure | Where | What Quorum does |
|---|---|---|---|
| 1 | Member never replies to the DM | CAPTURING | Default applied (dates = yes, ceiling = organiser's number); stated in the DM up front |
| 2 | Member's ceiling is below every option | PLANNING | Tells them privately what the cheapest option costs; they raise it or opt out. The group never sees whose ceiling bound |
| 3 | Listing is missing what we need (refund terms, twin-sharing, rooms as pictured) | PLANNING — trip | **Verification call** to the property for the missing fields only. Mismatch ⇒ drop, call the next one |
| 4 | Property does not answer / call fails | PLANNING — trip | Retry once; still nothing ⇒ drop from the list, say so in the group post |
| 5 | Return city differs and no return leg fits the trip end | PLANNING — travel | Offer that member the nearest fitting leg (±1 day or a train) privately; never changes the group's dates |
| 6 | Wedding: no leg arrives before the first function | PLANNING — travel | Flags it to that member before posting; the arrival buffer is a hard constraint, not a preference |
| 7 | Fare rises during the 48h window, within 10 % headroom | POSTED → BOOKED | Absorbed by the headroom, no new approval |
| 8 | Fare rises beyond headroom | POSTED → BOOKED | Re-asks only the affected members for the difference, with a short clock |
| 9 | Member has not approved 6h before the deadline | POSTED | **Deadline call** — who is in, what they owe, "approve in your UPI app now or you're out" |
| 10 | Quorum missed | POSTED | Every mandate lapses, nothing charged; organiser can re-run with a lower quorum or the other bundle |
| 11 | Member revokes mandate in their UPI app | POSTED | Counted as a no; quorum recomputed |
| 12 | One debit fails after others succeeded | BOOKED | Refund the successful debits, mark LAPSED, tell the group. This is the wall we describe to Pine Labs (Q4) |
| 13 | Carrier cancels a leg | TRAVELLING | Re-book inside the cap, then **emergency call** to the affected member(s); a text alone will not wake anyone at 1 am |
| 14 | Re-book costs more than the cap | TRAVELLING | Emergency call asks the member to approve the difference or pick a listed alternative |
| 15 | Member drops out after booking | BOOKED | Their non-refundable share stays theirs; stay split re-priced for the rest and put to them. Goes to humans |

Voice failures — **fill these from the Gnani playground before submitting** (switch Hindi/English
mid-sentence, 10s silence, talk over it, noisy street, "haan… matlab nahi"):

| # | What we did on the call | What broke | What the agent does |
|---|---|---|---|
| V1 | | | |
| V2 | | | |
| V3 | | | |

## 4. Rails — what exists, what must be built

### Gnani — voice (three P0 jobs)

Voice is not the interface; WhatsApp is. Voice is used where text fails: a property that is only reachable
by phone, a member who is about to miss a deadline, and an emergency.

| Job | Leverages (exists) | Needs built |
|---|---|---|
| Fill listing gaps | Inya Agent Builder: agent with system prompt + Jinja variables; `POST /v1/agents/{botId}/trigger_call`; pre-call dynamic variables fetched from our server (the list of *missing* fields for this property); `GET /v1/conversations/{id}/stats` / post-call webhook for disposition + transcript | **Structured extraction on the read path:** we define a schema (`refund_terms`, `twin_sharing`, `room_as_pictured`), the platform returns fields, not a transcript. Without it our engine parses prose |
| Deadline call | Same outbound call with variables (member name, amount, deadline, who is already in) | **Handoff back to the chat/payment:** member says "haan, kar deta hoon" ⇒ we need the call to end by sending the UPI approval link, i.e. a post-call action that triggers our webhook with intent `WILL_APPROVE` **[verify: actions/variables]** |
| Emergency call | Same outbound call; Hindi/English/regional voices | **Priority / DND-override calling** for transactional emergencies, and **bulk trigger** (call five members in parallel, one disposition per member) **[verify]** |
| (later) Voice-note replies | Vachana STT (`vach_` key we already hold; ₹27/audio-hour) for Hinglish voice notes in DMs | — |

Wall: outbound calls reach whitelisted numbers only in the sandbox; production needs consent/DND handling
for B2B verification calls and transactional member calls.

### Pine Labs — payments and authorisation (load-bearing)

| Leverages (exists) | Needs built |
|---|---|
| UPI One-Time Mandate: create customer → no-plan OT subscription (`plan_details.amount` = share + 10 %, `validity_days`) → payment with `mandate_info.request_type = CREATE_MANDATE` (UPI intent) → subscription `ACTIVE` = funds blocked → `POST /presentations` for the share at quorum; unpresented mandates lapse | **Group mandate:** N mandates bound to one merchant order with `min_payers`, a shared expiry, **atomic capture** (all debited or none), and one webhook `GROUP_ORDER_SECURED` / `GROUP_ORDER_LAPSED`. Also a **merchant-initiated cancel** of an `ACTIVE` OT subscription **[verify]** and validity as short as 48h **[verify]** |

What breaks without it: we loop one presentation per member. If the fourth of five fails, three people are
charged for a trip that cannot be booked, and we run refunds with three counterparties.

### Delhivery — logistics (trip side, not parcels)

| Leverages (exists) | Needs built |
|---|---|
| Maps reference: geocoding of stays and venues, distance / travel time between points **[verify exact endpoints]**. Used to (a) rank stays by distance to a wedding venue or offsite, (b) plan the transfer when the trip has two stays, (c) check airport/station → stay travel time so arrival buffers are real | **Road accessibility for the last mile** (is the lane to a homestay motorable for a cab/tempo traveller) — today that is one of the questions we have to phone the property for. Wedding logistics (gifts/outfits shipped to the venue) is a natural parcel use we are *not* claiming for Round 2 |

## 5. A fourth rail

**A group fare-and-inventory hold.** Today every fare and room rate in our bundle can move during the 48-hour
decision window, and five people from four cities means five separate fare searches with no way to hold any
of them. The rail: hold N seats across different origin → destination legs and M rooms for a fixed window,
at a quoted price, released automatically if the payments rail reports `GROUP_ORDER_LAPSED`. It pairs with
the Pine Labs group mandate: money is blocked and inventory is held on the same clock.

Who should build it: **TBO Tek** — it already distributes flight and hotel inventory B2B to agents, so the
hold is an extension of its supplier contracts, not a new business. (Alternative: ixigo, for trains.)

## 6. How a human interacts with it

* **WhatsApp group** — organiser adds Quorum as a contact, types destination, rough dates, occasion.
  Quorum posts only three things to the group: the kickoff, the two bundles, the outcome.
* **Private WhatsApp DMs** — each member answers four questions (dates, ceiling, start city, return city,
  plus must-haves) in text or a voice note. Nobody sees anyone else's ceiling.
* **Their own UPI app** — approving the mandate is the "I'm in". No card, no payment link to the organiser.
* **Phone calls, three kinds only** — the deadline call (6h before, only to members who have not approved),
  the emergency call (a booked leg broke), and outbound calls to properties the member never hears about.
* **Organiser commands in the group** — `switch to comfort`, `re-run with 4`, `close trip`.

## 7. Name

**Quorum** — the trip happens when enough people commit, not when one person pays.

## 8. Which Indian company is best placed to build this

**MakeMyTrip.** It owns both halves KP split apart — travel inventory for every origin and stays at the
destination — already sells group bookings **[verify: what its groups product covers]**, and has the
checkout traffic. What it would
have to give up is the one-card checkout its funnel is built on; that is exactly why a payments company
holding the group mandate (Pine Labs) is the threat, and why MakeMyTrip should build it first.
