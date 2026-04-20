"""ASGI middleware that rejects oversized request bodies BEFORE they reach
the route handler.

FastAPI / Starlette does not impose a body-size limit by default. Without
this middleware, an attacker can POST an arbitrarily large multipart body
and Starlette will happily buffer each part to ``/tmp`` via its
SpooledTemporaryFile machinery until disk fills up. Our route-level
streaming size check in ``/invoices/upload`` is a belt-and-suspenders
second layer; this middleware is the primary defence.

Strategy: wrap the ASGI ``receive`` callable and count bytes as they
arrive. On the first chunk that pushes accumulated bytes past the
threshold, return a 413 response without invoking the downstream app.

Path scoping: only applied to write endpoints (``POST /api/v1/invoices/upload``
and ``POST /api/v1/invoices/upload-batch`` — the latter is reserved for a
future release). GET / OPTIONS and non-upload writes are allowed to pass
through unchanged so a giant JSON body on ``/auth/login`` is still caught
elsewhere (pydantic) and regular requests pay zero overhead.
"""

from __future__ import annotations

from typing import Callable, Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class ContentSizeLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_content_size: int,
        protected_paths: Iterable[str] = (),
    ) -> None:
        self.app = app
        self.max_content_size = max_content_size
        self._protected_paths = tuple(protected_paths)

    def _is_protected(self, scope: Scope) -> bool:
        if scope.get("type") != "http":
            return False
        if scope.get("method", "").upper() not in {"POST", "PUT", "PATCH"}:
            return False
        path: str = scope.get("path", "") or ""
        return any(path.startswith(prefix) for prefix in self._protected_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._is_protected(scope):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                declared_len = int(declared)
            except ValueError:
                declared_len = -1
            if declared_len > self.max_content_size:
                await self._send_413(send)
                return

        received = 0
        limit_exceeded = False

        async def receive_wrapper() -> Message:
            nonlocal received, limit_exceeded
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"") or b""
                received += len(body)
                if received > self.max_content_size:
                    limit_exceeded = True
                    return {
                        "type": "http.request",
                        "body": b"",
                        "more_body": False,
                    }
            return message

        sent_response = False

        async def send_wrapper(message: Message) -> None:
            nonlocal sent_response
            if limit_exceeded:
                return
            sent_response = True
            await send(message)

        await self.app(scope, receive_wrapper, send_wrapper)

        if limit_exceeded and not sent_response:
            await self._send_413(send)

    async def _send_413(self, send: Send) -> None:
        detail = (
            f'{{"detail":"Request body exceeds {self.max_content_size} byte limit"}}'
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(detail)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": detail, "more_body": False})


# Default protected prefixes used when the middleware is registered via
# ``app.add_middleware`` without an explicit override. Keeps the
# configuration single-sourced with the upload endpoint itself.
DEFAULT_PROTECTED_PATHS: tuple[str, ...] = (
    "/api/v1/invoices/upload",
)
