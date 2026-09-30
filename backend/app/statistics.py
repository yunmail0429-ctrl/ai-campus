"""School aggregates only: never return prompts, names or private resource titles."""

import csv
import io
from datetime import UTC, date, datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import case, distinct, func

from .auth import ADMIN
from .errors import AppError, Envelope, ok
from .models import AuditLog, EmbeddingUsage, ModelInvocation, QuotaTransaction, ResourceTask, TaskEvent
from .resource_contracts import KINDS

router = APIRouter(prefix="/api/v1/admin/statistics", tags=["statistics"])
SCHOOL_ZONE = timezone(timedelta(hours=8))


def period(start: date | None, end: date | None):
    end = end or datetime.now(SCHOOL_ZONE).date()
    if end.year < 1970 or (start and start.year < 1970):
        raise AppError(422, "INVALID_DATE_RANGE", "统计日期不能早于 1970 年。")
    start = start or end - timedelta(days=29)
    if end < start or end == date.max or (end - start).days >= 366:
        raise AppError(422, "INVALID_DATE_RANGE", "请选择先后顺序正确且不超过 366 天的日期范围。")
    lower = datetime.combine(start, datetime.min.time(), SCHOOL_ZONE).astimezone(UTC)
    upper = datetime.combine(end + timedelta(days=1), datetime.min.time(), SCHOOL_ZONE).astimezone(UTC)
    return start, end, lower, upper


def report(repo, start, end):
    start, end, lower, upper = period(start, end)
    daily = {}
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        daily[day] = {
            "date": day,
            "logins": 0,
            "calls": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "running": 0,
            "skillCalls": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "consumedCredits": 0,
            "embeddingCalls": 0,
            "embeddingTokens": 0,
            "embeddingUncertain": 0,
            "feedbackCount": 0,
            "feedbackScoreTotal": 0,
            "resourceOutputs": 0,
            **{f"resource_{kind}": 0 for kind in KINDS},
        }

    def query(model):
        return repo.query(model).where(model.created_at >= lower, model.created_at < upper)

    def day_key(model, column=None):
        column = model.created_at if column is None else column
        if repo.session.bind.dialect.name == "postgresql":
            return func.to_char(func.timezone("Asia/Shanghai", column), "YYYY-MM-DD")
        return func.strftime("%Y-%m-%d", column, "+8 hours")

    def grouped(model, columns, conditions=()):
        day = day_key(model)
        return repo.session.execute(
            query(model).where(*conditions).with_only_columns(day, *columns).group_by(day)
        )

    def total_if(condition, value=1):
        return func.sum(case((condition, value), else_=0))

    m = ModelInvocation
    columns = [
        func.count(),
        *[total_if(m.status == status) for status in ("completed", "failed", "cancelled", "running")],
        total_if(m.config_snapshot["skillId"].as_string().is_not(None)),
        total_if(m.status == "completed", m.input_tokens),
        total_if(m.status == "completed", m.output_tokens),
    ]
    keys = [
        "calls",
        "completed",
        "failed",
        "cancelled",
        "running",
        "skillCalls",
        "inputTokens",
        "outputTokens",
    ]
    for row in grouped(m, columns):
        daily[row[0]].update(zip(keys, row[1:], strict=True))
    for day, count in grouped(
        AuditLog, [func.count(distinct(AuditLog.actor_id))], [AuditLog.action == "auth.login"]
    ):
        daily[day]["logins"] = count
    for day, credits in grouped(
        QuotaTransaction, [func.sum(-QuotaTransaction.balance_delta)], [QuotaTransaction.kind == "settle"]
    ):
        daily[day]["consumedCredits"] = credits
    e = EmbeddingUsage
    for day, calls, tokens, uncertain in grouped(
        e,
        [func.count(), total_if(e.status == "completed", e.input_tokens), total_if(e.status != "completed")],
    ):
        daily[day].update(embeddingCalls=calls, embeddingTokens=tokens or 0, embeddingUncertain=uncertain)
    feedback_day = day_key(m, m.feedback_at)
    ratings = (
        repo.query(m)
        .where(m.feedback_at >= lower, m.feedback_at < upper, m.feedback_rating.is_not(None))
        .with_only_columns(feedback_day, func.count(), func.sum(m.feedback_rating))
        .group_by(feedback_day)
    )
    for day, count, score in repo.session.execute(ratings):
        daily[day].update(feedbackCount=count, feedbackScoreTotal=score)
    # Success is emitted in the same transaction as output readiness and settlement.
    # Use the first success event, not task creation or mutable output timestamps.
    # Deduplicate before applying the date filter so redeliveries cannot move history.
    success = (
        repo.query(TaskEvent)
        .where(TaskEvent.status == "succeeded")
        .with_only_columns(TaskEvent.task_id, func.min(TaskEvent.created_at).label("completed_at"))
        .group_by(TaskEvent.task_id)
        .subquery()
    )
    resource_day = day_key(ResourceTask, success.c.completed_at)
    outputs = (
        repo.query(ResourceTask)
        .join(success, success.c.task_id == ResourceTask.id)
        .where(success.c.completed_at >= lower, success.c.completed_at < upper)
        .with_only_columns(resource_day, ResourceTask.kind, func.count())
        .group_by(resource_day, ResourceTask.kind)
    )
    for day, kind, count in repo.session.execute(outputs):
        daily[day]["resourceOutputs"] += count
        daily[day][f"resource_{kind}"] = count
    summary = {
        key: sum(row[key] for row in daily.values())
        for key in next(iter(daily.values()))
        if key not in ("date", "logins")
    }
    summary["activeAccounts"] = repo.session.scalar(
        query(AuditLog)
        .where(AuditLog.action == "auth.login")
        .with_only_columns(func.count(distinct(AuditLog.actor_id)))
    )
    terminal = summary["completed"] + summary["failed"] + summary["cancelled"]
    summary["successRate"] = summary["completed"] / terminal if terminal else None
    summary["feedbackAverage"] = (
        summary["feedbackScoreTotal"] / summary["feedbackCount"] if summary["feedbackCount"] else None
    )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": "Asia/Shanghai",
        "summary": summary,
        "daily": list(daily.values()),
        "resourceOutputs": {kind: summary[f"resource_{kind}"] for kind in KINDS},
    }


