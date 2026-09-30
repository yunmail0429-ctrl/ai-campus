from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import or_

from .auth import ADMIN, CTX
from .billing import lock_account
from .errors import AppError, Envelope, Page, ok
from .models import (
    Conversation,
    ModelInvocation,
    QuotaAccount,
    Skill,
    SkillReview,
    SkillTestCase,
    SkillVersion,
    iso_utc,
)
from .schemas import (
    MessageInput,
    ReviewInput,
    RollbackInput,
    SkillInput,
    SkillInvokeInput,
    SkillOutput,
    SkillReadinessInput,
)

router = APIRouter(prefix="/api/v1", tags=["skills"])


def serialize(row):
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "prompt": row.prompt,
        "scope": row.scope,
        "status": row.status,
        "version": row.active_version,
        "reviewerNotes": row.reviewer_notes,
    }


def editable(ctx, skill_id):
    row = ctx.repo.require(Skill, Skill.id == skill_id, Skill.owner_id == ctx.user.id, lock=True)
    if row.status not in ("draft", "rejected"):
        raise AppError(409, "SKILL_NOT_EDITABLE", "只有草稿或退回的 Skill 可以修改。")
    return row


def visible(ctx, skill_id):
    row = ctx.repo.require(Skill, Skill.id == skill_id)
    if row.owner_id != ctx.user.id and not (row.scope == "school" and row.status == "published"):
        raise AppError(404, "NOT_FOUND", "Skill 不存在或无权访问。")
    return row


def snapshot(row):
    return {
        "name": row.name,
        "description": row.description,
        "prompt": row.prompt,
        "riskBoundary": row.risk_boundary,
        "grades": row.grades,
    }


def readiness_output(ctx, row):
    cases = ctx.repo.session.scalars(
        ctx.repo.query(SkillTestCase).where(SkillTestCase.skill_id == row.id).order_by(SkillTestCase.position)
    )
    return {
        "riskBoundary": row.risk_boundary,
        "grades": row.grades,
        "tests": [{"input": case.input, "expectedOutput": case.expected_output} for case in cases],
    }


@router.get("/skills/{skill_id}/readiness", response_model=Envelope[dict])
def get_readiness(skill_id: str, request: Request, ctx: CTX):
    row = ctx.repo.require(Skill, Skill.id == skill_id, Skill.owner_id == ctx.user.id)
    return ok(request, readiness_output(ctx, row))


@router.get("/admin/skills/{skill_id}/readiness", response_model=Envelope[dict])
def get_review_materials(skill_id: str, request: Request, ctx: ADMIN):
    row = ctx.repo.require(
        Skill, Skill.id == skill_id, or_(Skill.owner_id == ctx.user.id, Skill.status != "draft")
    )
    return ok(request, readiness_output(ctx, row))


@router.get("/admin/skills/{skill_id}/versions", response_model=Envelope[list[dict]])
def versions(skill_id: str, request: Request, ctx: ADMIN):
    ctx.repo.require(Skill, Skill.id == skill_id, or_(Skill.owner_id == ctx.user.id, Skill.status != "draft"))
    rows = ctx.repo.session.scalars(
        ctx.repo.query(SkillVersion)
        .where(SkillVersion.skill_id == skill_id)
        .order_by(SkillVersion.number.desc())
    )
    return ok(
        request,
        [
            {
                "version": row.number,
                "createdAt": iso_utc(row.created_at),
                "disabled": bool(row.snapshot.get("disabled")),
            }
            for row in rows
        ],
    )


