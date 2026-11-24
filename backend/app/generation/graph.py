"""统一编排图：小说名 + 多章原文 → 逐章五类产出。

## 三层分工（从外到内）

1. **graph.py（本模块）**：多章循环——切章 → 逐章生成 → 聚合，顺序由图边保证。
2. **agent.py**：章内路径——用 `create_agent` 让模型自选工具（盘资产 / 写分镜），
   顺序依赖由 `wrap_tool_call` 中间件守；模型决定"走哪条路"，代码守"哪条路合法"。
3. **llm.py**：每段生成的具体实现——提示词、LCEL 链、校验、机检，完全复用不改。

## 为什么外层仍是 LangGraph

项目后续还有文生图、生视频，它们与文生文之间是**固定先后依赖**：
资产 → 分镜 → 图片提示词 → 图片 → 视频提示词 → 视频。若每个阶段都在函数入口写
「前置必须存在 XX」，约束会散落在 N 处且顺序一改就要改 N 处。StateGraph 把顺序
集中表达成节点和边，新增阶段只加节点加边，不动现有节点。

```text
START → split ─→ generate ─┬─→ generate （还有下一章，回环）
                            └─→ finalize → END
                                └─ agent.invoke（章内：模型自选工具 + 顺序闸门）
```
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from loguru import logger

from . import agent as agent_module
from .chapters import split_chapters_tool

# 单章生成结果在状态里以普通 dict 传递（不含 Pydantic 模型之外的不可序列化对象），
# 将来要接 checkpointer 做断点续跑时无需改节点签名。
ChapterResult = dict[str, Any]

# 进度文案集中在这里，前端只渲染、不拼装。stream_mode="custom" 下每个节点
# 发一条 dict；非流式 invoke 下 get_stream_writer() 是 no-op，零副作用。
PROGRESS = {
    "split_start": "正在识别章节边界…",
    "split_done": "章节切分完成，共 {n} 章",
    "chapter_start": "正在生成第 {i}/{n} 章《{title}》：盘资产 → 写站位与分镜",
    "chapter_done": "第 {i}/{n} 章《{title}》生成完成",
    "finalize_start": "正在汇总各章产出与成本…",
    "archive_start": "正在构建生图提示词并归档资产…",
    "done": "全部完成",
}


def _progress(phase: str, text: str, step: int, total: int) -> None:
    """发一条进度事件；invoke 路径下 writer 是 no-op，不影响任何现有调用。"""
    get_stream_writer()({"phase": phase, "text": text, "step": step, "total": total})


class NovelGraphState(TypedDict, total=False):
    novel_title: str
    source_text: str
    params: dict[str, Any]
    run_id: str

    chapters: list[dict[str, Any]]
    chapter_index: int
    # 累加通道：节点只返回本章这一条，由图的 reducer 追加，节点里不再手写
    # `[*state["results"], item]`（并发分支接进来时也不会互相覆盖）。
    results: Annotated[list[ChapterResult], operator.add]
    final: dict[str, Any]


def split_node(state: NovelGraphState) -> dict[str, Any]:
    """把多章原文切成逐字保真的章节列表，并复位游标。"""
    logger.bind(
        event="graph_split",
        run_id=state.get("run_id", "-"),
        chars=len(state["source_text"]),
    ).debug("chapter split start")
    _progress("split", PROGRESS["split_start"], 0, 0)  # 章数未知，total=0 交给前端做不确定态
    payload = split_chapters_tool.invoke({"text": state["source_text"]})
    if not payload["verbatim_ok"]:
        # 与 chapters.py 的断言双保险：回拼不等就绝不进入模型调用
        raise ValueError("章节切分后逐字回拼失败，已拒绝本次输入")
    chapters = payload["chapters"]
    logger.bind(
        event="graph_split",
        run_id=state.get("run_id", "-"),
        count=len(chapters),
    ).debug("chapter split done")
    total = len(chapters) + 3  # split + 每章 + finalize + archive
    _progress("split", PROGRESS["split_done"].format(n=len(chapters)), 1, total)
    return {"chapters": chapters, "chapter_index": 0, "results": []}


# 章内生成的唯一入口。测试 patch 这个符号即可替换整章 agent，不必构造假模型。
generate_chapter = agent_module.run_chapter_agent


def generate_node(state: NovelGraphState) -> dict[str, Any]:
    """生成当前游标指向的一章；节点内部交给章内 agent（模型自选工具路径）。"""
    index = state["chapter_index"]
    chapter = state["chapters"][index]
    n = len(state["chapters"])
    total = n + 3
    log = logger.bind(
        event="graph_chapter_start",
        run_id=state.get("run_id", "-"),
        chapter_index=index + 1,
        chapter_title=chapter["title"],
        chars=len(chapter["text"]),
    )
    log.debug("chapter generation start")
    _progress(
        "chapter",
        PROGRESS["chapter_start"].format(i=index + 1, n=n, title=chapter["title"]),
        index + 2,  # split 占第 1 步，第 i 章从第 2 步起
        total,
    )

    result = generate_chapter(
        chapter["text"], state["params"], run_id=state.get("run_id", "-")
    )
    pkg = result["package"]
    item: ChapterResult = {
        "index": index + 1,
        "title": chapter["title"],
        "text_chars": len(chapter["text"]),
        "package": pkg,
        "latency_ms": result["latency_ms"],
        "prompt_version": result["prompt_version"],
        "mode": result["mode"],
        "llm_calls": result["llm_calls"],
        "agent_calls": result.get("agent_calls", 0),
        "hints_used": list(result["hints_used"]),
        "injection_warning": result["injection_warning"],
        "skills_loaded": list(result["skills_loaded"]),
        "skills_skipped": list(result["skills_skipped"]),
        "skills_refs_expanded": result["skills_refs_expanded"],
        "skills_refs_skipped": result["skills_refs_skipped"],
        "stages": list(result["stages"]),
        "gate_issues": list(result["gate_issues"]),
        "total_groups_estimated": result["total_groups_estimated"],
        "truncated": result["truncated"],
    }
    logger.bind(
        event="graph_chapter_done",
        run_id=state.get("run_id", "-"),
        chapter_index=index + 1,
        llm_calls=result["llm_calls"],
        agent_calls=result.get("agent_calls", 0),
        latency_ms=result["latency_ms"],
        characters=len(pkg.characters),
        props=len(pkg.props),
        scenes=len(pkg.scenes),
        storyboards=len(pkg.storyboards),
        tool_trace=result.get("tool_trace", []),
    ).info("chapter generation done")
    _progress(
        "chapter",
        PROGRESS["chapter_done"].format(i=index + 1, n=n, title=chapter["title"]),
        index + 2,
        total,
    )
    return {"chapter_index": index + 1, "results": [item]}


def route_after_generate(state: NovelGraphState) -> str:
    """回环条件：游标还没走完章节就再进 generate，否则去 finalize。"""
    return "generate" if state["chapter_index"] < len(state["chapters"]) else "finalize"


def finalize_node(state: NovelGraphState) -> dict[str, Any]:
    """聚合逐章结果，保留每章独立元数据，便于按章核对成本与机检。"""
    results = state["results"]
    _progress("finalize", PROGRESS["finalize_start"], len(results) + 2, len(results) + 3)
    final = {
        "novel_title": state["novel_title"],
        "chapter_count": len(results),
        "chapters": results,
        "total_llm_calls": sum(r["llm_calls"] for r in results),
        "total_agent_calls": sum(r.get("agent_calls", 0) for r in results),
        "total_latency_ms": sum(r["latency_ms"] for r in results),
        "total_characters": sum(len(r["package"].characters) for r in results),
        "total_props": sum(len(r["package"].props) for r in results),
        "total_scenes": sum(len(r["package"].scenes) for r in results),
        "total_storyboards": sum(len(r["package"].storyboards) for r in results),
        "injection_warning": any(r["injection_warning"] for r in results),
        "gate_issue_count": sum(len(r["gate_issues"]) for r in results),
    }
    logger.bind(
        event="graph_done",
        run_id=state.get("run_id", "-"),
        chapters=final["chapter_count"],
        llm_calls=final["total_llm_calls"],
        agent_calls=final["total_agent_calls"],
        latency_ms=final["total_latency_ms"],
        gate_issues=final["gate_issue_count"],
    ).info("novel pipeline done")
    return {"final": final}


def build_novel_graph():
    """构建并编译编排图。每次请求调用一次；编译结果无状态，可安全复用。"""
    graph = StateGraph(NovelGraphState)
    graph.add_node("split", split_node)
    graph.add_node("generate", generate_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "split")
    graph.add_edge("split", "generate")
    graph.add_conditional_edges(
        "generate",
        route_after_generate,
        {"generate": "generate", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)
    return graph.compile()


def run_novel_pipeline(
    novel_title: str,
    source_text: str,
    params: dict[str, Any] | None = None,
    *,
    run_id: str = "-",
) -> dict[str, Any]:
    """路由层唯一入口：返回聚合后的 final 字典。"""
    compiled = build_novel_graph()
    outcome = compiled.invoke(
        {
            "novel_title": novel_title,
            "source_text": source_text,
            "params": params or {},
            "run_id": run_id,
        }
    )
    return outcome["final"]


def stream_novel_pipeline(
    novel_title: str,
    source_text: str,
    params: dict[str, Any] | None = None,
    *,
    run_id: str = "-",
):
    """同步生成器：产出 ("progress", 事件dict) 与 ("final", final字典) 两类元组。

    与 `run_novel_pipeline` 共用同一份编译图，靠 `stream_mode=["custom", "values"]`
    同时收集节点内 `get_stream_writer()` 发的进度，以及最后一块的 `final`。
    `values` 块本身不外发（含整章产出，体积大且属内容，进度流只报阶段）。
    """
    compiled = build_novel_graph()
    final: dict[str, Any] | None = None
    for mode, chunk in compiled.stream(
        {
            "novel_title": novel_title,
            "source_text": source_text,
            "params": params or {},
            "run_id": run_id,
        },
        stream_mode=["custom", "values"],
    ):
        if mode == "custom":
            yield "progress", chunk
        elif isinstance(chunk, dict) and "final" in chunk:
            final = chunk["final"]
    yield "final", final