@router.get("", response_model=Envelope[dict])
def statistics(request: Request, ctx: ADMIN, start: date | None = None, end: date | None = None):
    return ok(request, report(ctx.repo, start, end))


@router.get("/export")
def export(request: Request, ctx: ADMIN, start: date | None = None, end: date | None = None):
    data = report(ctx.repo, start, end)
    content = io.StringIO(newline="")
    writer = csv.writer(content)
    writer.writerow(
        [
            "日期（北京时间）",
            "登录人数",
            "模型调用",
            "完成",
            "失败",
            "取消",
            "进行中",
            "Skill调用",
            "确认输入Token",
            "确认输出Token",
            "实际消耗积分",
            "嵌入调用",
            "确认嵌入Token",
            "嵌入计量未确认",
            "评价数量",
            "评分合计",
            "资源产出",
            "图片产出",
            "PPT产出",
            "视频产出",
            "音乐产出",
            "数字人产出",
            "3D产出",
        ]
    )
    fields = [
        "date",
        "logins",
        "calls",
        "completed",
        "failed",
        "cancelled",
        "running",
        "skillCalls",
        "inputTokens",
        "outputTokens",
        "consumedCredits",
        "embeddingCalls",
        "embeddingTokens",
        "embeddingUncertain",
        "feedbackCount",
        "feedbackScoreTotal",
        "resourceOutputs",
        *[f"resource_{kind}" for kind in KINDS],
    ]
    writer.writerows([[row[key] for key in fields] for row in data["daily"]])
    ctx.repo.audit(
        ctx.user.id,
        "statistics.exported",
        None,
        request.state.request_id,
        {"start": data["start"], "end": data["end"]},
    )
    return Response(
        content.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="school-usage-{data["start"]}-{data["end"]}.csv"'
        },
    )
