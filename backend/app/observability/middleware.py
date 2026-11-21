"""HTTP 层中间件：给每个请求绑定 run_id，并记录请求起止。

## 为什么用中间件而不是装饰器

`run_id` 要在「请求入口 → 路由 → LCEL 回调 → 响应」全程可见。中间件是最外层，
在它里面 set 一次 ContextVar，内层任何深度（包括 chains.py 的回调）都能读到，
路由结束后 reset，避免 asyncio 复用上下文时串号。

## 记什么

- DEBUG 请求进入：method / path / 请求体**字节数**（不是内容），便于分析提示词长度与入参规模
- INFO 请求完成：status / 耗时 / run_id，由 Starlette 的 response 观察
- ERROR 未捕获异常：异常类型与消息前缀（截 200 字，防止请求体被带进日志）

章节原文、补充要求正文、api_key 都不记——只记字节数。字节数直接取 `content-length`
头，**不读请求体**：读 body 要接管 `receive` 才能还给下游，在 BaseHTTPMiddleware 里
既有流式响应风险，也会让「只加日志」的基础设施开始影响业务请求的读取方式。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .logging import (
    get_request_id,
    new_request_id,
    reset_request_id,
    set_request_id,
)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """给每个 HTTP 请求绑定 run_id 并记录起止日志。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        token = set_request_id(new_request_id())
        started = time.perf_counter()
        logger.bind(
            event="request_start",
            method=request.method,
            path=request.url.path,
            body_bytes=_declared_length(request),
        ).debug("http request started")
        try:
            response = await call_next(request)
        except Exception as e:  # 中间件兜底：记错误后原样抛出，交给 FastAPI 处理
            logger.bind(
                event="request_error",
                status=500,
                error_type=type(e).__name__,
                error_message=str(e)[:200],
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            ).error("http request failed")
            raise
        else:
            logger.bind(
                event="request_done",
                status=response.status_code,
                method=request.method,
                path=request.url.path,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                run_id=get_request_id(),
            ).info("http request finished")
            return response
        finally:
            reset_request_id(token)


def _declared_length(request: Request) -> int:
    """请求体字节数（取客户端声明的 content-length；分块传输时为 0）。"""
    declared = request.headers.get("content-length")
    return int(declared) if declared and declared.isdigit() else 0
