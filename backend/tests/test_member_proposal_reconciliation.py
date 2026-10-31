from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.jobs as jobs
from app.integrations.email_sender import EmailSendResult
from app.models import (
    Activity,
    ActivityStatus,
    MemberProposal,
    MemberProposalStatus,
    MemberProposalType,
    MemberProposalVote,
    MemberVoteChoice,
    Membership,
    MembershipStatus,
    OutboxEvent,
    ProposalOption,
    User,
)
from app.routers.community import community_router
from app.schemas import MemberProposalCreate
from tests.support import api_test_context, auth_headers, make_test_settings


class RecordingEmailSender:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(message)
        return EmailSendResult(accepted=True, provider_message_id="isolated-email", status_code=202)


@pytest.mark.asyncio
async def test_fresh_reconcile_settles_option_vote_and_completes_later_work(database_session, monkeypatch):
    now = datetime.now(timezone.utc)
    author = User(
        email="proposal-job-author@example.com",
        display_name="提案社員",
        password_hash="unused",
        membership=Membership(status=MembershipStatus.ACTIVE, member_number="JOB-MEMBER-1"),
    )
    payload = MemberProposalCreate(
        title="共學內容選擇",
        body="選擇下次共學內容",
        proposal_type=MemberProposalType.MULTIPLE_CHOICE,
        options=[{"label": "農事體驗"}, {"label": "食農教育"}],
    )
    proposal = MemberProposal(
        created_by=author,
        title=payload.title,
        body=payload.body,
        proposal_type=payload.proposal_type,
        status=MemberProposalStatus.VOTING,
        minimum_voters=1,
        discussion_ends_at=now - timedelta(days=2),
        voting_ends_at=now - timedelta(hours=1),
        options=[ProposalOption(label=option.label, position=index) for index, option in enumerate(payload.options)],
    )
    activity = Activity(
        created_by=author,
        title="已結束共學",
        description="確認後續活動結算仍正常執行",
        location="合作社",
        starts_at=now - timedelta(hours=3),
        ends_at=now - timedelta(hours=2),
        registration_deadline=now - timedelta(days=1),
        capacity=2,
        status=ActivityStatus.PUBLISHED,
    )
    database_session.add_all([author, proposal, activity])
    await database_session.flush()
    database_session.add(MemberProposalVote(proposal_id=proposal.id, user_id=author.id, option_id=proposal.options[0].id))
    database_session.add(OutboxEvent(
        event_type="send_email",
        aggregate_type="user",
        aggregate_id=author.id,
        available_at=now - timedelta(seconds=1),
        payload={"to_email": author.email, "subject": "後段通知", "text_content": "投票結算後仍應處理此通知"},
    ))
    await database_session.commit()
    factory = async_sessionmaker(database_session.bind, expire_on_commit=False)
    sender = RecordingEmailSender()
    monkeypatch.setattr(jobs, "email_sender_from_settings", lambda settings: sender)

    async with factory() as worker_session:
        report = await jobs.reconcile_once(worker_session, settings=make_test_settings(), now=now)

    assert report.member_proposals == 1
    assert report.activities == 1
    assert report.outbox_completed == 1
    assert report.outbox_failed == 0
    assert [message.subject for message in sender.messages] == ["後段通知"]
    async with factory() as read_session:
        async with api_test_context(read_session, [community_router], settings=make_test_settings()) as client:
            detail = await client.get(f"/v1/member-proposals/{proposal.id}", headers=auth_headers(author))
            activity_detail = await client.get(f"/v1/activities/{activity.id}", headers=auth_headers(author))
    assert detail.status_code == activity_detail.status_code == 200
    assert detail.json()["status"] == "passed"
    assert detail.json()["result_summary"] == "最高票選項：農事體驗"
    assert activity_detail.json()["status"] == "completed"

    async with factory() as retry_session:
        repeated = await jobs.reconcile_once(retry_session, settings=make_test_settings(), now=now)
    assert (repeated.member_proposals, repeated.activities, repeated.outbox_completed) == (0, 0, 0)
    assert len(sender.messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proposal_type, choices, minimum_voters, expected_status, expected_summary",
    [
        pytest.param(MemberProposalType.MULTIPLE_CHOICE, [], 1, "rejected", None, id="multiple-no-votes"),
        pytest.param(MemberProposalType.MULTIPLE_CHOICE, ["a", "b"], 1, "passed", "最高票選項：食農教育", id="multiple-tied"),
        pytest.param(MemberProposalType.MULTIPLE_CHOICE, ["a", "b"], 3, "rejected", "最高票選項：食農教育", id="multiple-below-quorum"),
        pytest.param(MemberProposalType.RESOLUTION, [], 1, "rejected", None, id="resolution-no-votes"),
        pytest.param(MemberProposalType.RESOLUTION, [MemberVoteChoice.YES, MemberVoteChoice.NO], 1, "rejected", None, id="resolution-tied"),
        pytest.param(MemberProposalType.RESOLUTION, [MemberVoteChoice.YES, MemberVoteChoice.ABSTAIN], 2, "passed", None, id="resolution-abstention-counts-for-quorum"),
    ],
)
async def test_fresh_reconcile_preserves_governance_outcomes(
    database_session, proposal_type, choices, minimum_voters, expected_status, expected_summary,
):
    now = datetime.now(timezone.utc)
    users = [User(
        email=f"outcome-voter-{index}@example.com",
        display_name=f"表決社員{index}",
        password_hash="unused",
        membership=Membership(status=MembershipStatus.ACTIVE, member_number=f"OUTCOME-MEMBER-{index}"),
    ) for index in range(2)]
    option_ids = {"a": "00000000-0000-4000-8000-00000000000a", "b": "00000000-0000-4000-8000-00000000000b"}
    payload = MemberProposalCreate(
        title="表決結果回歸",
        body="保留目前合法投票的結算結果",
        proposal_type=proposal_type,
        options=([{"label": "農事體驗"}, {"label": "食農教育"}]
                 if proposal_type == MemberProposalType.MULTIPLE_CHOICE else []),
    )
    proposal = MemberProposal(
        created_by=users[0], title=payload.title, body=payload.body, proposal_type=payload.proposal_type,
        status=MemberProposalStatus.VOTING, minimum_voters=minimum_voters,
        discussion_ends_at=now - timedelta(days=2), voting_ends_at=now - timedelta(hours=1),
        options=[ProposalOption(id=option_ids[key], label=option.label, position=index)
                 for index, (key, option) in enumerate(zip(("a", "b"), payload.options))],
    )
    database_session.add_all([*users, proposal])
    await database_session.flush()
    for voter, choice in zip(users, choices):
        database_session.add(MemberProposalVote(
            proposal_id=proposal.id, user_id=voter.id,
            option_id=option_ids[choice] if proposal_type == MemberProposalType.MULTIPLE_CHOICE else None,
            choice=choice if proposal_type == MemberProposalType.RESOLUTION else None,
        ))
    await database_session.commit()
    factory = async_sessionmaker(database_session.bind, expire_on_commit=False)
    async with factory() as worker_session:
        report = await jobs.reconcile_once(worker_session, settings=make_test_settings(), now=now)
    assert report.member_proposals == 1
    assert report.outbox_failed == 0
    async with factory() as read_session:
        async with api_test_context(read_session, [community_router], settings=make_test_settings()) as client:
            detail = await client.get(f"/v1/member-proposals/{proposal.id}", headers=auth_headers(users[0]))
    assert detail.status_code == 200
    assert detail.json()["status"] == expected_status
    assert detail.json()["result_summary"] == expected_summary
