"""SSA-012: pure human-review contracts and decision rules.

No database I/O, provider calls, or connector dispatch happens in this module.
Inputs named ``Trusted*`` MUST be constructed from authenticated server-side
context, never bound directly from a customer's or reviewer's JSON input.

These functions are NOT an atomic approval service: SSA-012's future service /
repository must lock the relevant records, reload trusted state, call these
functions, and persist the action, review and audit changes in ONE transaction.
The SSA-013 worker must independently revalidate before booking dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID


BOOKING_EXCEPTION_CODES = frozenset({"GROUP_SIZE", "SHORT_NOTICE"})
REVIEWER_ROLES = frozenset({"reviewer", "owner"})


class ReviewStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ReviewDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class FollowUpReason(str, Enum):
    REVIEW_UNANSWERED = "REVIEW_UNANSWERED"
    SESSION_ENDED = "SESSION_ENDED"


class ReviewErrorCode(str, Enum):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"  # Deliberately hides cross-tenant existence.
    CSRF_INVALID = "CSRF_INVALID"
    INVALID_NOTE = "INVALID_NOTE"
    INVALID_EXCEPTION = "INVALID_EXCEPTION"
    INCOMPLETE_EXCEPTION = "INCOMPLETE_EXCEPTION"
    STALE = "STALE"
    CONFLICT = "CONFLICT"
    EXPIRED = "EXPIRED"
    MISSING_CONFIRMATION = "MISSING_CONFIRMATION"
    HARD_DENIAL = "HARD_DENIAL"
    SESSION_CLOSED = "SESSION_CLOSED"
    INVALID_STATE = "INVALID_STATE"


class ReviewError(ValueError):
    """Safe, machine-readable failure. API maps these to 401/403/404/409/422."""

    def __init__(self, code: ReviewErrorCode):
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True)
class ReviewBinding:
    """Server-persisted identity of the exact assessment being reviewed."""

    business_id: UUID
    action_id: UUID
    action_version: int
    payload_hash: str
    policy_version: int
    context_digest: str
    assessment_id: UUID


@dataclass(frozen=True)
class ExpectedReviewBinding:
    """Untrusted client's optimistic expectations; never grant authority alone."""

    action_id: UUID
    action_version: int
    payload_hash: str
    policy_version: int
    context_digest: str
    assessment_id: UUID


@dataclass(frozen=True)
class ReviewRecord:
    review_id: UUID
    binding: ReviewBinding
    expires_at: datetime
    required_exception_codes: frozenset[str] = field(default_factory=frozenset)
    allowed_exception_codes: frozenset[str] = field(default_factory=frozenset)
    status: ReviewStatus = ReviewStatus.PENDING
    reviewer_id: UUID | None = None
    note: str | None = None
    waived_exception_codes: frozenset[str] = field(default_factory=frozenset)
    decided_at: datetime | None = None


@dataclass(frozen=True)
class TrustedReviewerContext:
    """Construct ONLY after authenticated session, role and CSRF validation."""

    staff_user_id: UUID
    business_id: UUID
    role: str
    session_valid: bool
    enabled: bool
    csrf_valid: bool


@dataclass(frozen=True)
class TrustedActionSnapshot:
    """Load under the decision transaction, not from a caller or model.

    ``current_policy_version`` is the business's current policy, not merely
    the historical action policy. ``capacity_available`` is trusted booking
    evidence; dispatch rechecks it again under the worker's rules.
    """

    business_id: UUID
    conversation_id: UUID
    action_id: UUID
    action_version: int
    payload_hash: str
    policy_version: int
    context_digest: str
    assessment_id: UUID
    state: str
    current_policy_version: int
    customer_confirmed: bool
    capacity_available: bool
    hard_denial_codes: frozenset[str]
    session_active: bool
    expires_at: datetime
    automation_paused: bool = False


