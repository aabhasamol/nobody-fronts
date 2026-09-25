# Quorum — Round 2 submission (final)

The Ken Great Rewiring 2026 · Planning the trip · Aabhas, Aditi and Sayan

This is the text of `Quorum_Round_2_Final.docx`, the answers as submitted. `round2-answers.md` is the
working draft that led to it; where the two differ, this file is what we submitted. The state diagram for
the final flow is `quorum-flow-final.webp`, and `quorum-trip-flow.html` is the interactive walk-through.

## 1. Outcome

Book an approved group trip for every participating payer, including travel from their own city, without
requiring any one member to advance another member's share.

## 2. Autonomy level: L3

Quorum may price, book and rebook a previously approved trip within each payer's own authorised cap and the
agreed itinerary constraints. That is its most consequential unsupervised act. A change in hotel, travel
date, arrival deadline, refund terms, or a cost above a payer's cap goes back to the affected people for
approval. A simple majority decides whether a proposal proceeds to payment, but nobody is charged merely
because the group voted yes. Each payer separately authorises their own amount. This is L3: the agent acts
independently inside explicit limits and escalates outside them. A one-time UPI mandate or card hold has no
reusable headroom after capture, so later rebooking needs fresh authorisation unless an eligible
multi-debit reserve or already collected funds cover it.

## 3. States and failure handling

The customer insight is that a tentative group-chat "yes" is different from a funded commitment. Quorum
separates the social decision from payment, then asks each payer for a bounded authorisation before it
books.

Happy flow: INITIATED → GATHERING → PROPOSAL → PRIVATE VOTE → RE-SCOPE → AUTHORISATION → INVENTORY CHECK →
BOOKING → TRAVEL MONITORING → COMPLETE.

- The organiser supplies destination, dates, occasion, rough all-in budget per head and maximum permitted
  overshoot. Each payer privately supplies head count, origin and return city, constraints and their own
  ceiling. Silence never counts as consent.
- Quorum proposes one shared stay and activity plan, with separate outbound and return travel for each
  paying party. Each payer receives their own all-in share and votes privately. The itinerary carries one
  verified place hook relevant to them: for example, a film location, a locally iconic food stop, or a
  viewpoint recently drawing attention. The same hook is not repeated, and a trend is never invented. Only
  the tally reaches the group.
- If a majority says yes, Quorum recalculates rooms, seats and shares for the actual yes-voters. It checks
  both the group total and every individual ceiling. If the revised proposal materially changes what they
  voted for, it asks again. Each remaining payer then authorises their own stated maximum; the vote itself
  moves no money.
- Immediately before capture, Quorum checks live prices, availability and supplier payment terms. Only a
  fully authorised, still feasible plan proceeds. It confirms bookings, sends each traveller their own
  ticket and voucher privately, and sends the group a summary without personal travel documents.

Unhappy flow and the next state:

- No feasible plan, supplier unavailable, or price beyond a payer's limit → revise dates, inventory or
  party size and return to PROPOSAL. Quorum never hides one person's overspend inside other people's
  shares.
- Vote fails → collect private reasons and make at most two targeted revisions. A non-voter is excluded.
  An authorised payer drops out → recalculate costs and seek fresh approval for material changes; release
  unused holds.
- A phone-only stay is unreachable or its rate/terms differ → retry once, use another stay, or ask for
  approval. A verbal 48-hour hold is treated as unconfirmed inventory until reconfirmed.
- One of several payment captures fails → stop before further supplier bookings, void remaining
  authorisations, initiate refunds for captured payments, and show REFUND PENDING until confirmed. Existing
  APIs do not provide an all-or-nothing group capture.
- A carrier cancels → use its refund and search alternatives. Rebook automatically only where the original
  authorisation or collected funds still cover it and itinerary constraints hold; otherwise ask the
  affected payer. A missed departure may have no refund, so show the new price and require approval before
  an extra charge. If urgent text goes unanswered, escalate by call, then hand off to a human.

## 4. Three rails: existing capability and missing capability

