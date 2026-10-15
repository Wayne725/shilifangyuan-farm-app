from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import get_current_user, require_active_member, require_admin
from ..database import get_session
from ..models import (
    Activity,
    ActivityRegistration,
    ActivityRegistrationStatus,
    ActivityStatus,
    AdminAudit,
    MemberProposal,
    MemberProposalComment,
    MemberProposalStatus,
    MemberProposalType,
    MemberProposalVote,
    Notification,
    OutboxEvent,
    PointAccount,
    PointSourceType,
    PointTransaction,
    ProposalOption,
    User,
)
from ..schemas import (
    AdminActivityRegistrationRead,
    ActivityCreate,
    ActivityRead,
    ActivityRegistrationRead,
    ActivityReview,
    MemberProposalCommentCreate,
    MemberProposalCommentRead,
    MemberProposalCreate,
    MemberProposalNamedVoteRead,
    MemberProposalRead,
    MemberProposalReview,
    MemberProposalTally,
    MemberProposalVoteUpsert,
    ProposalOptionRead,
)
from ..v2_domain import (
    apply_member_proposal_clock,
    next_activity_registration_status,
    promote_activity_waitlist,
    tally_member_votes,
)


community_router = APIRouter(tags=["community"])


async def _award_points_once(
    session: AsyncSession,
    user_id: str,
    amount: int,
    source_type: PointSourceType,
    reference_id: str,
    note: str,
) -> None:
    account = await session.scalar(select(PointAccount).where(PointAccount.user_id == user_id))
    if account is None:
        account = PointAccount(user_id=user_id)
        session.add(account)
        await session.flush()
    existing = await session.scalar(
        select(PointTransaction.id).where(
            PointTransaction.account_id == account.id,
            PointTransaction.source_type == source_type,
            PointTransaction.reference_id == reference_id,
        )
    )
    if existing is None:
        session.add(PointTransaction(account_id=account.id, amount=amount, source_type=source_type, reference_id=reference_id, note=note))


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _activity_read(
    activity: Activity,
    user_id: str,
) -> ActivityRead:
    registered = [
        registration
        for registration in activity.registrations
        if registration.status
        in {
            ActivityRegistrationStatus.REGISTERED,
            ActivityRegistrationStatus.ATTENDED,
            ActivityRegistrationStatus.NO_SHOW,
        }
    ]
    waitlisted = [
        registration
        for registration in activity.registrations
        if registration.status == ActivityRegistrationStatus.WAITLISTED
    ]
    mine = next(
        (
            registration
            for registration in activity.registrations
            if registration.user_id == user_id
        ),
        None,
    )
    return ActivityRead(
        id=activity.id,
        created_by_id=activity.created_by_id,
        title=activity.title,
        description=activity.description,
        image_url=activity.image_url,
        location=activity.location,
        starts_at=activity.starts_at,
        ends_at=activity.ends_at,
        registration_deadline=activity.registration_deadline,
        capacity=activity.capacity,
        waitlist_enabled=activity.waitlist_enabled,
        status=activity.status,
        reviewed_at=activity.reviewed_at,
        review_reason=activity.review_reason,
        registration_count=len(registered),
        waitlist_count=len(waitlisted),
        my_registration=(
            ActivityRegistrationRead.model_validate(mine)
            if mine is not None
            else None
        ),
    )


def _proposal_read(
    proposal: MemberProposal,
    user_id: str,
) -> MemberProposalRead:
    tally = tally_member_votes(proposal.votes, proposal.minimum_voters)
    my_vote_record = next((vote for vote in proposal.votes if vote.user_id == user_id), None)
    return MemberProposalRead(
        id=proposal.id,
        created_by_id=proposal.created_by_id,
        created_by_name=proposal.created_by.display_name,
        title=proposal.title,
        body=proposal.body,
        proposal_type=proposal.proposal_type,
        options=[
            ProposalOptionRead(
                id=option.id,
                label=option.label,
                position=option.position,
                vote_count=sum(vote.option_id == option.id for vote in proposal.votes),
            )
            for option in sorted(proposal.options, key=lambda item: item.position)
        ],
        status=proposal.status,
        minimum_voters=proposal.minimum_voters,
        discussion_ends_at=proposal.discussion_ends_at,
        voting_ends_at=proposal.voting_ends_at,
        review_reason=proposal.review_reason,
        result_summary=proposal.result_summary,
        tally=MemberProposalTally(
            yes=tally.yes,
            no=tally.no,
            abstain=tally.abstain,
            total=tally.total,
        ),
        my_vote=my_vote_record.choice if my_vote_record else None,
        my_option_id=my_vote_record.option_id if my_vote_record else None,
        created_at=proposal.created_at,
    )


