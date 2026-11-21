"""文生文核心：两段串行调用 + 结构化校验 + 代码机检。

## 分层（迁移到 LangChain 之后）

- `prompts.py`：两套 system 规则层，用 `ChatPromptTemplate`(jinja2) 渲染。
  提示词正文与迁移前**逐字相同**，`PROMPT_VERSION` 因此不升版。
- `chains.py`：模型工厂（配置驱动换厂商）、LCEL 链、`RunnableConfig`
  （超时/重试/标签/并发上限）、`UsageCollector`（token 按 stage 归因）。
- `llm.py`（本文件）：只做业务编排——消息组装、两段执行、元数据。
  章节原文仍由本文件逐字拼进 user 消息，**不进任何模板引擎**。
  mock 样例数据在 `mock_data.py`，不在本文件里。

## 为什么是两段，而不是一次调用出五类

五类产出在 AGENTS.md 与技能包里分属两个阶段：
- 角色 / 角色道具 / 场景 = S2 视觉设定（`$short-drama-assets` → `视觉设定.md`）
- 站位 / 分镜组表        = S4 分镜（`$short-drama-storyboard` → `分镜.md`）

`short-drama-assets` 的 `stage-contract` 写明本阶段「不越界决定单镜临时动作、左右手拿握、
构图景别、动作终点或生图/视频提示词」，技能正文也写着「机位走位交由下游」。所以站位与分镜
不能塞进资产那一次调用，否则就是让模型在设定还没定的时候越界写镜头层。

更硬的理由是**一致性**：想让分镜引用同一批角色名、道具名、地点名，就必须让第二段**看到**
第一段的结果——所以两段串行，第一段的资产表作为第二段的输入。代价 2 次调用。

## 五类在数据上只有四处来源

站位不是第五份独立内容，它是分镜里 `shots[].blocking` 的按组聚合（`build_blockings`）。
分镜把站位写进每镜的起点/终点；单独生成一遍会写两处、互相矛盾，聚合出来则永远一致。

## 提示词分层（v7）

- system：角色 / 硬性规则 / 技能规范 / 输出契约 / 输入可信度声明。稳定不变，
  由 `prompts.py` 的 ChatPromptTemplate 渲染（只渲染可信的规则层）。
- user  ：章节原文（不可信素材）+ 分类补充要求 + （第二段）已定稿资产表。
  由本文件直接拼字符串，不经模板引擎，保证逐字零容错。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from langchain_core.runnables import Runnable

from ..core.config import settings
from ..core.schemas import (
    Assets,
    Character,
    GateIssue,
    Prop,
    Scene,
    ScriptPackage,
    StoryboardGroup,
    build_blockings,
    check_storyboards,
)
from ..knowledge.skills import SkillSection, build_skill_section, catalog
from . import chains, prompts
from .mock_data import mock_package

# 改提示词正文就升版本号，响应里会带回来，方便回归评测与 A/B 归因。
# 迁到 ChatPromptTemplate 只换载体、正文逐字未变，所以仍是 v7。
PROMPT_VERSION = "v7"

# 资产三类（第一段）与视觉两类（第二段）——技能装载按这两组分别匹配
_ASSET_KEYS = ("characters", "props", "scenes")
_VISUAL_KEYS = ("storyboard", "blocking")

# 类别词表与输出契约在 prompts.py（规则层），这里只留别名给编排层用
_CATEGORIES = prompts.CATEGORIES

# 章节原文是「不可信输入」，命中这些模式只告警、不改写、不拦截（小说里可能真有这种台词）
_INJECTION_PATTERNS = [
    r"忽略(上面|之前|以上|前述|下列)的?(指令|要求|提示|规则|所有)",
    r"ignore\s+(previous|above|all|prior)\s+instructions",
    r"(你现在是|从现在开始你是|请扮演)",
    r"system\s*[:：]",
    r"</?\s*(chapter_text|user_hints|hint|asset_table)\b",
]


def detect_injection(text: str) -> bool:
    """轻量注入探测：只用于给调用方一个可见的 warning，不阻断、不改动原文。"""
    return any(re.search(p, text, re.IGNORECASE) for p in _INJECTION_PATTERNS)


def _asset_system_prompt(only: str | None, skill_text: str = "") -> str:
    """第一段 system。兼容旧调用点；渲染交给 prompts.py 的 ChatPromptTemplate。"""
    return prompts.render_asset_system(only, skill_text)


def _visual_system_prompt(
    skill_text: str = "",
    max_groups: int = 6,
    target_duration: float = 15.0,
    scene_filter: Sequence[str] | None = None,
) -> str:
    """第二段 system。兼容旧调用点；渲染交给 prompts.py 的 ChatPromptTemplate。"""
    return prompts.render_visual_system(
        skill_text, max_groups, target_duration, scene_filter
    )


def _hint_blocks(hints: dict[str, str], only: str | None) -> list[str]:
    blocks: list[str] = []
    for key, (label, _) in _CATEGORIES.items():
        if only and key != only:
            continue
        text = (hints.get(key) or "").strip()
        if not text:
            continue
        blocks.append(f'<hint category="{key}" label="{label}">\n{text}\n</hint>')
    return blocks


def build_messages(
    chapter: str,
    hints: dict[str, str] | None = None,
    *,
    only: str | None = None,
    skill_text: str = "",
) -> list[dict[str, str]]:
    """第一段（盘资产）的消息组装。纯函数。

    - 章节原文逐字放入 <chapter_text>，不做任何清洗或截断（对账基准不可改字）。
    - 补充要求各自包在带 category 的 <hint> 里，模型据此限定作用域。
    - 技能规范进 system（规则层），不进 user（素材层）。
    - 末尾重述一次输出要求，对抗长章节带来的「中间迷失」。
    """
    hints = hints or {}
    parts = [f"<chapter_text>\n{chapter}\n</chapter_text>"]

    blocks = _hint_blocks(hints, only)
    if blocks:
        parts.append("<user_hints>\n" + "\n\n".join(blocks) + "\n</user_hints>")

    parts.append("请严格按系统提示的输出契约，只返回 JSON 对象。")

    return [
        {"role": "system", "content": _asset_system_prompt(only, skill_text)},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def build_visual_messages(
    chapter: str,
    assets: Assets,
    *,
    skill_text: str = "",
    max_groups: int = 6,
    target_duration: float = 15.0,
    scene_filter: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    """第二段（站位 + 分镜）的消息组装。纯函数。

    关键在 <asset_table>：把第一段的资产表原样交给模型，它才有依据保证
    「分镜里出现的名字必须是资产表里已有的」。没有这一段，两段调用就白拆了。

    scene_filter：长章分片用。一章常常需要 30–60 组，而单次上限 20 组，
    不分场就永远只拍得到开头那一场；点了场次之后，同一份资产表可以接着拆后面几场。
    """
    table = assets.model_dump_json(indent=2)
    parts = [
        f"<chapter_text>\n{chapter}\n</chapter_text>",
        f"<asset_table>\n{table}\n</asset_table>",
    ]
    if scene_filter:
        parts.append(
            "<scope>\n本次只拆场次：" + "、".join(scene_filter) + "\n</scope>"
        )
    parts.append(
        f"请按系统提示的输出契约，只返回 JSON 对象；本次最多输出 {max_groups} 组。"
    )
    return [
        {
            "role": "system",
            "content": _visual_system_prompt(
                skill_text, max_groups, target_duration, scene_filter
            ),
        },
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def _parse_json(text: str) -> dict:
    """容忍模型常见的两种跑偏：```json 围栏、JSON 前后带解释文字。"""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"LLM 输出里找不到 JSON 对象：{text[:120]!r}") from None
        return json.loads(text[start : end + 1])


# ---------------------------------------------------------------- LangChain 通道

LLMChain = Runnable[list[dict[str, str]], str]


def _client() -> LLMChain:
    """返回 LCEL 链（模型工厂 / 消息映射 / 输出转文本都在 chains.py）。

    保留这个注入点：测试可以整条链替换成假实现，不必碰网络与凭据。
    """
    return chains.build_chain()


def _call_once(
    client: LLMChain,
    messages: list[dict[str, str]],
    *,
    stage: str,
    mode: str,
    collector: chains.UsageCollector | None = None,
) -> dict:
    """一次调用 = 一条 LCEL 链 + 统一 RunnableConfig + JSON 容错解析。

    输出契约没变：仍是「模型返回纯 JSON 文本 → _parse_json → Pydantic 强校验」，
    没有改成 function calling（原因见 chains.py 模块 docstring）。
    """
    raw = client.invoke(
        messages,
        config=chains.run_config(
            stage=stage,
            mode=mode,
            prompt_version=PROMPT_VERSION,
            collector=collector,
        ),
    )
    return _parse_json(raw)


# ---------------------------------------------------------------- 技能分流

def _split_named_skills(
    names: Sequence[str] | None,
) -> tuple[list[str] | None, list[str] | None]:
    """把用户点名的技能按类目分到两段，避免「点了分镜技能也被塞进资产段」。

    规则：只声明视觉类（storyboard/blocking）的进第二段；只声明资产类的进第一段；
    其余（未声明类别、两类都沾、global）两段都装——用户既然点了，就按点了算。
    """
    if not names:
        return None, None
    cat_map = {item["name"]: set(item["categories"]) for item in catalog(None)}
    asset: list[str] = []
    visual: list[str] = []
    for name in names:
        cats = cat_map.get(name, set())
        in_visual = bool(cats & set(_VISUAL_KEYS))
        in_asset = bool(cats & set(_ASSET_KEYS))
        if in_asset or not in_visual:
            asset.append(name)
        if in_visual or not in_asset:
            visual.append(name)
    return asset, visual


def _sections_for(
    mode: str, skills: Sequence[str] | None
) -> tuple[dict[str, SkillSection], SkillSection]:
    """返回（资产段的分段装载结果，视觉段的装载结果）。"""
    asset_names, visual_names = _split_named_skills(skills)

    if mode == "per_type":
        asset_sections = {key: build_skill_section((key,), asset_names) for key in _ASSET_KEYS}
    else:
        asset_sections = {"*": build_skill_section(_ASSET_KEYS, asset_names)}

    visual = build_skill_section(_VISUAL_KEYS, visual_names)
    return asset_sections, visual


def _merge_skills(
    sections: dict[str, SkillSection], visual: SkillSection
) -> tuple[list[str], list[str], int, int]:
    """多段装载结果合并去重（同一技能可能在多类里各装一次）。"""
    all_sections = [*sections.values(), visual]
    loaded = list(dict.fromkeys(n for s in all_sections for n in s.loaded))
    skipped = list(dict.fromkeys(n for s in all_sections for n in s.skipped))
    refs_ok = len({r for s in all_sections for r in s.refs})
    refs_skip = len({r for s in all_sections for r in s.refs_skipped})
    return loaded, skipped, refs_ok, refs_skip


# ---------------------------------------------------------------- 两段执行

def _run_assets_single(
    client: LLMChain,
    chapter: str,
    hints: dict[str, str],
    section: SkillSection,
    *,
    mode: str,
) -> tuple[Assets, chains.UsageCollector]:
    collector = chains.UsageCollector()
    data = _call_once(
        client,
        build_messages(chapter, hints, skill_text=section.text),
        stage="assets",
        mode=mode,
        collector=collector,
    )
    return Assets.model_validate(data), collector


def _run_assets_per_type(
    client: LLMChain,
    chapter: str,
    hints: dict[str, str],
    sections: dict[str, SkillSection],
    *,
    mode: str,
) -> tuple[Assets, chains.UsageCollector]:
    """三类各自一次调用：补充要求与技能规范都物理隔离，代价是 token/调用数 ~3 倍。

    并发交给 LangChain 的 `Runnable.batch` + `RunnableConfig.max_concurrency`，
    上限由 `LLM_MAX_CONCURRENCY` 卡死（原来是无上限的 ThreadPoolExecutor）。
    """
    keys = list(_CATEGORIES)
    batches = [
        build_messages(
            chapter,
            {key: hints.get(key, "")},
            only=key,
            skill_text=sections[key].text,
        )
        for key in keys
    ]
    collector = chains.UsageCollector()
    config = chains.run_config(
        stage="assets",
        mode=mode,
        prompt_version=PROMPT_VERSION,
        collector=collector,
        max_concurrency=settings.max_concurrency,
    )
    # 任一类失败直接抛，由路由统一转 500；不做静默降级，避免产出半张表
    texts = client.batch(batches, config=config)
    raw = {key: _parse_json(text) for key, text in zip(keys, texts, strict=True)}
    return (
        Assets(
            characters=[Character.model_validate(x) for x in raw["characters"].get("characters", [])],
            props=[Prop.model_validate(x) for x in raw["props"].get("props", [])],
            scenes=[Scene.model_validate(x) for x in raw["scenes"].get("scenes", [])],
        ),
        collector,
    )


def _run_visual(
    client: LLMChain,
    chapter: str,
    assets: Assets,
    section: SkillSection,
    max_groups: int,
    target_duration: float,
    scene_filter: Sequence[str] | None = None,
    *,
    mode: str,
) -> tuple[list[StoryboardGroup], int, chains.UsageCollector]:
    collector = chains.UsageCollector()
    data = _call_once(
        client,
        build_visual_messages(
            chapter,
            assets,
            skill_text=section.text,
            max_groups=max_groups,
            target_duration=target_duration,
            scene_filter=scene_filter,
        ),
        stage="visual",
        mode=mode,
        collector=collector,
    )
    raw = data.get("storyboards") or []
    groups = [StoryboardGroup.model_validate(x) for x in raw][:max_groups]
    estimated = data.get("total_groups_estimated")
    total = int(estimated) if isinstance(estimated, (int, float)) else len(groups)
    return groups, total, collector


@dataclass
class ExtractionResult:
    """一次生成的产物 + 可观测元数据（便于评测归因与成本核算）。"""

    package: ScriptPackage
    mock: bool
    latency_ms: int
    prompt_version: str = PROMPT_VERSION
    mode: str = "auto"
    llm_calls: int = 0
    hints_used: list[str] = field(default_factory=list)
    injection_warning: bool = False
    skills_loaded: list[str] = field(default_factory=list)
    skills_skipped: list[str] = field(default_factory=list)
    skills_refs_expanded: int = 0
    skills_refs_skipped: int = 0
    stages: list[dict] = field(default_factory=list)
    gate_issues: list[GateIssue] = field(default_factory=list)
    total_groups_estimated: int = 0
    truncated: bool = False


def extract_assets(
    chapter: str,
    *,
    character_hint: str = "",
    prop_hint: str = "",
    scene_hint: str = "",
    mode: str = "auto",
    skills: Sequence[str] | None = None,
    max_groups: int = 6,
    target_duration: float = 15.0,
    assets: Assets | None = None,
    scene_filter: Sequence[str] | None = None,
) -> ExtractionResult:
    """唯一入口：章节（+可选补充要求 +技能）→ 五类文生文产出。

    mode="auto"          两段串行：盘资产 → 站位+分镜。默认。
    mode="assets_only"   只出三类资产，一次调用（返修资产时用，便宜）。
    mode="per_type"      三类资产并发三次，物理隔离，用于对照验证是否串味。
    mode="visual_only"   跳过盘资产，拿请求里带回来的 assets 只重跑站位+分镜（1 次调用）。

    scene_filter：只拆指名字幕里的场次。长章一章常需 30–60 组，单次上限 20 组，
    不分场就永远只拍得到开头那一场，所以按场分片是长章的常规跑法。

    用户主路径只需要 chapter；其余参数留空即走默认口径。
    """
    if mode == "visual_only" and assets is None:
        raise ValueError("mode=visual_only 必须同时提供 assets")

    hints = {
        "characters": character_hint,
        "props": prop_hint,
        "scenes": scene_hint,
    }
    hints_used = [key for key, value in hints.items() if value.strip()]
    warning = detect_injection(chapter)

    # visual_only 不装资产段技能，省掉那一半正文
    asset_sections, visual_section = _sections_for(
        "auto" if mode == "visual_only" else mode, skills
    )
    skills_loaded, skills_skipped, refs_ok, refs_skip = _merge_skills(
        asset_sections, visual_section
    )
    calls_expected = {"auto": 2, "per_type": 3}.get(mode, 1)

    if settings.mock or not settings.api_key:
        time.sleep(0.2)
        package = mock_package("auto" if mode == "visual_only" else mode)
        return ExtractionResult(
            package=package,
            mock=True,
            latency_ms=200,
            mode=mode,
            llm_calls=0,
            hints_used=hints_used,
            injection_warning=warning,
            skills_loaded=skills_loaded,
            skills_skipped=skills_skipped,
            skills_refs_expanded=refs_ok,
            skills_refs_skipped=refs_skip,
            stages=[
                {"stage": "assets", "ms": 100, "mock": True},
                *(
                    [{"stage": "visual", "ms": 100, "mock": True}]
                    if mode in ("auto", "visual_only")
                    else []
                ),
            ],
            gate_issues=check_storyboards(package.storyboards),
            total_groups_estimated=len(package.storyboards),
            truncated=False,
        )

    t0 = time.time()
    client = _client()
    stages: list[dict] = []
    total_estimated = 0

    # ---- 只重跑分镜：资产表原样用请求带回的那份，名字才接得上
    if mode == "visual_only":
        t_visual = time.time()
        groups, total_estimated, usage = _run_visual(
            client, chapter, assets, visual_section, max_groups, target_duration,
            scene_filter, mode=mode,
        )
        stages.append(
            {
                "stage": "visual",
                "ms": int((time.time() - t_visual) * 1000),
                "groups": len(groups),
                "reused_assets": True,
                "usage": usage.as_dict(),
            }
        )
        package = ScriptPackage(
            characters=assets.characters,
            props=assets.props,
            scenes=assets.scenes,
            blockings=build_blockings(groups),
            storyboards=groups,
        )
        return ExtractionResult(
            package=package,
            mock=False,
            latency_ms=int((time.time() - t0) * 1000),
            mode=mode,
            llm_calls=1,
            hints_used=hints_used,
            injection_warning=warning,
            skills_loaded=skills_loaded,
            skills_skipped=skills_skipped,
            skills_refs_expanded=refs_ok,
            skills_refs_skipped=refs_skip,
            stages=stages,
            gate_issues=check_storyboards(groups),
            total_groups_estimated=total_estimated,
            truncated=total_estimated > len(groups),
        )

    # ---- 第一段：盘资产
    t_assets = time.time()
    if mode == "per_type":
        assets, asset_usage = _run_assets_per_type(
            client, chapter, hints, asset_sections, mode=mode
        )
    else:
        assets, asset_usage = _run_assets_single(
            client, chapter, hints, asset_sections["*"], mode=mode
        )
    stages.append(
        {
            "stage": "assets",
            "ms": int((time.time() - t_assets) * 1000),
            "characters": len(assets.characters),
            "props": len(assets.props),
            "scenes": len(assets.scenes),
            "usage": asset_usage.as_dict(),
        }
    )

    groups: list[StoryboardGroup] = []
    if mode == "auto":
        # ---- 第二段：带着已定稿资产写站位 + 分镜
        t_visual = time.time()
        groups, total_estimated, usage = _run_visual(
            client, chapter, assets, visual_section, max_groups, target_duration,
            scene_filter, mode=mode,
        )
        stages.append(
            {
                "stage": "visual",
                "ms": int((time.time() - t_visual) * 1000),
                "groups": len(groups),
                "usage": usage.as_dict(),
            }
        )
    else:
        total_estimated = 0

    package = ScriptPackage(
        characters=assets.characters,
        props=assets.props,
        scenes=assets.scenes,
        blockings=build_blockings(groups),
        storyboards=groups,
    )
    return ExtractionResult(
        package=package,
        mock=False,
        latency_ms=int((time.time() - t0) * 1000),
        mode=mode,
        llm_calls=calls_expected,
        hints_used=hints_used,
        injection_warning=warning,
        skills_loaded=skills_loaded,
        skills_skipped=skills_skipped,
        skills_refs_expanded=refs_ok,
        skills_refs_skipped=refs_skip,
        stages=stages,
        gate_issues=check_storyboards(groups),
        total_groups_estimated=total_estimated,
        truncated=bool(mode == "auto" and total_estimated > len(groups)),
    )
