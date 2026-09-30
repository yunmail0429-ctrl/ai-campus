from fastapi import Request
from pydantic import BaseModel


class Envelope[T](BaseModel):
    code: str = "OK"
    message: str = "success"
    data: T
    requestId: str


class Page[T](BaseModel):
    items: list[T]
    nextCursor: str | None


class ErrorOutput(BaseModel):
    code: str
    message: str
    data: None = None
    requestId: str


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def ok(request: Request, data=None):
    return {"code": "OK", "message": "success", "data": data, "requestId": request.state.request_id}
