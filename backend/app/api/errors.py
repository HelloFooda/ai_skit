"""路由层异常 → HTTP 响应的唯一出口。

## 为什么不在每个路由里写 try/except

两个生成端点原来各自抄了三段 except（技能名不存在 → 400、ValueError → 400、
其余 → 500），日志字段同一份、detail 前缀却各写各的。而「异常类型 → 状态码」
本来就是**全局约定**，不是某个端点的局部逻辑：注册在 FastAPI 的 exception_handler
上一处定义，新增端点自动继承，不会再各抄一遍。

## 边界

- 4xx 由 `ExceptionMiddleware` 就地处理，不会冒泡到请求日志中间件的 except 分支，
  所以这里自己记一条 warning（带 path，便于看出是哪个接口被写错参数）。
- `Exception` 的处理器挂在 `ServerErrorMiddleware` 上：响应体统一成 JSON，但 Starlette
  仍会把异常继续抛出（TestClient 能看到原始异常）；耗时与错误类型已由请求日志中间件
  记下，这里不重复记一条 5xx，免得一次故障两条 error。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from ..knowledge.skills import UnknownSkillError


def _respond(
    request: Request, status: int, exc: Exception, *, detail: str | None = None
) -> JSONResponse:
    if status < 500:
        logger.bind(
            event="request_error",
            status=status,
            path=request.url.path,
            error_type=type(exc).__name__,
        ).warning("bad request")
    return JSONResponse(status_code=status, content={"detail": detail or str(exc)})


async def _bad_skill(request: Request, exc: UnknownSkillError) -> JSONResponse:
    """技能名写错属于请求问题，给 400，不要伪装成服务端故障。"""
    return _respond(request, 400, exc)


async def _bad_argument(request: Request, exc: ValueError) -> JSONResponse:
    """参数语义问题（章节格式、visual_only 缺 assets、逐字回拼失败）同样是请求错误。"""
    return _respond(request, 400, exc)


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    """兜底：LLM / 解析 / 校验异常统一 500，不向前端透堆栈。"""
    return _respond(request, 500, exc, detail=f"服务端处理失败：{exc}")


def register_error_handlers(app: FastAPI) -> None:
    """按「具体 → 宽泛」注册；Starlette 按 MRO 就近匹配，UnknownSkillError 先命中。"""
    app.add_exception_handler(UnknownSkillError, _bad_skill)
    app.add_exception_handler(ValueError, _bad_argument)
    app.add_exception_handler(Exception, _unhandled)
