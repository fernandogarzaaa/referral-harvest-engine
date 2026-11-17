"""State-machine transition tests: every legal move allowed, every illegal one raises."""

import pytest

from referral_harvest.models import (
    AskStatus,
    Referral,
    ReferralAsk,
    ReferralStatus,
    Reward,
    RewardKind,
    RewardStatus,
)
from referral_harvest.transitions import IllegalTransition, legal_transitions, transition
from referral_harvest.util import utcnow


def _ask():
    return ReferralAsk(
        tenant_id="t",
        job_id="j",
        customer_id="c",
        code="MIKE-ABCD",
        expires_at=utcnow(),
        status=AskStatus.scheduled,
    )


def _referral():
    return Referral(
        tenant_id="t",
        code="MIKE-ABCD",
        referee_name="R",
        referee_phone_e164="+15550001111",
        status=ReferralStatus.claimed,
    )


def _reward():
    return Reward(
        referral_id="r",
        kind=RewardKind.credit,
        amount_cents=2500,
        status=RewardStatus.pending,
    )


def test_ask_full_lifecycle():
    ask = _ask()
    assert transition(ask, AskStatus.sent) is ask  # returns obj for chaining
    assert ask.status == AskStatus.sent
    transition(ask, "expired")  # string form also accepted
    assert ask.status == AskStatus.expired


def test_ask_cancel_paths():
    a1, a2 = _ask(), _ask()
    transition(a1, AskStatus.cancelled)
    transition(a2, AskStatus.sent)
    transition(a2, AskStatus.cancelled)


def test_referral_full_lifecycle():
    r = _referral()
    transition(r, ReferralStatus.booked)
    transition(r, ReferralStatus.completed)
    assert r.status == ReferralStatus.completed


def test_reward_full_lifecycle():
    w = _reward()
    transition(w, RewardStatus.approved)
    transition(w, RewardStatus.fulfilled)
    assert w.status == RewardStatus.fulfilled


@pytest.mark.parametrize(
    "make,fro,to",
    [
        (_ask, AskStatus.scheduled, AskStatus.expired),  # skip sent
        (_ask, AskStatus.sent, AskStatus.scheduled),  # backwards
        (_ask, AskStatus.expired, AskStatus.cancelled),  # out of terminal
        (_ask, AskStatus.cancelled, AskStatus.sent),  # out of terminal
        (_referral, ReferralStatus.claimed, ReferralStatus.completed),  # skip booked
        (_referral, ReferralStatus.booked, ReferralStatus.claimed),  # backwards
        (_referral, ReferralStatus.completed, ReferralStatus.booked),  # out of terminal
        (_reward, RewardStatus.pending, RewardStatus.fulfilled),  # skip approved
        (_reward, RewardStatus.approved, RewardStatus.pending),  # backwards
        (_reward, RewardStatus.fulfilled, RewardStatus.cancelled),  # out of terminal
    ],
)
def test_illegal_transitions_raise(make, fro, to):
    obj = make()
    obj.status = fro
    with pytest.raises(IllegalTransition):
        transition(obj, to)


def test_self_transition_from_terminal_raises():
    ask = _ask()
    ask.status = AskStatus.expired
    with pytest.raises(IllegalTransition):
        transition(ask, AskStatus.expired)


def test_illegal_transition_message_names_states():
    ask = _ask()
    with pytest.raises(IllegalTransition, match="scheduled -> expired"):
        transition(ask, AskStatus.expired)


def test_legal_transitions_sets():
    assert legal_transitions(AskStatus.scheduled) == {AskStatus.sent, AskStatus.cancelled}
    assert legal_transitions(AskStatus.expired) == set()
    assert legal_transitions(ReferralStatus.claimed) == {
        ReferralStatus.booked,
        ReferralStatus.cancelled,
    }
    assert legal_transitions(RewardStatus.approved) == {
        RewardStatus.fulfilled,
        RewardStatus.cancelled,
    }