@dataclass(frozen=True)
class ReviewDecisionRequest:
    decision: ReviewDecision
    note: str
    expected: ExpectedReviewBinding
    waived_exception_codes: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ReviewResolution:
    """A proposed persisted transition, NOT an execution authorization.

    The review service applies this result atomically with the action and audit.
    Even a successful APPROVE does not dispatch a booking; the SSA-013 worker
    must independently check confirmation, policy, expiry, pause, and capacity.
    """

    review: ReviewRecord
    next_action_state: str
    replay: bool = False


@dataclass(frozen=True)
class NonExecutableFollowUp:
    """Staff-only context record; deliberately has no execute/approve token."""

    business_id: UUID
    conversation_id: UUID
    action_id: UUID
    action_version: int
    review_id: UUID
    reason: FollowUpReason
    executable: bool = field(default=False, init=False)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("All review and action timestamps must be timezone-aware")
    return dt.astimezone(timezone.utc)


def _check_access(actor: TrustedReviewerContext, review: ReviewRecord,
                  action: TrustedActionSnapshot) -> None:
    if not actor.session_valid or not actor.enabled:
        raise ReviewError(ReviewErrorCode.UNAUTHENTICATED)
    if actor.role not in REVIEWER_ROLES:
        raise ReviewError(ReviewErrorCode.FORBIDDEN)
    if (actor.business_id != review.binding.business_id
            or action.business_id != review.binding.business_id):
        raise ReviewError(ReviewErrorCode.NOT_FOUND)
    if not actor.csrf_valid:
        raise ReviewError(ReviewErrorCode.CSRF_INVALID)


def _matches_binding(expected: ExpectedReviewBinding,
                     binding: ReviewBinding) -> bool:
    return (
        expected.action_id == binding.action_id
        and expected.action_version == binding.action_version
        and expected.payload_hash == binding.payload_hash
        and expected.policy_version == binding.policy_version
        and expected.context_digest == binding.context_digest
        and expected.assessment_id == binding.assessment_id
    )


def _current_action_matches(action: TrustedActionSnapshot,
                            binding: ReviewBinding) -> bool:
    return (
        action.action_id == binding.action_id
        and action.action_version == binding.action_version
        and action.payload_hash == binding.payload_hash
        and action.policy_version == binding.policy_version
        and action.current_policy_version == binding.policy_version
        and action.context_digest == binding.context_digest
        and action.assessment_id == binding.assessment_id
    )


