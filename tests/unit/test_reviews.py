"""SSA-012 pure review rules. Real DB locking, HTTP auth and dispatch remain blocked.

Run from repository root: python -m pytest -q tests/unit/test_reviews.py
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.domain.reviews import (
    ExpectedReviewBinding, FollowUpReason, ReviewBinding, ReviewDecision,
    ReviewDecisionRequest, ReviewError, ReviewErrorCode, ReviewRecord,
    ReviewStatus, TrustedActionSnapshot, TrustedReviewerContext,
    expire_pending_review, make_non_executable_follow_up, resolve_review,
)

NOW = datetime(2026, 10, 10, 17, 0, tzinfo=timezone.utc)


@pytest.fixture
def scenario():
    """Fictional salon booking, two guests: requires GROUP_SIZE exception."""
    business_id, action_id, conversation_id = uuid4(), uuid4(), uuid4()
    binding = ReviewBinding(
        business_id=business_id,
        action_id=action_id,
        action_version=2,
        payload_hash="sha256:booking-v2",
        policy_version=4,
        context_digest="sha256:context-12",
        assessment_id=uuid4(),
    )
    review = ReviewRecord(
        review_id=uuid4(),
        binding=binding,
        expires_at=NOW + timedelta(minutes=5),
        required_exception_codes=frozenset({"GROUP_SIZE"}),
        allowed_exception_codes=frozenset({"GROUP_SIZE", "SHORT_NOTICE"}),
    )
    action = TrustedActionSnapshot(
        business_id=business_id,
        conversation_id=conversation_id,
        action_id=action_id,
        action_version=2,
        payload_hash=binding.payload_hash,
        policy_version=4,
        context_digest=binding.context_digest,
        assessment_id=binding.assessment_id,
        state="AWAITING_REVIEW",
        current_policy_version=4,
        customer_confirmed=True,
        capacity_available=True,
        hard_denial_codes=frozenset(),
        session_active=True,
        expires_at=NOW + timedelta(minutes=10),
    )
    reviewer = TrustedReviewerContext(
        staff_user_id=uuid4(),
        business_id=business_id,
        role="reviewer",
        session_valid=True,
        enabled=True,
        csrf_valid=True,
    )
    expected = ExpectedReviewBinding(
        action_id=binding.action_id,
        action_version=binding.action_version,
        payload_hash=binding.payload_hash,
        policy_version=binding.policy_version,
        context_digest=binding.context_digest,
        assessment_id=binding.assessment_id,
    )
    request = ReviewDecisionRequest(
        decision=ReviewDecision.APPROVE,
        note="Staff verified group-size exception.",
        expected=expected,
        waived_exception_codes=frozenset({"GROUP_SIZE"}),
    )
    return review, action, reviewer, request


def fail_code(code, **inputs):
    with pytest.raises(ReviewError) as exc:
        resolve_review(**inputs)
    assert exc.value.code == code


def call(scenario, *, now=NOW):
    review, action, actor, request = scenario
    return resolve_review(review=review, action=action, actor=actor, request=request, now=now)


def test_T02_pending_review_cannot_execute_without_decision(scenario):
    review, action, *_ = scenario
    assert review.status is ReviewStatus.PENDING
    assert action.state == "AWAITING_REVIEW"
    assert review.decided_at is None
    # This domain module does not expose or call a connector.


def test_T02_valid_approval_returns_transition_only(scenario):
    review, action, actor, request = scenario
    result = call(scenario)
    assert result.review.status is ReviewStatus.APPROVED
    assert result.review.reviewer_id == actor.staff_user_id
    assert result.review.note == request.note
    assert result.review.waived_exception_codes == frozenset({"GROUP_SIZE"})
    assert result.next_action_state == "APPROVED"
    assert not result.replay
    assert review.status is ReviewStatus.PENDING  # No in-memory mutation.
    assert action.state == "AWAITING_REVIEW"  # No dispatch occurs here.


def test_rejection_requires_note_and_denies(scenario):
    review, action, actor, request = scenario
    request = replace(request, decision=ReviewDecision.REJECT, waived_exception_codes=frozenset())
    result = resolve_review(review=review, action=action, actor=actor, request=request, now=NOW)
    assert result.review.status is ReviewStatus.REJECTED
    assert result.next_action_state == "DENIED"


@pytest.mark.parametrize("bad_note", ["", "   ", "x" * 1001])
def test_both_decisions_require_real_notes(scenario, bad_note):
    review, action, actor, request = scenario
    fail_code(ReviewErrorCode.INVALID_NOTE, review=review, action=action,
              actor=actor, request=replace(request, note=bad_note), now=NOW)


def test_notes_are_normalized_on_write(scenario):
    review, action, actor, request = scenario
    result = resolve_review(review=review, action=action, actor=actor,
                            request=replace(request, note="  Verified.  "), now=NOW)
    assert result.review.note == "Verified."


def test_T15_stale_payload_hash_is_rejected(scenario):
    review, action, actor, request = scenario
    fail_code(ReviewErrorCode.STALE, review=review,
              action=replace(action, payload_hash="sha256:changed"),
              actor=actor, request=request, now=NOW)


def test_T15_wrong_expected_version_is_rejected(scenario):
    review, action, actor, request = scenario
    changed = replace(request, expected=replace(request.expected, action_version=1))
    fail_code(ReviewErrorCode.STALE, review=review, action=action,
              actor=actor, request=changed, now=NOW)


def test_changed_assessment_or_context_is_stale(scenario):
    review, action, actor, request = scenario
    for changed_action in (
        replace(action, context_digest="different"),
        replace(action, assessment_id=uuid4()),
    ):
        fail_code(ReviewErrorCode.STALE, review=review, action=changed_action,
                  actor=actor, request=request, now=NOW)


def test_T16_policy_changed_before_approval(scenario):
    review, action, actor, request = scenario
    fail_code(ReviewErrorCode.STALE, review=review,
              action=replace(action, current_policy_version=5),
              actor=actor, request=request, now=NOW)


def test_T17_identical_retry_is_idempotent_and_no_new_transition(scenario):
    review, action, actor, request = scenario
    committed = call(scenario).review
    result = resolve_review(review=committed, action=replace(action, state="APPROVED"),
                            actor=actor, request=request, now=NOW + timedelta(seconds=3))
    assert result.replay is True
    assert result.review is committed
    assert result.next_action_state == "APPROVED"


def test_T17_conflicting_second_decision_is_rejected(scenario):
    review, action, actor, request = scenario
    committed = call(scenario).review
    competing = replace(request, decision=ReviewDecision.REJECT, waived_exception_codes=frozenset())
    fail_code(ReviewErrorCode.CONFLICT, review=committed,
              action=replace(action, state="APPROVED"), actor=actor,
              request=competing, now=NOW + timedelta(seconds=2))


def test_retry_by_a_different_reviewer_is_not_same_decision(scenario):
    review, action, actor, request = scenario
    committed = call(scenario).review
    fail_code(ReviewErrorCode.CONFLICT, review=committed,
              action=replace(action, state="APPROVED"),
              actor=replace(actor, staff_user_id=uuid4()),
              request=request, now=NOW + timedelta(seconds=2))


def test_T18_expiry_enforced_before_cleanup(scenario):
    review, action, actor, request = scenario
    fail_code(ReviewErrorCode.EXPIRED, review=review, action=action, actor=actor,
              request=request, now=review.expires_at)
    result = expire_pending_review(review=review, action=action, now=review.expires_at)
    assert result.review.status is ReviewStatus.EXPIRED
    assert result.next_action_state == "EXPIRED"
    fail_code(ReviewErrorCode.EXPIRED, review=result.review, action=action,
              actor=actor, request=request, now=review.expires_at)


def test_action_expiry_blocks_approval_independently(scenario):
    review, action, actor, request = scenario
    fail_code(ReviewErrorCode.EXPIRED, review=review,
              action=replace(action, expires_at=NOW),
              actor=actor, request=request, now=NOW)


def test_T30_cross_tenant_not_found(scenario):
    review, action, actor, request = scenario
    fail_code(ReviewErrorCode.NOT_FOUND, review=review, action=action,
              actor=replace(actor, business_id=uuid4()), request=request, now=NOW)
    fail_code(ReviewErrorCode.NOT_FOUND, review=review,
              action=replace(action, business_id=uuid4()),
              actor=actor, request=request, now=NOW)


@pytest.mark.parametrize("change,code", [
    ({"role": "customer"}, ReviewErrorCode.FORBIDDEN),
    ({"role": "viewer"}, ReviewErrorCode.FORBIDDEN),
    ({"session_valid": False}, ReviewErrorCode.UNAUTHENTICATED),
    ({"enabled": False}, ReviewErrorCode.UNAUTHENTICATED),
    ({"csrf_valid": False}, ReviewErrorCode.CSRF_INVALID),
])
def test_T30_authentication_roles_and_csrf(scenario, change, code):
    review, action, actor, request = scenario
    fail_code(code, review=review, action=action, actor=replace(actor, **change),
              request=request, now=NOW)


def test_missing_confirmation_is_not_waivable(scenario):
    review, action, actor, request = scenario
    fail_code(ReviewErrorCode.MISSING_CONFIRMATION, review=review,
              action=replace(action, customer_confirmed=False),
              actor=actor, request=request, now=NOW)


@pytest.mark.parametrize("change", [
    {"hard_denial_codes": frozenset({"INVALID_SLOT"})},
    {"capacity_available": False},
])
def test_hard_denial_and_capacity_cannot_be_waived(scenario, change):
    review, action, actor, request = scenario
    fail_code(ReviewErrorCode.HARD_DENIAL, review=review,
              action=replace(action, **change), actor=actor, request=request, now=NOW)


def test_unknown_or_unrequired_exception_cannot_be_waived(scenario):
    review, action, actor, request = scenario
    for code in ("OVERRIDE_CAPACITY", "SHORT_NOTICE"):
        fail_code(ReviewErrorCode.INVALID_EXCEPTION if code == "OVERRIDE_CAPACITY"
                  else ReviewErrorCode.INCOMPLETE_EXCEPTION,
                  review=review, action=action, actor=actor,
                  request=replace(request, waived_exception_codes=frozenset({code})),
                  now=NOW)


def test_all_required_exception_codes_must_be_waived(scenario):
    review, action, actor, request = scenario
    review = replace(review, required_exception_codes=frozenset({"GROUP_SIZE", "SHORT_NOTICE"}))
    fail_code(ReviewErrorCode.INCOMPLETE_EXCEPTION, review=review,
              action=action, actor=actor, request=request, now=NOW)
    allowed = replace(request, waived_exception_codes=frozenset({"GROUP_SIZE", "SHORT_NOTICE"}))
    assert resolve_review(review=review, action=action, actor=actor,
                          request=allowed, now=NOW).review.status is ReviewStatus.APPROVED


def test_approval_of_unchanged_contextual_jev_concern(scenario):
    review, action, actor, request = scenario
    review = replace(review, required_exception_codes=frozenset())
    request = replace(request, waived_exception_codes=frozenset())
    result = resolve_review(review=review, action=action, actor=actor, request=request, now=NOW)
    # Its exact assessment_id/context_digest remains attached to the recorded approval.
    assert result.review.binding.assessment_id == action.assessment_id
    assert result.review.binding.context_digest == action.context_digest
    assert result.review.status is ReviewStatus.APPROVED


def test_paused_business_can_record_approval_but_never_dispatch_here(scenario):
    review, action, actor, request = scenario
    result = resolve_review(review=review, action=replace(action, automation_paused=True),
                            actor=actor, request=request, now=NOW)
    assert result.review.status is ReviewStatus.APPROVED
    assert result.next_action_state == "APPROVED"
    # Future worker must recheck pause and refuse any new dispatch claim.


def test_closed_session_cannot_approve(scenario):
    review, action, actor, request = scenario
    fail_code(ReviewErrorCode.SESSION_CLOSED, review=review,
              action=replace(action, session_active=False),
              actor=actor, request=request, now=NOW)


def test_T34_unanswered_cancellation_creates_non_executable_followup(scenario):
    review, action, *_ = scenario
    cancelled = replace(action, state="CANCELLED", session_active=False)
    followup = make_non_executable_follow_up(
        review=review, action=cancelled, reason=FollowUpReason.REVIEW_UNANSWERED,
    )
    assert followup.executable is False
    assert followup.review_id == review.review_id
    assert followup.action_id == action.action_id
    assert followup.conversation_id == action.conversation_id


def test_follow_up_is_not_allowed_for_pending_executable_action(scenario):
    review, action, *_ = scenario
    with pytest.raises(ReviewError) as exc:
        make_non_executable_follow_up(
            review=review, action=action, reason=FollowUpReason.REVIEW_UNANSWERED,
        )
    assert exc.value.code == ReviewErrorCode.INVALID_STATE


def test_utc_timestamps_must_have_timezones(scenario):
    review, action, actor, request = scenario
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_review(review=review, action=action, actor=actor,
                       request=request, now=datetime(2026, 10, 10, 17, 0))
