from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import get_current_user, get_optional_user, require_admin
from ..database import get_session
from ..config import get_settings
from ..domain import apply_proposal_clock, aware, validate_campaign_schedule
from ..models import (
    GroupBundle,
    GroupCampaign,
    Notification,
    OutboxEvent,
    Product,
    ProposalStatus,
    TargetType,
    User,
    UserRole,
    Vote,
    VoteProposal,
)
from ..schemas import (
    CampaignCreate,
    CampaignRead,
    ProposalCreate,
    ProposalRead,
    ProposalReject,
    ProposalReview,
    VoteUpsert,
)


proposals_router = APIRouter(prefix="/v1/vote-proposals", tags=["proposals"])


OPEN_PROPOSAL_STATUSES = {
    ProposalStatus.PENDING_REVIEW,
    ProposalStatus.VOTING,
    ProposalStatus.CONVERSION_PENDING,
}


async def resolve_target(
    session: AsyncSession, target_type: TargetType, target_id: str
) -> Tuple[str, Optional[str]]:
    if target_type == TargetType.PRODUCT:
        product = await session.scalar(
            select(Product).where(
                Product.id == target_id, Product.is_active.is_(True)
            )
        )
        if product is None:
            raise HTTPException(status_code=422, detail="選擇的商品不存在")
        return product.name, product.image_url
    bundle = await session.scalar(
        select(GroupBundle).where(
            GroupBundle.id == target_id, GroupBundle.is_active.is_(True)
        )
    )
    if bundle is None:
        raise HTTPException(status_code=422, detail="選擇的團購套組不存在")
    return bundle.name, bundle.image_url


def proposal_read(
    proposal: VoteProposal, current_user: Optional[User]
) -> ProposalRead:
    my_vote = next(
        (
            vote
            for vote in proposal.votes
            if current_user is not None and vote.user_id == current_user.id
        ),
        None,
    )
    return ProposalRead(
        id=proposal.id,
        proposer_id=proposal.proposer_id,
        target_type=proposal.target_type,
        target_id=proposal.target_id,
        target_name=proposal.target_name_snapshot,
        status=proposal.status,
        threshold=proposal.threshold,
        deadline=proposal.deadline,
        conversion_deadline=proposal.conversion_deadline,
        vote_count=len(proposal.votes),
        estimated_quantity=sum(
            vote.estimated_quantity for vote in proposal.votes
        ),
        my_vote_quantity=(
            my_vote.estimated_quantity if my_vote is not None else None
        ),
        review_reason=proposal.review_reason,
        created_at=proposal.created_at,
    )


async def get_proposal_with_votes(
    session: AsyncSession, proposal_id: str, lock: bool = False
) -> Optional[VoteProposal]:
    query = (
        select(VoteProposal)
        .where(VoteProposal.id == proposal_id)
        .options(selectinload(VoteProposal.votes))
    )
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


async def apply_clock_with_events(
    session: AsyncSession,
    proposal: VoteProposal,
    now: datetime,
) -> bool:
    previous_status = proposal.status
    changed = apply_proposal_clock(proposal, len(proposal.votes), now)
    if not changed or proposal.status == previous_status:
        return changed
    event_names = {
        ProposalStatus.ENDED_UNMET: (
            "proposal.ended_unmet",
            "團購投票已截止",
            f"{proposal.target_name_snapshot} 未達投票門檻",
        ),
        ProposalStatus.CONVERSION_PENDING: (
            "proposal.conversion_pending",
            "團購投票已達標",
            f"{proposal.target_name_snapshot} 可由管理員建立正式團購",
        ),
        ProposalStatus.EXPIRED_UNHANDLED: (
            "proposal.expired_unhandled",
            "團購投票已關閉",
            f"{proposal.target_name_snapshot} 未在期限內建立正式團購",
        ),
    }
    event = event_names.get(proposal.status)
    if event is not None:
        event_type, title, body = event
        session.add_all(
            [
                Notification(
                    user_id=proposal.proposer_id,
                    event_type=event_type,
                    title=title,
                    body=body,
                    data={"proposal_id": proposal.id},
                ),
                OutboxEvent(
                    event_type=event_type,
                    aggregate_type="vote_proposal",
                    aggregate_id=proposal.id,
                    payload={"user_id": proposal.proposer_id},
                ),
            ]
        )
    return changed