**Gnani voice.** Existing: the speech APIs turn scripted questions into audio and replies into
transcripts; Gnani also advertises streaming, entity recognition and outbound voice-agent products. In our
prototype, a supplier call asks for rooms, dates, rate and cancellation terms, and a disruption call reads
specific alternatives to a traveller. We still need to validate which outbound-call features are enabled
in the competition sandbox and test real interruptions, silence and Hindi/English/Konkani switching. Our
own logic must check numbers, dates and contradictions against the spoken answer, request confirmation,
and hand uncertain commitments to a person. Our recorded-reply demo is not evidence of a live supplier
booking. The missing integration is a reliable, auditable call-to-booking handoff, not speech-to-text or
streaming as such.

**Pine Labs payments.** Existing: `POST /api/pay/v1/paymentlink` creates a hosted payment link and returns
its link/order identifiers; a configured card pre-authorisation can be captured or cancelled within its
authorisation window. UPI One-Time Mandate is a separate subscription/mandate-registration/presentation
flow: it blocks up to the enabled limit and supports one debit. UPI Reserve Pay permits multiple debits
from a blocked pool, but its published cap is ₹10,000, with limited bank availability; it cannot be our
default for a ₹20,000-plus trip share. P3P enables bounded agent payments for one user. We need a
group-order capability that ties N independently approved shares to one booking deadline, verifies every
authorisation, and conditionally captures or releases them as one unit. Until then Quorum must use
sequential capture with compensating refunds and openly show refund delays. A captured payment also has to
settle before it is available in a merchant bank account; supplier fulfilment needs an OTA/merchant
settlement arrangement, or the booking must wait. A payment link with UPI selected should not be presented
as automatically creating an OTM or Reserve Pay mandate.

**Delhivery Maps.** Existing: Geocoding locates the venue and stays; Routing and Distance Matrix return
road distances and travel-time estimates for venue-to-stay and airport/station-to-stay choices. That helps
select a wedding stay and check whether an arrival buffer is realistic. We still need a verified
vehicle-accessibility and last-mile handoff: a route estimate does not tell us whether a tempo traveller
can reach a lane or a homestay entrance. For phone-only stays we ask the supplier and record the answer.
Delhivery Maps does not provide live flight fares, rooms or travel booking inventory.

## 5. Fourth rail

A synchronised travel inventory and fulfilment rail would make Quorum much more effective. It would return
a time-stamped quote and expiry for each flight, train, room and transfer, hold those items where the
supplier permits, and expose a single status showing which parts remain bookable while the group
authorises payment. It should release unused holds on a failed group order and clearly flag inventory that
cannot be held. TBO Tek is a plausible builder because its B2B platform already aggregates and books
flights, hotels and transfers. The proposed cross-supplier hold and settlement guarantee would require new
supplier agreements; it is a capability request, not something we assume exists today.

## 6. Human interface

The organiser starts Quorum by sending a destination, dates, occasion, per-head budget and overshoot
limit. The agent sends each payer a private WhatsApp conversation for head count, cities, constraints,
their own ceiling, a proposal with one relevant and fact-checked destination nudge, and a confidential
yes/no. Each payer separately authorises their maximum through the relevant Pine Labs payment flow. The
organiser can share Quorum's group summary in the existing chat; personal votes, costs, medical details,
tickets and booking references stay private. A future native group integration would depend on Meta's
business Groups API eligibility and group-size limits, so the design does not assume a bot can simply join
any existing group. Supplier calls are used only when inventory is genuinely phone-only; travellers
receive a call only for time-critical decisions they have not answered by text. Every charge, change and
refund has a private status and receipt.

## 7. Agent name

Quorum. A trip moves from a plan to a booking only when enough people commit and each participant approves
their own share.

## 8. Indian company best placed to build it

MakeMyTrip. It already combines travel and stay inventory and offers group travel services, so it could
connect Quorum's private commitments to actual bookings and supplier relationships. The product gap is a
consumer group workflow: separate origins and budgets, private votes, individual payment authorisations, a
shared inventory clock and a clear recovery path when one payment or booking fails. Pine Labs can power
bounded payments, but payments alone cannot secure seats and rooms. MakeMyTrip has the stronger position to
own the whole trip outcome, provided it builds a multi-payer checkout and resolves supplier settlement
timing.
