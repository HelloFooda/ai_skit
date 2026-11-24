"""文生图提示词渲染：资产事实 + 固定版式模板 → `IMG-...` 条目。

## 这一层是纯函数，零 LLM 调用

S2 资产的 `prompt` 字段已经是「把 appearance/stable_features/state 整合成的可读外观
陈述」（见 schemas.Character.prompt 的注释）。图片提示词缺的只是技能包
`short-drama-image-prompts/references/sheet-prompt-guide.md` 规定的版式外壳
（设定图类型 → 核心外观 → 本张状态 → 构图版式 → 材质布光 → 背景清场与字迹排除）。
外壳是确定性规则，用模板拼比再调一次 LLM 更稳、更省 token，也不会临场改掉资产事实。

## 边界

- **事实主体**：逐字取资产 `prompt`；为空时按 appearance/stable_features/state（道具按
  appearance/state_changes，场景按 time/light/atmosphere）回退，仍为空则跳过该条并回告，
  绝不编造外观。
- **版式外壳**：固定模板，按资产类型选一套；不写画幅数字、不写机位、不写具体型号，
  与技能包「资产阶段不含版式、图片提示词阶段补上」的分工一致。
- **不生成图片、不绑定参考图**：这里只产 `IMG-...` 条目；真实成图与 `REF-...` 绑定
  属投产阶段，当前 `- 参考：` 一律写「无外部参考」。
- **字迹默认**：道具表面默认无可读字，需可读字句时由人工按技能包 IMG-07 规则改写——
  资产契约里没有字迹规则字段，代码不替创作者拍板。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_PREFIX = {"character": "IMG-CHAR", "scene": "IMG-SCENE", "prop": "IMG-PROP"}


@dataclass(frozen=True)
class ImagePrompt:
    """一条 `IMG-...` 条目。"""

    id: str
    title: str
    usage: str
    body: str
    category: str
    asset_name: str

    def to_markdown(self) -> str:
        return (
            f"## {self.id} · {self.title}\n\n"
            f"- 用途：{self.usage}\n"
            "- 参考：无外部参考\n\n"
            "### 可复制提示词\n\n"
            f"> {self.body}\n"
        )


@dataclass(frozen=True)
class RenderResult:
    """渲染结果：`warnings` 非空表示有条目的资产事实缺失而被跳过。"""

    entries: list[ImagePrompt]
    warnings: list[str]


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())  # type: ignore[union-attr]
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _clean(value: object) -> str:
    return str(value or "").strip().rstrip("。；;，, ")


def _fact(data: dict[str, Any], keys: Sequence[str]) -> str:
    """资产事实：先取 prompt，再按字段回退；只取第一段非空值，不拼凑多来源。"""
    for key in keys:
        text = _clean(data.get(key))
        if text:
            return text
    return ""


# 三区域版式（角色 / 场景 / 道具共用同一几何，内容按类别适配）：
#   上部 2/3 高：左主区占 2/3 宽，右子区占 1/3 宽；
#   下部 1/3 高：细节区横向满宽。
# 边界必须显式声明、内容互不串混——对齐技能包「同板对照多状态须标明分栏、
# 不写几宫格这类空排版词」。细节区只指明部位、复用同一份资产事实，不新增外观。
_SHEET_LAYOUT_RULE = "三区域边界清晰、内容互不串混。"


def _character_body(data: dict[str, Any], fact: str) -> str:
    name = _clean(data.get("name")) or "未命名角色"
    return "".join(
        [
            f"角色设定图：{name}，三区域分栏设定板。",
            f"固定核心外观：{fact}。",
            ("版式：上部占画面高度 2/3，其中左侧主区域占宽度 2/3，展示全身三视图"
            "（正面、侧面、背面共享同一基线，各视图同衣同态）；右侧子区域占宽度 1/3，"
            "展示头肩特写并与主区五官发型一致。下部细节区域占高度 1/3、横向满宽，"
            "局部放大展示上述外观中的发型轮廓、脸部特征、服饰材质与磨损细节。"),
            _SHEET_LAYOUT_RULE,
            "纯中性浅灰背景，前侧柔光均匀照亮五官起伏与服装层次，避开底光与纯逆光。",
            ("各区域五官、发型与身高比例一致，无视图标签、无标注箭头、无尺寸刻度、"
            "无夸张广角畸变、无遮脸道具、无文字、无水印。"),
        ]
    )


def _scene_body(data: dict[str, Any], fact: str) -> str:
    name = _clean(data.get("name")) or "未命名场景"
    time = _clean(data.get("time"))
    light = _clean(data.get("light"))
    parts = [f"空镜场景设定图：{name}" + (f"，{time}" if time else "") + "，三区域分栏设定板。", f"空间布局与固定陈设：{fact}。"]
    if light:
        parts.append(f"主光方位与光景：{light}。")
    parts.extend(
        [
            ("版式：上部占画面高度 2/3，其中左侧主区域占宽度 2/3，展示主视角全景空镜"
            "（门窗与主通道都在画面内，陈设相对位置与透视关系清楚）；右侧子区域占宽度 1/3，"
            "展示同一空间的关键陈设局部或另一机位特写。下部细节区域占高度 1/3、横向满宽，"
            "局部放大展示上述空间中的墙面、地面、陈设摆件与光景细节。"),
            _SHEET_LAYOUT_RULE,
            "室内外常规空镜清场，无人影、无剧情动作、无文字、无水印。",
        ]
    )
    return "".join(parts)


def _prop_body(data: dict[str, Any], fact: str) -> str:
    name = _clean(data.get("name")) or "未命名道具"
    return "".join(
        [
            f"道具设定图：{name}，三区域分栏设定板。",
            f"形制、尺寸与材质：{fact}。",
            ("版式：上部占画面高度 2/3，其中左侧主区域占宽度 2/3，展示道具整体形制，"
            "合页、搭扣、齿纹等关键辨识结构朝向镜头清晰可见；右侧子区域占宽度 1/3，"
            "展示关键辨识结构的局部特写。下部细节区域占高度 1/3、横向满宽，"
            "局部放大展示上述道具的材质纹理、磨损痕迹与结构细节。"),
            _SHEET_LAYOUT_RULE,
            "以常规桌面边缘或刻度垫板作尺寸参照，不把手当尺子。",
            "器物表面无可读文字（字迹待后期叠加），无水印。",
        ]
    )


def _build(
    items: Sequence[object],
    category: str,
    fact_keys: Sequence[str],
    body_fn: Any,
    title_suffix: str,
    usage_prefix: str,
    counters: dict[str, int],
    warnings: list[str],
) -> list[ImagePrompt]:
    entries: list[ImagePrompt] = []
    for raw in items:
        data = _as_dict(raw)
        name = _clean(data.get("name"))
        if not name:
            continue
        fact = _fact(data, fact_keys)
        if not fact:
            warnings.append(f"{category}「{name}」缺少可用资产事实，已跳过其图片提示词")
            continue
        counters[category] += 1
        entry_id = f"{_PREFIX[category]}-{counters[category]:03d}"
        entries.append(
            ImagePrompt(
                id=entry_id,
                title=f"{name}{title_suffix}",
                usage=f"{usage_prefix}「{name}」{title_suffix}",
                body=body_fn(data, fact),
                category=category,
                asset_name=name,
            )
        )
    return entries


def render_image_prompts(
    characters: Sequence[object] = (),
    scenes: Sequence[object] = (),
    props: Sequence[object] = (),
) -> RenderResult:
    """三类资产 → `IMG-...` 条目列表。纯拼装，不调 LLM、不读文件。"""
    counters = {"character": 0, "scene": 0, "prop": 0}
    warnings: list[str] = []
    entries: list[ImagePrompt] = []
    entries += _build(
        characters, "character", ("prompt", "appearance", "stable_features", "state"),
        _character_body, "角色设定图", "人物", counters, warnings,
    )
    entries += _build(
        scenes, "scene", ("prompt", "time", "light", "atmosphere"),
        _scene_body, "空镜设定图", "地点", counters, warnings,
    )
    entries += _build(
        props, "prop", ("prompt", "appearance", "state_changes"),
        _prop_body, "道具设定图", "道具", counters, warnings,
    )
    return RenderResult(entries=entries, warnings=warnings)


def _document(entries: Sequence[ImagePrompt], title: str) -> str:
    parts = [
        f"# 图片提示词 · {title}\n\n",
        ("> 由资产事实 + 固定版式模板拼装（零 LLM 调用）；事实主体取自同目录资产文档的"
        "「资产描述」。出图前请人工复核版式、连戏锁与字迹规则。\n\n"),
    ]
    for entry in entries:
        parts.append(entry.to_markdown())
        parts.append("\n")
    return "".join(parts).rstrip() + "\n"


def render_image_prompts_markdown(result: RenderResult, title: str) -> str:
    """把整批渲染结果落成一份 `图片提示词.md`（按技能包二级标题 + 可复制块格式）。"""
    return _document(result.entries, title)


def render_entry_document(entry: ImagePrompt) -> str:
    """把单条 `IMG-...` 落成一份独立文档，供按资产目录逐条落盘。"""
    return _document([entry], entry.asset_name)
