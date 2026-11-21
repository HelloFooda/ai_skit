"""章节切分工具：把「标题 + 正文」同框的多章输入切成逐字保真的章节列表。

## 为什么是工具而不是让 LLM 拆

用户把多章内容贴进同一个输入框。若让模型自己判断边界，它可能在拆分时顺手改标点、
合并章节或重排顺序——这与项目「台词逐字零容错」的红线冲突。这里的做法是：

- 识别边界用确定性正则（只认**行首**的「第X章/第X回/...」标题行）；
- 切分用字符串切片，不重建、不 `strip`、不归一化空白；
- 切完做逐字回拼校验：`"".join(raw) == source`，差一个字就报错交还 400。

模型在这一步没有改写机会。用 `@tool` 注册是为了让后续 agent 节点也能直接调用同一份
实现；LangGraph 的 split 节点则直接调用纯函数，顺序仍由图结构固定。

## 输入格式约定

```
第一章：雨夜来客
正文第一段……

第二章：铜钥匙
正文第二段……
```

标题必须独占一行且位于最前面。识别两类写法：

- `第X章 / 回 / 节 / 部 / 篇 / 集`（中文或阿拉伯数字）；
- 惯用节名：`序章 / 序幕 / 楔子 / 引子 / 终章 / 尾声 / 后记 / 番外`。

标题行允许带装饰前缀（`📖 `、`## ` 等非文字符号），因为从阅读器或笔记软件
直接粘贴时常会带上；前缀随切片原样保留，不影响逐字回拼。正文里出现的
「第三章」若不是独立标题行不会被误切。
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

# 与 llm.py 的单章上限、与响应体容量保持一致
MAX_CHAPTERS = 20
MAX_CHAPTER_CHARS = 20_000

# 只认行首标题：可选装饰前缀 + 第X章/回/节/部/篇/集（或序章/楔子等惯用节名）
# + 可选标题文字，整行结束。
#
# 装饰前缀（`📖 `、`# `、`【` 之类非文字符号）是为了容纳从阅读器 / 笔记软件里
# 直接粘贴的标题行：粘贴出来的标题常带 emoji 或列表符号，而符号不属于原文语义，
# 认作标题行的一部分原样切片即可（raw 仍逐字等于源串，回拼校验不受影响）。
#
# 正文里出现的「第三章的时候他……」不会命中（「的」不是分隔符，且行未结束）。
_HEADING_RE = re.compile(
    r"^[ \t]*(?:[^\w\s]{1,3}[ \t]*)?(?P<title>"
    r"(?:第[零〇一二三四五六七八九十百千万两0-9]+[章节回部篇集]"
    r"|序章|序幕|楔子|引子|终章|尾声|后记|番外)"
    r"(?:[：:、.． \t][^\n]{0,60})?"
    r")[ \t]*$",
    re.MULTILINE,
)


class Chapter(BaseModel):
    """一章的原文切片。raw 是包含标题行在内的完整切片，逐字等于源串对应区间。"""

    index: int = Field(ge=1, description="章节序号，从 1 起")
    title: str = Field(description="标题行原文（含「第X章」），不改写")
    text: str = Field(description="标题行之后的正文，含原有换行，不改写")
    raw: str = Field(description="标题行 + 正文的完整切片，用于回拼校验")


def split_chapters_text(source: str) -> list[Chapter]:
    """确定性切分。任何不确定或会改字的情况都抛 ValueError，由路由转成 400。"""
    if not source or not source.strip():
        raise ValueError("输入为空，请提供带「第X章」标题的小说正文")

    matches = list(_HEADING_RE.finditer(source))
    if not matches:
        raise ValueError(
            "未找到章节标题。请在每个章节前独占一行写「第一章：标题」这样的标题行"
        )

    prefix = source[: matches[0].start()]
    if prefix.strip():
        raise ValueError(
            "第一个「第X章」标题前不能有正文，请把标题放到输入最前面（小说名请填独立字段）"
        )

    if len(matches) > MAX_CHAPTERS:
        raise ValueError(
            f"本次输入含 {len(matches)} 章，超过单次上限 {MAX_CHAPTERS} 章，请分批提交"
        )

    # 第一段的起点取 0，把标题前的空白也纳入切片，保证回拼严格相等。
    starts = [0, *(m.start() for m in matches[1:])]
    ends = [*starts[1:], len(source)]

    chapters: list[Chapter] = []
    for i, (m, start, end) in enumerate(zip(matches, starts, ends, strict=True)):
        raw = source[start:end]
        text = raw[m.end() - start :]
        if not text.strip():
            raise ValueError(f"{m.group('title')} 标题下没有正文")
        if len(text) > MAX_CHAPTER_CHARS:
            raise ValueError(
                f"{m.group('title')} 正文 {len(text)} 字，超过单章上限 {MAX_CHAPTER_CHARS} 字"
            )
        chapters.append(
            Chapter(index=i + 1, title=m.group("title"), text=text, raw=raw)
        )

    if "".join(c.raw for c in chapters) != source:
        # 理论上不可达：raw 全部由源串切片得到。留作断言，防止将来改动引入改字。
        raise ValueError("章节回拼与原文不一致，已拒绝本次输入")
    return chapters


@tool("split_chapters", parse_docstring=True)
def split_chapters_tool(text: str) -> dict[str, Any]:
    """按行首「第X章」标题切分多章小说输入，并做逐字回拼校验。

    Args:
        text: 小说名以外、由「章节标题 + 正文」组成的原始输入，逐字不改。

    Returns:
        dict: count 为章数，verbatim_ok 为回拼是否严格相等，chapters 为逐章标题与正文。
    """
    chapters = split_chapters_text(text)
    return {
        "count": len(chapters),
        "verbatim_ok": "".join(c.raw for c in chapters) == text,
        "chapters": [
            {"index": c.index, "title": c.title, "text": c.text} for c in chapters
        ],
    }