async def _load_activity(
    session: AsyncSession,
    activity_id: str,
    *,
    for_update: bool = False,
) -> Optional[Activity]:
    query = (
        select(Activity)
        .where(Activity.id == activity_id)
        .options(
            selectinload(Activity.registrations).selectinload(
                ActivityRegistration.user
            )
        )
    )
    if for_update:
        query = query.with_for_update()
    return await session.scalar(query)


async def _load_proposal(
    session: AsyncSession,
    proposal_id: str,
    *,
    for_update: bool = False,
) -> Optional[MemberProposal]:
    query = (
        select(MemberProposal)
        .where(MemberProposal.id == proposal_id)
        .options(
            selectinload(MemberProposal.votes),
            selectinload(MemberProposal.comments),
            selectinload(MemberProposal.options),
            selectinload(MemberProposal.created_by),
        )
    )
    if for_update:
        query = query.with_for_update()
    return await session.scalar(query)


@community_router.get("/v1/activities", response_model=list[ActivityRead])
async def list_activities(
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> list[ActivityRead]:
    activities = (
        await session.scalars(
            select(Activity)
            .where(Activity.status == ActivityStatus.PUBLISHED)
            .options(selectinload(Activity.registrations))
            .order_by(Activity.starts_at)
        )
    ).all()
    return [_activity_read(activity, user.id) for activity in activities]


@community_router.get(
    "/v1/activities/{activity_id}",
    response_model=ActivityRead,
)
async def get_activity(
    activity_id: str,
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> ActivityRead:
    activity = await _load_activity(session, activity_id)
    if activity is None or (
        activity.status != ActivityStatus.PUBLISHED
        and activity.created_by_id != user.id
    ):
        raise HTTPException(status_code=404, detail="找不到活動")
    return _activity_read(activity, user.id)


@community_router.post(
    "/v1/activities",
    response_model=ActivityRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_activity(
    body: ActivityCreate,
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> ActivityRead:
    activity = Activity(
        created_by_id=user.id,
        status=ActivityStatus.PENDING_REVIEW,
        registrations=[],
        **body.model_dump(),
    )
    session.add(activity)
    await session.flush()
    session.add(
        OutboxEvent(
            event_type="activity.pending_review",
            aggregate_type="activity",
            aggregate_id=activity.id,
            payload={"created_by_id": user.id},
        )
    )
    await session.commit()
    return _activity_read(activity, user.id)


@community_router.post(
    "/v1/activities/{activity_id}/register",
    response_model=ActivityRegistrationRead,
)
async def register_activity(
    activity_id: str,
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> ActivityRegistrationRead:
    activity = await _load_activity(session, activity_id, for_update=True)
    if activity is None:
        raise HTTPException(status_code=404, detail="找不到活動")
    existing = next(
        (
            registration
            for registration in activity.registrations
            if registration.user_id == user.id
        ),
        None,
    )
    if existing is not None and existing.status != (
        ActivityRegistrationStatus.CANCELLED
    ):
        return ActivityRegistrationRead.model_validate(existing)
    active_count = sum(
        registration.status
        in {
            ActivityRegistrationStatus.REGISTERED,
            ActivityRegistrationStatus.ATTENDED,
            ActivityRegistrationStatus.NO_SHOW,
        }
        for registration in activity.registrations
    )
    try:
        next_status = next_activity_registration_status(
            activity,
            active_count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    queue_position = (
        max(
            (
                registration.queue_position
                for registration in activity.registrations
            ),
            default=0,
        )
        + 1
    )
    if existing is None:
        registration = ActivityRegistration(
            activity_id=activity.id,
            user_id=user.id,
            queue_position=queue_position,
            status=next_status,
        )
        session.add(registration)
    else:
        registration = existing
        registration.status = next_status
        registration.queue_position = queue_position
        registration.registered_at = datetime.now(timezone.utc)
        registration.cancelled_at = None
    await session.commit()
    return ActivityRegistrationRead.model_validate(registration)


@community_router.post(
    "/v1/activities/{activity_id}/cancel-registration",
    response_model=ActivityRegistrationRead,
)
async def cancel_activity_registration(
    activity_id: str,
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> ActivityRegistrationRead:
    activity = await _load_activity(session, activity_id, for_update=True)
    if activity is None:
        raise HTTPException(status_code=404, detail="找不到活動")
    registration = next(
        (
            item
            for item in activity.registrations
            if item.user_id == user.id
            and item.status
            in {
                ActivityRegistrationStatus.REGISTERED,
                ActivityRegistrationStatus.WAITLISTED,
            }
        ),
        None,
    )
    if registration is None:
        raise HTTPException(status_code=409, detail="目前沒有可取消的報名")
    was_registered = (
        registration.status == ActivityRegistrationStatus.REGISTERED
    )
    registration.status = ActivityRegistrationStatus.CANCELLED
    registration.cancelled_at = datetime.now(timezone.utc)
    if was_registered:
        promoted = promote_activity_waitlist(activity.registrations)
        if promoted is not None:
            session.add(
                Notification(
                    user_id=promoted.user_id,
                    event_type="activity.waitlist_promoted",
                    title="活動候補遞補成功",
                    body=f"你已遞補為「{activity.title}」正式名額",
                    data={"activity_id": activity.id},
                )
            )
    await session.commit()
    return ActivityRegistrationRead.model_validate(registration)


@community_router.get(
    "/v1/admin/activities",
    response_model=list[ActivityRead],
)
async def admin_list_activities(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[ActivityRead]:
    activities = (
        await session.scalars(
            select(Activity)
            .options(selectinload(Activity.registrations))
            .order_by(Activity.created_at.desc())
        )
    ).all()
    return [_activity_read(activity, admin.id) for activity in activities]


@community_router.get(
    "/v1/admin/activities/{activity_id}/registrations",
    response_model=list[AdminActivityRegistrationRead],
)
async def admin_list_activity_registrations(
    activity_id: str,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[AdminActivityRegistrationRead]:
    activity = await _load_activity(session, activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="找不到活動")
    return [
        AdminActivityRegistrationRead(
            **ActivityRegistrationRead.model_validate(
                registration
            ).model_dump(),
            display_name=registration.user.display_name,
            email=registration.user.email,
        )
        for registration in sorted(
            activity.registrations,
            key=lambda item: (item.queue_position, item.registered_at),
        )
    ]


async def _review_activity(
    activity_id: str,
    body: ActivityReview,
    next_status: ActivityStatus,
    admin: User,
    session: AsyncSession,
) -> ActivityRead:
    activity = await _load_activity(session, activity_id, for_update=True)
    if (
        activity is None
        or activity.status != ActivityStatus.PENDING_REVIEW
    ):
        raise HTTPException(status_code=409, detail="此活動目前不可審核")
    if next_status == ActivityStatus.REJECTED and not body.reason:
        raise HTTPException(status_code=422, detail="請填寫駁回原因")
    activity.status = next_status
    activity.reviewed_by_id = admin.id
    activity.reviewed_at = datetime.now(timezone.utc)
    activity.review_reason = body.reason
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action=f"activity.{next_status.value}",
            aggregate_type="activity",
            aggregate_id=activity.id,
            reason=body.reason,
        )
    )
    await session.commit()
    return _activity_read(activity, admin.id)


@community_router.post(
    "/v1/admin/activities/{activity_id}/approve",
    response_model=ActivityRead,
)
async def approve_activity(
    activity_id: str,
    body: ActivityReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ActivityRead:
    return await _review_activity(
        activity_id,
        body,
        ActivityStatus.PUBLISHED,
        admin,
        session,
    )


@community_router.post(
    "/v1/admin/activities/{activity_id}/reject",
    response_model=ActivityRead,
)
async def reject_activity(
    activity_id: str,
    body: ActivityReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ActivityRead:
    return await _review_activity(
        activity_id,
        body,
        ActivityStatus.REJECTED,
        admin,
        session,
    )


@community_router.post(
    "/v1/admin/activities/{activity_id}/cancel",
    response_model=ActivityRead,
)
async def cancel_activity(
    activity_id: str,
    body: ActivityReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ActivityRead:
    if not body.reason:
        raise HTTPException(status_code=422, detail="請填寫取消原因")
    activity = await _load_activity(session, activity_id, for_update=True)
    if activity is None or activity.status in {
        ActivityStatus.CANCELLED,
        ActivityStatus.COMPLETED,
    }:
        raise HTTPException(status_code=409, detail="此活動目前不可取消")
    activity.status = ActivityStatus.CANCELLED
    activity.cancelled_at = datetime.now(timezone.utc)
    for registration in activity.registrations:
        if registration.status in {
            ActivityRegistrationStatus.REGISTERED,
            ActivityRegistrationStatus.WAITLISTED,
        }:
            registration.status = ActivityRegistrationStatus.CANCELLED
            registration.cancelled_at = datetime.now(timezone.utc)
            session.add(
                Notification(
                    user_id=registration.user_id,
                    event_type="activity.cancelled",
                    title="活動已取消",
                    body=f"「{activity.title}」已取消：{body.reason}",
                    data={"activity_id": activity.id},
                )
            )
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="activity.cancel",
            aggregate_type="activity",
            aggregate_id=activity.id,
            reason=body.reason,
        )
    )
    await session.commit()
    return _activity_read(activity, admin.id)


@community_router.post(
    "/v1/admin/activities/{activity_id}/complete",
    response_model=ActivityRead,
)
async def complete_activity(
    activity_id: str,
    body: ActivityReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ActivityRead:
    activity = await _load_activity(session, activity_id, for_update=True)
    now = datetime.now(timezone.utc)
    if activity is None or activity.status != ActivityStatus.PUBLISHED:
        raise HTTPException(status_code=409, detail="此活動目前不可結案")
    if _aware(activity.ends_at) > now:
        raise HTTPException(status_code=409, detail="活動結束時間後才能結案")
    activity.status = ActivityStatus.COMPLETED
    for registration in activity.registrations:
        if registration.status == ActivityRegistrationStatus.REGISTERED:
            registration.status = ActivityRegistrationStatus.NO_SHOW
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="activity.complete",
            aggregate_type="activity",
            aggregate_id=activity.id,
            reason=body.reason,
        )
    )
    await session.commit()
    return _activity_read(activity, admin.id)


@community_router.post(
    "/v1/admin/activities/{activity_id}/registrations/"
    "{registration_id}/{attendance_status}",
    response_model=ActivityRegistrationRead,
)
async def mark_activity_attendance(
    activity_id: str,
    registration_id: str,
    attendance_status: ActivityRegistrationStatus,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ActivityRegistrationRead:
    if attendance_status not in {
        ActivityRegistrationStatus.ATTENDED,
        ActivityRegistrationStatus.NO_SHOW,
    }:
        raise HTTPException(status_code=422, detail="無效的出席狀態")
    registration = await session.scalar(
        select(ActivityRegistration)
        .where(
            ActivityRegistration.id == registration_id,
            ActivityRegistration.activity_id == activity_id,
            ActivityRegistration.status
            == ActivityRegistrationStatus.REGISTERED,
        )
        .with_for_update()
    )
    if registration is None:
        raise HTTPException(status_code=409, detail="此報名無法標記出席")
    registration.status = attendance_status
    if attendance_status == ActivityRegistrationStatus.ATTENDED:
        registration.checked_in_at = datetime.now(timezone.utc)
        await _award_points_once(session, registration.user_id, 5, PointSourceType.ACTIVITY, activity_id, "參與社員活動")
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action=f"activity.{attendance_status.value}",
            aggregate_type="activity_registration",
            aggregate_id=registration.id,
        )
    )
    await session.commit()
    return ActivityRegistrationRead.model_validate(registration)


@community_router.get(
    "/v1/member-proposals",
    response_model=list[MemberProposalRead],
)
async def list_member_proposals(
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> list[MemberProposalRead]:
    proposals = (
        await session.scalars(
            select(MemberProposal)
            .where(
                MemberProposal.status.notin_(
                    {
                        MemberProposalStatus.DRAFT,
                        MemberProposalStatus.PENDING_REVIEW,
                        MemberProposalStatus.WITHDRAWN,
                    }
                )
                | (MemberProposal.created_by_id == user.id)
            )
            .options(selectinload(MemberProposal.votes))
            .options(selectinload(MemberProposal.options))
            .options(selectinload(MemberProposal.created_by))
            .order_by(MemberProposal.created_at.desc())
        )
    ).all()
    changed = False
    for proposal in proposals:
        changed = (
            apply_member_proposal_clock(proposal, proposal.votes) or changed
        )
    if changed:
        await session.commit()
    return [_proposal_read(proposal, user.id) for proposal in proposals]


@community_router.get(
    "/v1/member-proposals/{proposal_id}",
    response_model=MemberProposalRead,
)
async def get_member_proposal(
    proposal_id: str,
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> MemberProposalRead:
    proposal = await _load_proposal(session, proposal_id)
    if proposal is None or (
        proposal.status
        in {
            MemberProposalStatus.DRAFT,
            MemberProposalStatus.PENDING_REVIEW,
            MemberProposalStatus.WITHDRAWN,
        }
        and proposal.created_by_id != user.id
    ):
        raise HTTPException(status_code=404, detail="找不到社員提案")
    if apply_member_proposal_clock(proposal, proposal.votes):
        await session.commit()
    return _proposal_read(proposal, user.id)


@community_router.post(
    "/v1/member-proposals",
    response_model=MemberProposalRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_member_proposal(
    body: MemberProposalCreate,
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> MemberProposalRead:
    proposal = MemberProposal(
        created_by_id=user.id,
        created_by=user,
        title=body.title,
        body=body.body,
        status=MemberProposalStatus.DRAFT,
        minimum_voters=10,
        proposal_type=body.proposal_type,
        votes=[],
        comments=[],
        options=[ProposalOption(label=item.label.strip(), position=position) for position, item in enumerate(body.options)],
    )
    session.add(proposal)
    await session.commit()
    return _proposal_read(proposal, user.id)


@community_router.post(
    "/v1/member-proposals/{proposal_id}/submit",
    response_model=MemberProposalRead,
)
async def submit_member_proposal(
    proposal_id: str,
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> MemberProposalRead:
    proposal = await _load_proposal(session, proposal_id, for_update=True)
    if (
        proposal is None
        or proposal.created_by_id != user.id
        or proposal.status != MemberProposalStatus.DRAFT
    ):
        raise HTTPException(status_code=409, detail="此提案目前不可送審")
    proposal.status = MemberProposalStatus.PENDING_REVIEW
    session.add(
        OutboxEvent(
            event_type="member_proposal.pending_review",
            aggregate_type="member_proposal",
            aggregate_id=proposal.id,
            payload={"created_by_id": user.id},
        )
    )
    await session.commit()
    return _proposal_read(proposal, user.id)


@community_router.post(
    "/v1/member-proposals/{proposal_id}/withdraw",
    response_model=MemberProposalRead,
)
async def withdraw_member_proposal(
    proposal_id: str,
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> MemberProposalRead:
    proposal = await _load_proposal(session, proposal_id, for_update=True)
    if (
        proposal is None
        or proposal.created_by_id != user.id
        or proposal.status
        not in {
            MemberProposalStatus.DRAFT,
            MemberProposalStatus.PENDING_REVIEW,
            MemberProposalStatus.DISCUSSION,
        }
    ):
        raise HTTPException(status_code=409, detail="此提案目前不可撤回")
    proposal.status = MemberProposalStatus.WITHDRAWN
    proposal.closed_at = datetime.now(timezone.utc)
    await session.commit()
    return _proposal_read(proposal, user.id)


@community_router.get(
    "/v1/member-proposals/{proposal_id}/comments",
    response_model=list[MemberProposalCommentRead],
)
async def list_member_proposal_comments(
    proposal_id: str,
    _user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> list[MemberProposalCommentRead]:
    comments = (
        await session.execute(
            select(MemberProposalComment, User)
            .join(User, User.id == MemberProposalComment.user_id)
            .where(
                MemberProposalComment.proposal_id == proposal_id,
                MemberProposalComment.deleted_at.is_(None),
            )
            .order_by(MemberProposalComment.created_at)
        )
    ).all()
    return [
        MemberProposalCommentRead(
            id=comment.id,
            user_id=comment.user_id,
            display_name=author.display_name,
            body=comment.body,
            created_at=comment.created_at,
            updated_at=comment.updated_at,
        )
        for comment, author in comments
    ]


@community_router.post(
    "/v1/member-proposals/{proposal_id}/comments",
    response_model=MemberProposalCommentRead,
    status_code=status.HTTP_201_CREATED,
)
async def comment_on_member_proposal(
    proposal_id: str,
    body: MemberProposalCommentCreate,
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> MemberProposalCommentRead:
    proposal = await _load_proposal(session, proposal_id)
    if proposal is None or proposal.status not in {
        MemberProposalStatus.DISCUSSION,
        MemberProposalStatus.VOTING,
    }:
        raise HTTPException(status_code=409, detail="此提案目前不開放留言")
    comment = MemberProposalComment(
        proposal_id=proposal.id,
        user_id=user.id,
        body=body.body,
    )
    session.add(comment)
    await session.commit()
    return MemberProposalCommentRead(
        id=comment.id,
        user_id=comment.user_id,
        display_name=user.display_name,
        body=comment.body,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )


@community_router.put(
    "/v1/member-proposals/{proposal_id}/vote",
    response_model=MemberProposalRead,
)
async def vote_on_member_proposal(
    proposal_id: str,
    body: MemberProposalVoteUpsert,
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> MemberProposalRead:
    proposal = await _load_proposal(session, proposal_id, for_update=True)
    if proposal is None:
        raise HTTPException(status_code=404, detail="找不到社員提案")
    apply_member_proposal_clock(proposal, proposal.votes)
    if (
        proposal.status != MemberProposalStatus.VOTING
        or proposal.voting_ends_at is None
        or datetime.now(timezone.utc) >= _aware(proposal.voting_ends_at)
    ):
        raise HTTPException(status_code=409, detail="此提案目前不開放投票")
    if proposal.proposal_type == MemberProposalType.RESOLUTION and body.choice is None:
        raise HTTPException(status_code=422, detail="決議表決需選擇贊成、反對或棄權")
    if proposal.proposal_type == MemberProposalType.MULTIPLE_CHOICE:
        if body.option_id is None or all(item.id != body.option_id for item in proposal.options):
            raise HTTPException(status_code=422, detail="請選擇此提案的有效選項")
    vote = next(
        (item for item in proposal.votes if item.user_id == user.id),
        None,
    )
    if vote is None:
        vote = MemberProposalVote(
            proposal_id=proposal.id,
            user_id=user.id,
            choice=body.choice,
            option_id=body.option_id,
        )
        session.add(vote)
        proposal.votes.append(vote)
    else:
        vote.choice = body.choice
        vote.option_id = body.option_id
    await _award_points_once(session, user.id, 1, PointSourceType.VOTE, proposal.id, "參與社員表決")
    await session.commit()
    return _proposal_read(proposal, user.id)


@community_router.get(
    "/v1/member-proposals/{proposal_id}/votes",
    response_model=list[MemberProposalNamedVoteRead],
)
async def list_named_member_proposal_votes(
    proposal_id: str,
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> list[MemberProposalNamedVoteRead]:
    proposal = await session.get(MemberProposal, proposal_id)
    if proposal is None or (
        proposal.status
        in {
            MemberProposalStatus.DRAFT,
            MemberProposalStatus.PENDING_REVIEW,
            MemberProposalStatus.WITHDRAWN,
        }
        and proposal.created_by_id != user.id
    ):
        raise HTTPException(status_code=404, detail="找不到社員提案")
    rows = (
        await session.execute(
            select(MemberProposalVote, User)
            .join(User, User.id == MemberProposalVote.user_id)
            .options(selectinload(MemberProposalVote.option))
            .where(MemberProposalVote.proposal_id == proposal_id)
            .order_by(MemberProposalVote.created_at)
        )
    ).all()
    return [
        MemberProposalNamedVoteRead(
            user_id=vote.user_id,
            display_name=voter.display_name,
            choice=vote.choice,
            option_id=vote.option_id,
            option_label=vote.option.label if vote.option else None,
            updated_at=vote.updated_at,
        )
        for vote, voter in rows
    ]


@community_router.get(
    "/v1/admin/member-proposals",
    response_model=list[MemberProposalRead],
)
async def admin_list_member_proposals(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[MemberProposalRead]:
    proposals = (
        await session.scalars(
            select(MemberProposal)
            .options(
                selectinload(MemberProposal.votes),
                selectinload(MemberProposal.options),
                selectinload(MemberProposal.created_by),
            )
            .order_by(MemberProposal.created_at.desc())
        )
    ).all()
    return [_proposal_read(proposal, admin.id) for proposal in proposals]


@community_router.post(
    "/v1/admin/member-proposals/{proposal_id}/approve",
    response_model=MemberProposalRead,
)
async def approve_member_proposal(
    proposal_id: str,
    body: MemberProposalReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MemberProposalRead:
    proposal = await _load_proposal(session, proposal_id, for_update=True)
    if (
        proposal is None
        or proposal.status != MemberProposalStatus.PENDING_REVIEW
    ):
        raise HTTPException(status_code=409, detail="此提案目前不可核准")
    now = datetime.now(timezone.utc)
    if body.discussion_ends_at <= now:
        raise HTTPException(status_code=422, detail="討論截止必須在未來")
    proposal.status = MemberProposalStatus.DISCUSSION
    proposal.minimum_voters = body.minimum_voters
    proposal.discussion_ends_at = body.discussion_ends_at
    proposal.voting_ends_at = body.voting_ends_at
    proposal.reviewed_by_id = admin.id
    proposal.reviewed_at = now
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="member_proposal.approve",
            aggregate_type="member_proposal",
            aggregate_id=proposal.id,
            data={"minimum_voters": body.minimum_voters},
        )
    )
    await session.commit()
    return _proposal_read(proposal, admin.id)


@community_router.post(
    "/v1/admin/member-proposals/{proposal_id}/reject",
    response_model=MemberProposalRead,
)
async def reject_member_proposal(
    proposal_id: str,
    body: ActivityReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MemberProposalRead:
    if not body.reason:
        raise HTTPException(status_code=422, detail="請填寫駁回原因")
    proposal = await _load_proposal(session, proposal_id, for_update=True)
    if (
        proposal is None
        or proposal.status != MemberProposalStatus.PENDING_REVIEW
    ):
        raise HTTPException(status_code=409, detail="此提案目前不可駁回")
    proposal.status = MemberProposalStatus.REJECTED
    proposal.review_reason = body.reason
    proposal.reviewed_by_id = admin.id
    proposal.reviewed_at = datetime.now(timezone.utc)
    proposal.closed_at = datetime.now(timezone.utc)
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="member_proposal.reject",
            aggregate_type="member_proposal",
            aggregate_id=proposal.id,
            reason=body.reason,
        )
    )
    await session.commit()
    return _proposal_read(proposal, admin.id)


@community_router.post(
    "/v1/admin/member-proposals/{proposal_id}/close",
    response_model=MemberProposalRead,
)
async def close_member_proposal(
    proposal_id: str,
    body: ActivityReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MemberProposalRead:
    if not body.reason:
        raise HTTPException(status_code=422, detail="請填寫處理結果")
    proposal = await _load_proposal(session, proposal_id, for_update=True)
    if proposal is None or proposal.status not in {
        MemberProposalStatus.PASSED,
        MemberProposalStatus.REJECTED,
    }:
        raise HTTPException(status_code=409, detail="此提案目前不可結案")
    proposal.result_summary = body.reason
    proposal.status = MemberProposalStatus.CLOSED
    proposal.closed_at = datetime.now(timezone.utc)
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="member_proposal.close",
            aggregate_type="member_proposal",
            aggregate_id=proposal.id,
            reason=body.reason,
        )
    )
    await session.commit()
    return _proposal_read(proposal, admin.id)


router = community_router