@router.get("/skills", response_model=Envelope[Page[SkillOutput]])
def list_skills(
    request: Request,
    ctx: CTX,
    scope: str | None = Query(None, pattern="^(school|mine)$"),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    public = (Skill.scope == "school") & (Skill.status == "published")
    private = Skill.owner_id == ctx.user.id
    rows, next_cursor = ctx.repo.page(
        Skill,
        (public if scope == "school" else private if scope == "mine" else or_(public, private),),
        limit,
        cursor,
    )
    return ok(request, {"items": [serialize(row) for row in rows], "nextCursor": next_cursor})


@router.get("/skills/{skill_id}", response_model=Envelope[SkillOutput])
def get_skill(skill_id: str, request: Request, ctx: CTX):
    return ok(request, serialize(visible(ctx, skill_id)))


@router.post("/skills", response_model=Envelope[SkillOutput], status_code=201)
def create(data: SkillInput, request: Request, ctx: CTX):
    row = ctx.repo.add(Skill, owner_id=ctx.user.id, **data.model_dump())
    ctx.repo.audit(ctx.user.id, "skill.created", row.id, request.state.request_id)
    return ok(request, serialize(row))


@router.patch("/skills/{skill_id}", response_model=Envelope[SkillOutput])
def edit(skill_id: str, data: SkillInput, request: Request, ctx: CTX):
    row = editable(ctx, skill_id)
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    row.status, row.reviewer_notes = "draft", ""
    return ok(request, serialize(row))


@router.put("/skills/{skill_id}/readiness", response_model=Envelope[SkillOutput])
def readiness(skill_id: str, data: SkillReadinessInput, request: Request, ctx: CTX):
    row = editable(ctx, skill_id)
    row.risk_boundary, row.grades = data.riskBoundary, data.grades
    if len({case.input for case in data.tests}) < 5:
        raise AppError(422, "INSUFFICIENT_DISTINCT_TESTS", "至少需要五个不同的测试输入。")
    for case in ctx.repo.session.scalars(
        ctx.repo.query(SkillTestCase).where(SkillTestCase.skill_id == row.id)
    ):
        ctx.repo.session.delete(case)
    ctx.repo.session.flush()
    for position, case in enumerate(data.tests):
        ctx.repo.add(
            SkillTestCase,
            skill_id=row.id,
            position=position,
            input=case.input,
            expected_output=case.expectedOutput,
        )
    return ok(request, serialize(row))


@router.post("/skills/{skill_id}/submit", response_model=Envelope[SkillOutput])
def submit(skill_id: str, request: Request, ctx: CTX):
    row = editable(ctx, skill_id)
    row.status = "pending"
    ctx.repo.audit(ctx.user.id, "skill.submitted", row.id, request.state.request_id)
    return ok(request, serialize(row))


@router.get("/admin/skills", response_model=Envelope[Page[SkillOutput]])
def review_list(
    request: Request,
    ctx: ADMIN,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    rows, next_cursor = ctx.repo.page(
        Skill, (or_(Skill.owner_id == ctx.user.id, Skill.status != "draft"),), limit=limit, cursor=cursor
    )
    return ok(request, {"items": [serialize(row) for row in rows], "nextCursor": next_cursor})


@router.post("/admin/skills/{skill_id}/reject", response_model=Envelope[SkillOutput])
def reject(skill_id: str, data: ReviewInput, request: Request, ctx: ADMIN):
    row = ctx.repo.require(Skill, Skill.id == skill_id, lock=True)
    if row.status != "pending":
        raise AppError(409, "INVALID_SKILL_STATE", "此 Skill 不在待审核状态。")
    row.status, row.reviewer_notes = "rejected", data.notes
    ctx.repo.add(SkillReview, skill_id=row.id, reviewer_id=ctx.user.id, decision="rejected", notes=data.notes)
    ctx.repo.audit(ctx.user.id, "skill.rejected", row.id, request.state.request_id)
    return ok(request, serialize(row))


@router.post("/admin/skills/{skill_id}/publish", response_model=Envelope[SkillOutput])
def publish(skill_id: str, data: ReviewInput, request: Request, ctx: ADMIN):
    row = ctx.repo.require(Skill, Skill.id == skill_id, lock=True)
    if row.status != "pending":
        raise AppError(409, "INVALID_SKILL_STATE", "请先提交学校审核。")
    cases = list(
        ctx.repo.session.scalars(
            ctx.repo.query(SkillTestCase)
            .where(SkillTestCase.skill_id == row.id)
            .order_by(SkillTestCase.position)
        )
    )
    if len(cases) < 5 or not row.risk_boundary or not row.grades:
        raise AppError(422, "SKILL_NOT_READY", "发布需五个测试样例及预期输出、风险边界和适用年级。")
    if row.version == 0:
        # Version zero is an explicit disabled baseline for a first-publication rollback.
        ctx.repo.add(SkillVersion, skill_id=row.id, number=0, snapshot={"disabled": True})
    row.version += 1
    row.active_version = row.version
    ctx.repo.add(
        SkillVersion,
        skill_id=row.id,
        number=row.version,
        snapshot={
            **snapshot(row),
            "tests": [{"input": c.input, "expectedOutput": c.expected_output} for c in cases],
        },
    )
    row.scope, row.status, row.reviewer_notes = "school", "published", data.notes
    ctx.repo.add(
        SkillReview, skill_id=row.id, reviewer_id=ctx.user.id, decision="published", notes=data.notes
    )
    ctx.repo.audit(ctx.user.id, "skill.published", row.id, request.state.request_id, {"version": row.version})
    return ok(request, serialize(row))


@router.post("/admin/skills/{skill_id}/unpublish", response_model=Envelope[SkillOutput])
def unpublish(skill_id: str, request: Request, ctx: ADMIN):
    row = ctx.repo.require(Skill, Skill.id == skill_id, lock=True)
    if row.status != "published":
        raise AppError(409, "INVALID_SKILL_STATE", "此 Skill 尚未发布。")
    row.status = "archived"
    ctx.repo.audit(ctx.user.id, "skill.unpublished", row.id, request.state.request_id)
    return ok(request, serialize(row))


@router.post("/admin/skills/{skill_id}/rollback", response_model=Envelope[SkillOutput])
def rollback(skill_id: str, data: RollbackInput, request: Request, ctx: ADMIN):
    row = ctx.repo.require(Skill, Skill.id == skill_id, lock=True)
    version = ctx.repo.require(
        SkillVersion, SkillVersion.skill_id == row.id, SkillVersion.number == data.version
    )
    row.active_version = data.version
    if version.snapshot.get("disabled"):
        row.status = "archived"
    else:
        row.name, row.description, row.prompt = (
            version.snapshot[k] for k in ("name", "description", "prompt")
        )
        row.risk_boundary, row.grades = version.snapshot["riskBoundary"], version.snapshot["grades"]
        row.status, row.scope = "published", "school"
    ctx.repo.audit(
        ctx.user.id, "skill.rolled_back", row.id, request.state.request_id, {"targetVersion": data.version}
    )
    return ok(request, serialize(row))


@router.post(
    "/skills/{skill_id}/invoke",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
def invoke(
    skill_id: str,
    data: SkillInvokeInput,
    request: Request,
    ctx: CTX,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
):
    from .chat_runtime import prepare, stream_chat

    skill = visible(ctx, skill_id)
    account = ctx.repo.require(QuotaAccount, QuotaAccount.user_id == ctx.user.id)
    lock_account(ctx.repo, account.id)
    previous = ctx.repo.one(
        ModelInvocation,
        ModelInvocation.user_id == ctx.user.id,
        ModelInvocation.operation_key == idempotency_key,
    )
    conversation_id = data.conversationId or (previous.conversation_id if previous else None)
    if conversation_id is None:
        conversation_id = ctx.repo.add(Conversation, user_id=ctx.user.id, title=skill.name).id
    prepared = prepare(
        ctx,
        request.app.state.settings,
        conversation_id,
        MessageInput(
            content=data.content,
            skillId=skill_id,
            documentIds=data.documentIds,
            useKnowledge=data.useKnowledge,
        ),
        idempotency_key,
    )
    return StreamingResponse(
        stream_chat(request.app.state.sessions, request.app.state.settings, prepared),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )
