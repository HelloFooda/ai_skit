"""token 用量提取：同一个 `LLMResult` 只解析一遍。

原先两处各写一份：`chains.UsageCollector` 汇总进响应的 `stages[].usage`，
`logging.LoggingCallback` 写进落盘日志。两份逻辑一旦分叉（比如一边先取
`usage_metadata`、另一边先看 `llm_output`），响应里的数字和日志里的数字就会对不上，
排查成本比省下的几行高得多。所以提取规则收在这里，两边都调它。

## 优先级

1. `generations[].message.usage_metadata`（LangChain 1.x 标准字段）；
2. 回退 `llm_output["token_usage"]`（只填这一处的老集成包）。

两条路径**互斥**：只要有一条 generation 带了 `usage_metadata` 就只用它，不与
`llm_output` 累加——两者同时有值时相加会翻倍。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.outputs import LLMResult


@dataclass(frozen=True)
class TokenUsage:
    """一次（或一批）调用的 token 用量；`found=False` 表示模型没报用量。"""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    found: bool = False


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _total(raw: Any, input_tokens: int, output_tokens: int) -> int:
    """没给 total 时用 in+out 兜底（部分兼容端点只报前两项）。"""
    return _int(raw) if raw is not None else input_tokens + output_tokens


def token_usage(response: LLMResult) -> TokenUsage:
    """从 `LLMResult` 提取用量，多处来源按上面的优先级合并。"""
    input_tokens = output_tokens = total_tokens = 0
    found = False
    for generations in response.generations:
        for generation in generations:
            metadata = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if not metadata:
                continue
            found = True
            in_t = _int(metadata.get("input_tokens"))
            out_t = _int(metadata.get("output_tokens"))
            input_tokens += in_t
            output_tokens += out_t
            total_tokens += _total(metadata.get("total_tokens"), in_t, out_t)
    if found:
        return TokenUsage(input_tokens, output_tokens, total_tokens, True)

    usage = (response.llm_output or {}).get("token_usage") or {}
    if not usage:
        return TokenUsage()
    in_t = _int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    out_t = _int(usage.get("completion_tokens") or usage.get("output_tokens"))
    return TokenUsage(in_t, out_t, _total(usage.get("total_tokens"), in_t, out_t), True)
