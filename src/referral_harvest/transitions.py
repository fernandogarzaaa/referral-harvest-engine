"""Legal state-machine transitions. The single choke point for status changes.

Statuses are enums, not scattered booleans. Every mutation of an ask,
referral, or reward status must go through `transition()`; anything else
raises IllegalTransition.
"""

from __future__ import annotations

from referral_harvest.models import AskStatus, ReferralStatus, RewardStatus


class IllegalTransition(ValueError):
    """Raised when a status change is not in the legal transition map."""


_ASK: dict[AskStatus, set[AskStatus]] = {
    AskStatus.scheduled: {AskStatus.sent, AskStatus.cancelled},
    AskStatus.sent: {AskStatus.expired, AskStatus.cancelled},
    AskStatus.expired: set(),
    AskStatus.cancelled: set(),
}

_REFERRAL: dict[ReferralStatus, set[ReferralStatus]] = {
    ReferralStatus.claimed: {ReferralStatus.booked, ReferralStatus.cancelled},
    ReferralStatus.booked: {ReferralStatus.completed, ReferralStatus.cancelled},
    ReferralStatus.completed: set(),
    ReferralStatus.cancelled: set(),
}

_REWARD: dict[RewardStatus, set[RewardStatus]] = {
    RewardStatus.pending: {RewardStatus.approved, RewardStatus.cancelled},
    RewardStatus.approved: {RewardStatus.fulfilled, RewardStatus.cancelled},
    RewardStatus.fulfilled: set(),
    RewardStatus.cancelled: set(),
}

_MAPS: dict[type, dict] = {
    AskStatus: _ASK,
    ReferralStatus: _REFERRAL,
    RewardStatus: _REWARD,
}


def legal_transitions(status) -> set:
    """The set of statuses reachable from `status` (empty for terminal states)."""
    return set(_MAPS[type(status)][status])


def transition[T](obj: T, to_status: AskStatus | ReferralStatus | RewardStatus | str) -> T:
    """Move `obj.status` to `to_status`, enforcing the legal transition map.

    `to_status` may be an enum member or its string value. Raises
    IllegalTransition on any illegal move, including no-op self-transitions
    out of terminal states. Returns the object for chaining.
    """
    enum_cls = type(obj.status)
    if isinstance(to_status, str):
        to_status = enum_cls(to_status)
    allowed = _MAPS[enum_cls][obj.status]
    if to_status not in allowed:
        raise IllegalTransition(
            f"illegal transition: {obj.status.value} -> {to_status.value} on {type(obj).__name__}"
        )
    obj.status = to_status
    return obj