@proposals_router.get("", response_model=List[ProposalRead])
async def list_proposals(
    current_user: Optional[User] = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
) -> List[ProposalRead]:
    public_statuses = [
        ProposalStatus.VOTING,
        ProposalStatus.ENDED_UNMET,
        ProposalStatus.CONVERSION_PENDING,
        ProposalStatus.CONVERTED,
        ProposalStatus.EXPIRED_UNHANDLED,
    ]
    filters = [VoteProposal.status.in_(public_statuses)]
    if current_user is not None:
        filters.append(VoteProposal.proposer_id == current_user.id)
    if current_user is not None and current_user.user_role == UserRole.ADMIN:
        filters = []
    query = select(VoteProposal).options(selectinload(VoteProposal.votes))
    if filters:
        query = query.where(or_(*filters))
    proposals = list(
        await session.scalars(query.order_by(VoteProposal.created_at.desc()))
    )
    changed = False
    now = datetime.now(timezone.utc)
    for proposal in proposals:
        changed = (
            await apply_clock_with_events(session, proposal, now) or changed
        )
    if changed:
        await session.commit()
    return [proposal_read(proposal, current_user) for proposal in proposals]


@proposals_router.get("/{proposal_id}", response_model=ProposalRead)
async def get_proposal(
    proposal_id: str,
    current_user: Optional[User] = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
) -> ProposalRead:
    proposal = await get_proposal_with_votes(session, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="找不到投票提案")
    if (
        proposal.status in {ProposalStatus.PENDING_REVIEW, ProposalStatus.REJECTED}
        and (
            current_user is None
            or (
                current_user.id != proposal.proposer_id
                and current_user.user_role != UserRole.ADMIN
            )
        )
    ):
        raise HTTPException(status_code=404, detail="找不到投票提案")
    if await apply_clock_with_events(
        session, proposal, datetime.now(timezone.utc)
    ):
        await session.commit()
    return proposal_read(proposal, current_user)


