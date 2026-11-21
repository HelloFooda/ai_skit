"""可观测层：分级日志 + 请求关联 id + LangChain 回调。

## 记什么、不记什么

| 记 | 不记 |
| --- | --- |
| run_id / stage / mode / prompt_version / model | 章节原文（不可信输入，逐字零容错） |
| system / user / skill 的**字符数**与 hash | 任何消息正文（system 规则、user 素材都不写） |
| input / output token、耗时、错误类型 | api_key、base_url 里的凭据 |
| 五类产出计数、机检告警数 | 后端堆栈里可能夹带的请求体 |

章节原文只以「长度 + 哈希」形态出现，既能对比两次调用喂进去的提示词是否相同，
又不会把用户素材落盘。密钥从不进本模块任何变量。

## 谁来初始化

FastAPI 入口 `main.py` 在导入时调 `configure_logging()`。直接把 `app.llm` 当库用
（脚本 / 定时任务，不经过 main）时，调用方要自己调一次 `configure_logging()`，
否则只有 loguru 的默认 stderr 输出、没有分级格式与文件 sink。这是显式边界，
避免在库导入时产生副作用。

## 为什么是 loguru

标准库 logging 要自己配 Formatter / Rotation / 上下文过滤器；loguru 一个
`logger.configure(patcher=...)` 就能给每条记录注入 run_id，`serialize=True` 直接
出 JSON 行，`rotation` / `retention` 内置。分级、轮转、结构化都不用自己写。

## 与 UsageCollector 的分工

`UsageCollector` 把 token 汇总进 HTTP 响应 `stages[].usage`（给前端看）；
本模块的 `LoggingCallback` 把同一个 `on_llm_end` 事件写成落盘日志（给人排查）。
两者都挂在 `RunnableConfig.callbacks` 上，互不干扰，同一模型调用各取所需；
**用量提取规则只在 `observability/usage.py` 一处**，避免响应与日志的数字对不上。
"""

from __future__ import annotations

import contextvars
import hashlib
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from loguru import logger

from ..core.config import settings
from .usage import token_usage

# 请求关联 id：HTTP 中间件在入口 set，链上回调在任意深度读，响应也带回同一个值。
# 用 ContextVar 而非函数参数贯穿，是为了不改 extract_assets / _call_once 的签名。
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ai_skit_request_id", default="-"
)

# sink 状态：配一次，重复调用 configure_logging 时先清空再加，避免单测里 sink 叠加。
_sink_ids: list[int] = []


def new_request_id() -> str:
    """生成 12 位十六进制请求 id，与 pipeline 路由原来的口径一致。"""
    import uuid

    return uuid.uuid4().hex[:12]


def set_request_id(value: str) -> contextvars.Token[str]:
    """绑定请求 id，返回的 token 供请求结束时 reset（防止跨请求串号）。"""
    return _request_id.set(value)


def reset_request_id(token: contextvars.Token[str]) -> None:
    _request_id.reset(token)


def get_request_id() -> str:
    return _request_id.get()


def _digest(text: str) -> str:
    """只输出内容指纹，不输出内容本身。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _patch_record(record: dict[str, Any]) -> None:
    """给每条日志注入当前请求 id；无请求上下文时显示 '-'。"""
    record["extra"]["run_id"] = _request_id.get()


def configure_logging() -> None:
    """按配置初始化 loguru：stderr 必开，log_dir 非空时追加 JSON 文件 sink。

    幂等：重复调用先移除旧 sink。level 用大写匹配 loguru 的分级名；写错等级名时
    loguru 会抛错，这里不吞——配置错误应在启动期暴露而不是静默降级。
    """
    global _sink_ids
    logger.remove()
    _sink_ids = []
    logger.configure(patcher=_patch_record)

    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{extra[run_id]}</cyan> | "
        "<level>{message}</level>"
    )
    _sink_ids.append(logger.add(sys_stderr(), level=settings.log_level.upper(), format=fmt))

    if settings.log_dir:
        from pathlib import Path

        log_path = Path(settings.log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        _sink_ids.append(
            logger.add(
                log_path / "ai_skit.log",
                level=settings.log_level.upper(),
                serialize=True,  # 一行一个 JSON 对象，便于 grep / jq 分析
                rotation=settings.log_rotate,
                retention=settings.log_retention,
                encoding="utf-8",
            )
        )


def sys_stderr():
    """返回 stderr 句柄；单独成函数便于测试注入 StringIO。"""
    import sys

    return sys.stderr


def reset_sinks() -> None:
    """移除本模块注册的 sink（测试 teardown 用），不动外部 sink。"""
    global _sink_ids
    for sink_id in _sink_ids:
        try:
            logger.remove(sink_id)
        except ValueError:
            pass
    _sink_ids = []


class LoggingCallback(BaseCallbackHandler):
    """把每次模型调用的起止写成 DEBUG / ERROR 日志。

    只从 `prompts` 取**字符数与哈希**，`serialized` 里也只取模型名——LangChain 传给
    `on_llm_start` 的 prompts 是拼好的 system+user 全文，里面就有章节原文，绝不
    写进日志正文。stage / mode / prompt_version 从 metadata 读（run_config 已放好）。
    """

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        meta = metadata or {}
        lengths = [len(p) for p in prompts]
        logger.bind(
            event="llm_start",
            stage=meta.get("stage", "?"),
            mode=meta.get("mode", "?"),
            prompt_version=meta.get("prompt_version", "?"),
            model=meta.get("model", "?"),
            provider=meta.get("provider", "?"),
            messages=len(prompts),
            chars=sum(lengths),
            chars_each=lengths,
            prompt_sha256=[_digest(p) for p in prompts],
        ).debug("model call started")

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        meta = metadata or {}
        usage = token_usage(response)
        logger.bind(
            event="llm_end",
            stage=meta.get("stage", "?"),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        ).debug("model call finished")

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        # 只记异常类型与消息前缀，不记完整堆栈：堆栈里的局部变量可能带请求体。
        message = str(error)[:200]
        logger.bind(
            event="llm_error",
            stage=(metadata or {}).get("stage", "?"),
            error_type=type(error).__name__,
            error_message=message,
        ).error("model call failed")
