import base64
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .errors import AppError
from .models import AuditLog


class Repository:
    """A tenant is resolved on the server before business queries are constructed."""

    def __init__(self, session: Session, tenant_id: str):
        if not tenant_id:
            raise ValueError("Repository requires a trusted tenant")
        self.session, self.tenant_id = session, tenant_id

    def query(self, model):
        return select(model).where(model.tenant_id == self.tenant_id)

    def one(self, model, *conditions, lock=False):
        statement = self.query(model).where(*conditions)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(statement)

    def require(self, model, *conditions, lock=False):
        row = self.one(model, *conditions, lock=lock)
        if row is None:
            raise AppError(404, "NOT_FOUND", "记录不存在或无权访问。")
        return row

    def add(self, model, /, **values):
        if "tenant_id" in values:
            raise ValueError("tenant_id cannot be overridden")
        row = model(tenant_id=self.tenant_id, **values)
        self.session.add(row)
        self.session.flush()
        return row

    def page(self, model, conditions=(), limit=50, cursor=None):
        statement = self.query(model).where(*conditions)
        if cursor:
            try:
                stamp, row_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                stamp = datetime.fromisoformat(stamp)
                if not isinstance(row_id, str) or len(row_id) != 36:
                    raise ValueError()
                if str(UUID(row_id)) != row_id:
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                raise AppError(422, "INVALID_CURSOR", "分页标识无效。") from None
            statement = statement.where(
                or_(model.created_at < stamp, and_(model.created_at == stamp, model.id < row_id))
            )
        rows = list(
            self.session.scalars(
                statement.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)
            )
        )
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = base64.urlsafe_b64encode(
                json.dumps([last.created_at.isoformat(), last.id]).encode()
            ).decode()
        return rows[:limit], next_cursor

    def audit(self, actor_id, action, target_id, request_id, details=None):
        self.add(
            AuditLog,
            actor_id=actor_id,
            action=action,
            target_id=target_id,
            request_id=request_id,
            details=details or {},
        )
