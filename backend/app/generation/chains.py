"""LangChain 调用层：模型工厂 + LCEL 链 + 按 stage 的 token 用量采集。

## 这一层负责什么

| 关注点 | 原来（原生 OpenAI SDK） | 现在 |
| --- | --- | --- |
| 换厂商 | 改 `base_url`（只覆盖 OpenAI 兼容端点） | `LLM_PROVIDER` 统一走 `init_chat_model`，兼容端点额外带 `base_url` |
| 超时 / 重试 | 无 | 模型工厂里的 `request_timeout` / `max_retries` |
| 并发 | `ThreadPoolExecutor(max_workers=3)` | `Runnable.batch` + `RunnableConfig.max_concurrency` |
| 可观测 | 只有耗时 | `UsageCollector` 回调采集 token，按 stage 归因进 `stages` |
| 链路标识 | 无 | `run_name` / `tags` / `metadata`，接 LangSmith / Langfuse 不用改业务代码 |

## 刻意不做的事

1. **不上 `with_structured_output`**。现有输出契约是「模型返回纯 JSON 文本 +
   `_parse_json` 容错 + Pydantic 强校验」；改成 function calling / JSON schema
   会让 DeepSeek 这类兼容端点（对 tool call 支持参差）直接不可用，也改变了
   「只返回 JSON 对象」这条提示词口径。要上必须先在目标模型上实测并跑评测基线。
2. **不把章节原文塞进模板引擎**。见 `prompts.py` 的说明：`_to_messages`
   只做 role → Message 的映射，content 原样透传，一个字都不碰。
3. **不在链层叠加 `with_retry`**。`ChatOpenAI` 自带 `max_retries`，再包一层
   `Runnable.with_retry()` 会指数级放大重试次数、烧掉额度。重试只在模型层做一次。
4. **密钥不进 cache key、不进 metadata、不进日志**。缓存键里只放密钥指纹。
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any
from uuid import UUID

from langchain.chat_models import init_chat_model
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import LLMResult
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda

from ..core.config import settings
from ..observability.logging import LoggingCallback, get_request_id
from ..observability.usage import token_usage

# 业务侧消息是 list[dict]，在这里一次性转成 LangChain Message。
# 未知 role 直接报错，不静默降级成 HumanMessage（那会改变规则/素材的权限层级）。
_ROLE_MAP = {
    "system": SystemMessage,
    "user": HumanMessage,
    "assistant": AIMessage,
}


def clear_model_cache() -> None:
    """配置变化后清空模型与链缓存（测试 / 运行时热更新用）。"""
    _cached_model.cache_clear()
    _cached_chain.cache_clear()


def _fingerprint(secret: str) -> str:
    """只取指纹，绝不让明文密钥成为可比较对象或出现在日志里。"""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


def _model_key() -> tuple[object, ...]:
    """配置快照即缓存键；密钥只进指纹，明文不进 key、不进日志。"""
    return (
        settings.provider,
        settings.model,
        settings.base_url,
        _fingerprint(settings.api_key),
        settings.temperature,
        settings.timeout,
        settings.max_retries,
    )


@lru_cache(maxsize=8)
def _cached_model(key: tuple[object, ...]) -> BaseChatModel:
    """按配置快照缓存模型对象：避免每个请求重建 HTTP client / 连接池。

    改 .env 后重启进程即生效；测试里 patch settings 后需调用 clear_model_cache()。
    """
    kwargs: dict[str, Any] = {
        "api_key": settings.api_key,
        "temperature": settings.temperature,
        "timeout": settings.timeout,
        "max_retries": settings.max_retries,
    }
    if settings.provider == "openai":
        # 兼容端点的自定义地址；其它厂商交给集成包自己的默认地址。
        kwargs["base_url"] = settings.base_url
    return init_chat_model(settings.model, model_provider=settings.provider, **kwargs)


def build_chat_model() -> BaseChatModel:
    """按配置装配 ChatModel。业务代码不写死厂商。

    统一走 `init_chat_model` 按 provider 名装配，只有 `base_url` 需要分流：
    它是 OpenAI 兼容端点（DeepSeek / 通义 / vLLM / Ollama）专属参数，传给
    `anthropic` / `google_genai` 之类会变成各 SDK 不认识的 kwarg。
    非 openai 的 provider 需另装对应集成包（`langchain-anthropic`、
    `langchain-google-genai` 等）。
    """
    return _cached_model(_model_key())


def _to_messages(payload: list[dict[str, str]]) -> list[BaseMessage]:
    """dict 消息 → LangChain Message。content 原样透传，绝不清洗/截断。

    章节原文在这里经过但**不经过模板引擎**：它是不可信素材，逐字零容错。
    """
    messages: list[BaseMessage] = []
    for item in payload:
        role = item["role"]
        if role not in _ROLE_MAP:
            raise ValueError(f"未知消息 role：{role!r}")
        messages.append(_ROLE_MAP[role](content=item["content"]))
    return messages


def _to_text(message: Any) -> str:
    """AIMessage → 纯文本；兼容部分模型返回 content blocks 的情况。"""
    if isinstance(message, AIMessage):
        content = message.content
        if isinstance(content, str):
            return content
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") in (None, "text")
        )
    return message if isinstance(message, str) else str(message)


@lru_cache(maxsize=8)
def _cached_chain(key: tuple[object, ...]) -> Runnable[list[dict[str, str]], str]:
    """链对象按同一份配置快照缓存：链是无状态的，重建只是浪费。"""
    return (
        RunnableLambda(_to_messages).with_config(run_name="ai_skit.to_messages")
        | build_chat_model()
        | RunnableLambda(_to_text).with_config(run_name="ai_skit.to_text")
    )


def build_chain() -> Runnable[list[dict[str, str]], str]:
    """LCEL 链：dict 消息 → Message → ChatModel → 纯文本。

    输出仍交给 `llm._parse_json` 做容错解析 + Pydantic 强校验，
    所以提示词口径与输出契约一点没变（见模块 docstring 第 1 条）。
    """
    return _cached_chain(_model_key())


class UsageCollector(BaseCallbackHandler):
    """采集一次 / 一批调用的 token 用量，供 `stages` 按段归因。

    提取规则在 `observability.usage`（与落盘日志共用一份），这里只负责累加。
    """

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.calls = 0
        self.errors = 0

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self.calls += 1
        usage = token_usage(response)
        if usage.found:
            self.input_tokens += usage.input_tokens
            self.output_tokens += usage.output_tokens
            self.total_tokens += usage.total_tokens

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self.errors += 1

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "llm_calls": self.calls,
            "llm_errors": self.errors,
        }


def run_config(
    *,
    stage: str,
    mode: str,
    prompt_version: str,
    collector: UsageCollector | None = None,
    max_concurrency: int | None = None,
) -> RunnableConfig:
    """统一的运行级配置：链路标识 + 回调 + 并发上限。

    metadata 里只放非敏感的定位信息，方便接 LangSmith / Langfuse 时按
    stage / mode / prompt_version 分组对比，不含 api_key 与章节原文。
    """
    config: RunnableConfig = {
        "run_name": f"ai_skit.{stage}",
        "tags": ["ai_skit", f"stage:{stage}", f"prompt:{prompt_version}"],
        "metadata": {
            "stage": stage,
            "mode": mode,
            "prompt_version": prompt_version,
            "model": settings.model,
            "provider": settings.provider,
            "run_id": get_request_id(),
        },
    }
    # 两个回调各司其职：UsageCollector 汇总进响应，LoggingCallback 落盘给人排查。
    callbacks: list[Any] = [LoggingCallback()]
    if collector is not None:
        callbacks.append(collector)
    config["callbacks"] = callbacks
    if max_concurrency is not None:
        config["max_concurrency"] = max_concurrency
    return config