def resolve_review(*, review: ReviewRecord, action: TrustedActionSnapshot,
                   actor: TrustedReviewerContext, request: ReviewDecisionRequest,
                   now: datetime) -> ReviewResolution:
    """Validate and describe one decision, with no side effects.

    Called ONLY inside the future repository's row-locked transaction. The
    returned transition is a proposal to commit, not permission to call a
    booking connector. Identical retry is recognized for the same reviewer,
    decision, normalized note, and exception codes.
    """
    now = _utc(now)
    _check_access(actor, review, action)

    if not isinstance(request.decision, ReviewDecision):
        raise ReviewError(ReviewErrorCode.INVALID_STATE)
    if not _matches_binding(request.expected, review.binding):
        raise ReviewError(ReviewErrorCode.STALE)
    if not _current_action_matches(action, review.binding):
        raise ReviewError(ReviewErrorCode.STALE)

    note = request.note.strip() if isinstance(request.note, str) else ""
    if not note or len(note) > 1000:
        raise ReviewError(ReviewErrorCode.INVALID_NOTE)

    # An already-committed decision may be retried. It never authorizes a
    # second state transition or a connector call. Different decisions conflict.
    if review.status in (ReviewStatus.APPROVED, ReviewStatus.REJECTED):
        same = (
            review.reviewer_id == actor.staff_user_id
            and review.status == (ReviewStatus.APPROVED
                                  if request.decision == ReviewDecision.APPROVE
                                  else ReviewStatus.REJECTED)
            and review.note == note
            and review.waived_exception_codes == request.waived_exception_codes
        )
        if same:
            return ReviewResolution(
                review=review,
                next_action_state=("APPROVED" if review.status == ReviewStatus.APPROVED
                                   else "DENIED"),
                replay=True,
            )
        raise ReviewError(ReviewErrorCode.CONFLICT)

    if review.status == ReviewStatus.EXPIRED or now >= _utc(review.expires_at):
        raise ReviewError(ReviewErrorCode.EXPIRED)
    if review.status != ReviewStatus.PENDING or action.state != "AWAITING_REVIEW":
        raise ReviewError(ReviewErrorCode.STALE)
    if now >= _utc(action.expires_at):
        raise ReviewError(ReviewErrorCode.EXPIRED)
    if not action.session_active:
        raise ReviewError(ReviewErrorCode.SESSION_CLOSED)

    if request.decision == ReviewDecision.APPROVE:
        if not action.customer_confirmed:
            raise ReviewError(ReviewErrorCode.MISSING_CONFIRMATION)
        if action.hard_denial_codes or not action.capacity_available:
            raise ReviewError(ReviewErrorCode.HARD_DENIAL)
        if (not request.waived_exception_codes <= BOOKING_EXCEPTION_CODES
                or not request.waived_exception_codes <= review.allowed_exception_codes
                or not review.required_exception_codes <= review.allowed_exception_codes):
            raise ReviewError(ReviewErrorCode.INVALID_EXCEPTION)
        # A partially waived booking cannot become fully approved.
        if request.waived_exception_codes != review.required_exception_codes:
            raise ReviewError(ReviewErrorCode.INCOMPLETE_EXCEPTION)
        status, next_state = ReviewStatus.APPROVED, "APPROVED"
    else:
        if request.waived_exception_codes:
            raise ReviewError(ReviewErrorCode.INVALID_EXCEPTION)
        status, next_state = ReviewStatus.REJECTED, "DENIED"

    decided = replace(
        review,
        status=status,
        reviewer_id=actor.staff_user_id,
        note=note,
        waived_exception_codes=request.waived_exception_codes,
        decided_at=now,
    )
    # A pause is NOT a force-approval shortcut: a worker must refuse to claim
    # the action until unpaused and ALL current requirements pass again.
    return ReviewResolution(review=decided, next_action_state=next_state)


def expire_pending_review(*, review: ReviewRecord,
                          action: TrustedActionSnapshot,
                          now: datetime) -> ReviewResolution:
    """Pure expiry transition; caller persists under the same row locks."""
    if review.binding.business_id != action.business_id or not _current_action_matches(action, review.binding):
        raise ReviewError(ReviewErrorCode.STALE)
    if review.status != ReviewStatus.PENDING or action.state != "AWAITING_REVIEW":
        raise ReviewError(ReviewErrorCode.INVALID_STATE)
    if _utc(now) < _utc(review.expires_at) and _utc(now) < _utc(action.expires_at):
        raise ReviewError(ReviewErrorCode.INVALID_STATE)
    return ReviewResolution(
        review=replace(review, status=ReviewStatus.EXPIRED),
        next_action_state="EXPIRED",
    )


def make_non_executable_follow_up(*, review: ReviewRecord,
                                  action: TrustedActionSnapshot,
                                  reason: FollowUpReason) -> NonExecutableFollowUp:
    """Create staff follow-up context AFTER cancellation or expiry.

    The future service must commit the cancellation/expiry and follow-up
    together; a follow-up is never a delayed approval or dispatch claim.
    """
    if (review.binding.business_id != action.business_id
            or review.binding.action_id != action.action_id
            or review.binding.action_version != action.action_version):
        raise ReviewError(ReviewErrorCode.STALE)
    if action.state not in ("CANCELLED", "EXPIRED"):
        raise ReviewError(ReviewErrorCode.INVALID_STATE)
    if reason not in (FollowUpReason.REVIEW_UNANSWERED, FollowUpReason.SESSION_ENDED):
        raise ReviewError(ReviewErrorCode.INVALID_STATE)
    return NonExecutableFollowUp(
        business_id=review.binding.business_id,
        conversation_id=action.conversation_id,
        action_id=action.action_id,
        action_version=action.action_version,
        review_id=review.review_id,
        reason=reason,
    )
