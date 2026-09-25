"""Domain objects. Everything the engine reasons about lives here."""
from __future__ import annotations
import math
from datetime import datetime, date
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class TripState(str, Enum):
    INITIATED = "INITIATED"        # organiser named destination, dates, occasion, budget and overshoot
    GATHERING = "GATHERING"        # private DMs out: start city, return city, dates, must-haves
    PLANNING = "PLANNING"          # trip side (stays, supplier calls) and travel side (per member) being built
    VOTING = "VOTING"              # one itinerary DM'd to each member; private yes/no; only the tally is posted
    AUTHORISING = "AUTHORISING"    # every yes-voter blocks share × (1 + overshoot) by UPI mandate
    BOOKING = "BOOKING"            # re-price at live fares, debit each share, book legs and stays
    BOOKED = "BOOKED"              # tickets and vouchers out; legs watched until the trip is over
    LAPSED = "LAPSED"              # nothing booked, nothing charged: vote failed for good, or a capture rolled back
    CLOSED = "CLOSED"              # organiser closed the trip


class AuthStatus(str, Enum):
    PENDING = "PENDING"            # mandate created, member has not approved in their UPI app
    BLOCKED = "BLOCKED"            # member approved; funds blocked, not debited
    CAPTURED = "CAPTURED"          # a debit executed (the share, or a later presentation inside the cap)
    RELEASED = "RELEASED"          # block released / mandate cancelled; nothing charged
    REVOKED = "REVOKED"            # member revoked the mandate in their UPI app
    EXPIRED = "EXPIRED"            # validity ran out without capture
    REFUNDED = "REFUNDED"          # captured, then returned because the group booking could not complete


class Member(BaseModel):
    id: str = Field(default_factory=lambda: new_id("m"))
    name: str
    phone: str
    home_city: str                           # the default start and return city
    past_trips: list[str] = Field(default_factory=list)
    pinelabs_customer_id: Optional[str] = None

    @property
    def first(self) -> str:
        return self.name.split()[0]


class Constraint(BaseModel):
    """One member's private answers to the gathering DM. Silence = home city both ways, stated dates."""
    member_id: str
    available: bool = True
    start_city: str = ""
    return_city: str = ""
    earliest: Optional[date] = None
    latest: Optional[date] = None
    must_haves: list[str] = Field(default_factory=list)
    replied_at: Optional[datetime] = None
    defaulted: bool = False


class Leg(BaseModel):
    id: str = Field(default_factory=lambda: new_id("leg"))
    member_id: str
    mode: str                                # flight / train / bus
    carrier: str
    origin: str
    destination: str
    depart: datetime
    arrive: datetime
    price: int                               # INR per head, live
    quoted: Optional[int] = None             # INR per head at the time of the vote (fares move)
    pnr: Optional[str] = None
    status: str = "QUOTED"                   # QUOTED / BOOKED / CANCELLED / REBOOKED


class CallRecord(BaseModel):
    """What came back from one outbound call, as fields. The engine never parses prose."""
    to: str                                  # property or member name
    phone: str
    language: str
    purpose: str                             # AVAILABILITY / LATE_ARRIVAL / ESCALATION
    disposition: str                         # CONFIRMED / UNAVAILABLE / NO_ANSWER
    available: Optional[bool] = None
    rate_per_room_night: Optional[int] = None
    rooms: Optional[int] = None
    twin_sharing: Optional[bool] = None
    refund_terms: Optional[str] = None
    hold_until: Optional[datetime] = None
    choice: Optional[int] = None             # escalation: the option the member picked, 1-based
    transcript: str = ""
    call_ref: Optional[str] = None
    called_at: Optional[datetime] = None


class Stay(BaseModel):
    id: str = Field(default_factory=lambda: new_id("stay"))
    name: str
    city: str
    area: str                                # neighbourhood, e.g. Assagao
    phone: str
    rate_per_room_night: int                 # INR per twin room per night; the listing's figure until a call replaces it
    nights: int
    phone_only: bool = False                 # no online inventory: availability, rate and hold come from a call
    listing_note: str = ""                   # what the listing (or the organiser's tip) says
    km_to_venue: float = 0.0                 # distance to the venue (wedding / offsite) or the beach (leisure)
    calls: list[CallRecord] = Field(default_factory=list)
    hold_until: Optional[datetime] = None
    booking_ref: Optional[str] = None

    @staticmethod
    def rooms_for(guests: int) -> int:
        return math.ceil(guests / 2)         # twin sharing

    def per_head(self, guests: int) -> int:
        if guests <= 0:
            return 0
        return math.ceil(self.rooms_for(guests) * self.rate_per_room_night * self.nights / guests)

    def confirmed(self) -> bool:
        """Listed stays are taken from the listing; phone-only stays need a CONFIRMED call."""
        return (not self.phone_only) or bool(self.calls and self.calls[-1].disposition == "CONFIRMED")