@proposals_router.post(
    "",
    response_model=ProposalRead,
)
async def create_proposal(
    body: ProposalCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProposalRead:
    target_name, _ = await resolve_target(
        session, body.target_type, body.target_id
    )
    duplicate = await session.scalar(
        select(VoteProposal)
        .where(
            VoteProposal.target_type == body.target_type,
            VoteProposal.target_id == body.target_id,
            VoteProposal.status.in_(OPEN_PROPOSAL_STATUSES),
        )
        .options(selectinload(VoteProposal.votes))
    )
    if duplicate is not None:
        changed = await apply_clock_with_events(
            session, duplicate, datetime.now(timezone.utc)
        )
        if changed:
            await session.commit()
        if duplicate.status in OPEN_PROPOSAL_STATUSES:
            return proposal_read(duplicate, user)
    proposal = VoteProposal(
        proposer_id=user.id,
        target_type=body.target_type,
        target_id=body.target_id,
        target_name_snapshot=target_name,
        status=ProposalStatus.PENDING_REVIEW,
    )
    session.add(proposal)
    await session.flush()
    session.add(
        OutboxEvent(
            event_type="proposal.review_requested",
            aggregate_type="vote_proposal",
            aggregate_id=proposal.id,
            payload={"proposer_id": user.id, "target_name": target_name},
        )
    )
    await session.commit()
    proposal = await get_proposal_with_votes(session, proposal.id)
    return proposal_read(proposal, user)


@proposals_router.post(
    "/{proposal_id}/admin/approve", response_model=ProposalRead
)
async def approve_proposal(
    proposal_id: str,
    body: ProposalReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ProposalRead:
    proposal = await get_proposal_with_votes(session, proposal_id, lock=True)
    if proposal is None:
        raise HTTPException(status_code=404, detail="找不到投票提案")
    if proposal.status != ProposalStatus.PENDING_REVIEW:
        raise HTTPException(status_code=409, detail="此提案已完成審核")
    now = datetime.now(timezone.utc)
    deadline = body.deadline or (
        now + timedelta(days=get_settings().default_vote_days)
    )
    if aware(deadline) <= now:
        raise HTTPException(status_code=422, detail="投票截止時間必須在未來")
    proposal.status = ProposalStatus.VOTING
    proposal.threshold = body.threshold
    proposal.deadline = deadline
    proposal.reviewed_by_id = admin.id
    proposal.reviewed_at = now
    session.add(
        OutboxEvent(
            event_type="proposal.approved",
            aggregate_type="vote_proposal",
            aggregate_id=proposal.id,
            payload={"user_id": proposal.proposer_id},
        )
    )
    await session.commit()
    return proposal_read(proposal, admin)


@proposals_router.post(
    "/{proposal_id}/admin/reject", response_model=ProposalRead
)
async def reject_proposal(
    proposal_id: str,
    body: ProposalReject,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ProposalRead:
    proposal = await get_proposal_with_votes(session, proposal_id, lock=True)
    if proposal is None:
        raise HTTPException(status_code=404, detail="找不到投票提案")
    if proposal.status != ProposalStatus.PENDING_REVIEW:
        raise HTTPException(status_code=409, detail="此提案已完成審核")
    proposal.status = ProposalStatus.REJECTED
    proposal.review_reason = body.reason
    proposal.reviewed_by_id = admin.id
    proposal.reviewed_at = datetime.now(timezone.utc)
    session.add(
        OutboxEvent(
            event_type="proposal.rejected",
            aggregate_type="vote_proposal",
            aggregate_id=proposal.id,
            payload={"user_id": proposal.proposer_id, "reason": body.reason},
        )
    )
    await session.commit()
    return proposal_read(proposal, admin)


@proposals_router.put("/{proposal_id}/vote", response_model=ProposalRead)
async def upsert_vote(
    proposal_id: str,
    body: VoteUpsert,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProposalRead:
    proposal = await get_proposal_with_votes(session, proposal_id, lock=True)
    if proposal is None:
        raise HTTPException(status_code=404, detail="找不到投票提案")
    changed = await apply_clock_with_events(
        session, proposal, datetime.now(timezone.utc)
    )
    if proposal.status != ProposalStatus.VOTING:
        if changed:
            await session.commit()
        raise HTTPException(status_code=409, detail="此提案目前無法投票")
    vote = next(
        (existing for existing in proposal.votes if existing.user_id == user.id),
        None,
    )
    previous_count = len(proposal.votes)
    if vote is None:
        vote = Vote(
            proposal_id=proposal.id,
            user_id=user.id,
            estimated_quantity=body.estimated_quantity,
        )
        session.add(vote)
        proposal.votes.append(vote)
    else:
        vote.estimated_quantity = body.estimated_quantity
    if previous_count < proposal.threshold <= len(proposal.votes):
        session.add(
            OutboxEvent(
                event_type="proposal.threshold_reached",
                aggregate_type="vote_proposal",
                aggregate_id=proposal.id,
                payload={"vote_count": len(proposal.votes)},
            )
        )
    await session.commit()
    return proposal_read(proposal, user)


@proposals_router.delete("/{proposal_id}/vote", response_model=ProposalRead)
async def withdraw_vote(
    proposal_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProposalRead:
    proposal = await get_proposal_with_votes(session, proposal_id, lock=True)
    if proposal is None:
        raise HTTPException(status_code=404, detail="找不到投票提案")
    changed = await apply_clock_with_events(
        session, proposal, datetime.now(timezone.utc)
    )
    if proposal.status != ProposalStatus.VOTING:
        if changed:
            await session.commit()
        raise HTTPException(status_code=409, detail="投票已截止，無法撤票")
    vote = next(
        (existing for existing in proposal.votes if existing.user_id == user.id),
        None,
    )
    if vote is None:
        raise HTTPException(status_code=404, detail="尚未參與此投票")
    await session.delete(vote)
    proposal.votes.remove(vote)
    await session.commit()
    return proposal_read(proposal, user)


@proposals_router.post(
    "/{proposal_id}/admin/convert",
    response_model=CampaignRead,
    status_code=status.HTTP_201_CREATED,
)
async def convert_proposal(
    proposal_id: str,
    body: CampaignCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> CampaignRead:
    proposal = await get_proposal_with_votes(session, proposal_id, lock=True)
    if proposal is None:
        raise HTTPException(status_code=404, detail="找不到投票提案")
    changed = await apply_clock_with_events(
        session, proposal, datetime.now(timezone.utc)
    )
    can_convert_early = (
        proposal.status == ProposalStatus.VOTING
        and len(proposal.votes) >= proposal.threshold
    )
    if (
        proposal.status != ProposalStatus.CONVERSION_PENDING
        and not can_convert_early
    ):
        if changed:
            await session.commit()
        raise HTTPException(status_code=409, detail="此提案尚未進入開團階段")
    if (
        body.target_type != proposal.target_type
        or body.target_id != proposal.target_id
    ):
        raise HTTPException(status_code=422, detail="開團目標與提案不一致")
    if (
        body.source_proposal_id is not None
        and body.source_proposal_id != proposal.id
    ):
        raise HTTPException(status_code=422, detail="來源提案編號不一致")
    campaign = GroupCampaign(
        **body.model_dump(exclude={"source_proposal_id"}),
        source_proposal_id=proposal.id,
        created_by_id=admin.id,
    )
    try:
        validate_campaign_schedule(campaign, datetime.now(timezone.utc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    proposal.status = ProposalStatus.CONVERTED
    session.add(campaign)
    await session.flush()
    session.add(
        OutboxEvent(
            event_type="group.opened",
            aggregate_type="group_campaign",
            aggregate_id=campaign.id,
            payload={"proposal_id": proposal.id},
        )
    )
    await session.commit()
    await session.refresh(campaign)
    return CampaignRead.model_validate(
        {
            **campaign.__dict__,
            "available_quantity": campaign.supply_cap,
        }
    )


router = proposals_router