class Plan(BaseModel):
    """One itinerary: the shared trip (stays) plus each traveller's own travel, priced per head."""
    id: str = Field(default_factory=lambda: new_id("plan"))
    version: int                             # 1 = first plan, 2 and 3 = revisions
    travellers: list[str]                    # member ids the stay is split among
    legs: list[Leg]                          # out + back per traveller, searched separately
    stays: list[Stay]                        # one or more; the occasion decides
    start: date
    end: date
    note: str = ""                           # what this revision changed, in a few words

    def nights(self) -> int:
        return (self.end - self.start).days

    def legs_for(self, member_id: str) -> list[Leg]:
        return [l for l in self.legs if l.member_id == member_id]

    def travel(self, member_id: str) -> int:
        return sum(l.price for l in self.legs_for(member_id))

    def stay_share(self) -> int:
        return sum(s.per_head(len(self.travellers)) for s in self.stays)

    def per_head(self, member_id: str) -> int:
        return self.travel(member_id) + self.stay_share()


class Vote(BaseModel):
    member_id: str
    yes: bool
    reason: str = ""                         # if no: what would make it a yes
    at: datetime


class Authorisation(BaseModel):
    member_id: str
    plan_id: str
    amount: int                              # INR — the cap: share × (1 + overshoot), or a top-up
    purpose: str = "SHARE"                   # SHARE / TOP_UP
    status: AuthStatus = AuthStatus.PENDING
    rail_ref: Optional[str] = None           # Pine Labs subscription_id (OT mandate)
    order_ref: Optional[str] = None          # Pine Labs order_id
    created_at: Optional[datetime] = None
    captured_amount: int = 0                 # cumulative presentations, never above `amount`


class Decision(BaseModel):
    """A choice one member owes the agent: a re-booking option, or a top-up at booking time."""
    member_id: str
    kind: str                                # REBOOK / TOP_UP
    leg_id: Optional[str] = None
    options: list[Leg] = Field(default_factory=list)
    choice: Optional[int] = None             # 1-based index into options
    shortfall: int = 0                       # INR above what the member has authorised
    asked_at: datetime
    escalated: bool = False                  # an escalation call has been placed


class Event(BaseModel):
    at: datetime
    channel: str                             # group / dm:<member_id> / rail:<name> / system
    actor: str                               # agent / member name / rail / system
    text: str


class Trip(BaseModel):
    id: str = Field(default_factory=lambda: new_id("trip"))
    name: str
    organiser_id: str
    members: list[Member]
    destination: str
    venue: str = ""                          # wedding venue / offsite hotel / temple; ranks stays by distance
    start: date
    end: date
    occasion: str = "leisure"                # leisure / wedding / offsite / pilgrimage
    budget: int                              # INR per head, all-in (travel + stay), the organiser's figure
    overshoot: float = 0.10                  # the most the agent may go above budget, per head; also the mandate headroom
    state: TripState = TripState.INITIATED
    constraints: dict[str, Constraint] = Field(default_factory=dict)
    gather_deadline: Optional[datetime] = None
    plans: list[Plan] = Field(default_factory=list)
    votes: dict[str, Vote] = Field(default_factory=dict)
    vote_deadline: Optional[datetime] = None
    reminded: list[str] = Field(default_factory=list)
    awaiting_organiser: Optional[str] = None # why the agent stopped and asked: no_fit / vote_failed
    stays_out: list[str] = Field(default_factory=list)  # phone-only stays that had no rooms or never answered
    authorisations: dict[str, Authorisation] = Field(default_factory=dict)
    top_ups: dict[str, Authorisation] = Field(default_factory=dict)
    auth_deadline: Optional[datetime] = None
    dropped: list[str] = Field(default_factory=list)   # yes-voters who did not authorise, revoked, or left
    pending: dict[str, Decision] = Field(default_factory=dict)
    events: list[Event] = Field(default_factory=list)

    # ---- helpers
    def member(self, member_id: str) -> Member:
        return next(m for m in self.members if m.id == member_id)

    def organiser(self) -> Member:
        return self.member(self.organiser_id)

    def nights(self) -> int:
        return (self.end - self.start).days

    def limit(self) -> int:
        """Per head, all-in: budget plus the overshoot the organiser allowed."""
        return int(round(self.budget * (1 + self.overshoot)))

    def majority(self) -> int:
        return len(self.members) // 2 + 1

    def plan(self) -> Optional[Plan]:
        return self.plans[-1] if self.plans else None

    def travellers(self) -> list[Member]:
        return [m for m in self.members if self.constraints.get(m.id, Constraint(member_id=m.id)).available]

    def yes_voters(self) -> list[str]:
        return [v.member_id for v in self.votes.values() if v.yes]

    def in_members(self) -> list[Member]:
        """Everyone who voted yes and has not dropped out. The trip is booked for exactly these people."""
        yes = set(self.yes_voters())
        return [m for m in self.members if m.id in yes and m.id not in self.dropped]

    def blocked(self) -> list[Authorisation]:
        return [a for a in self.authorisations.values() if a.status == AuthStatus.BLOCKED]
