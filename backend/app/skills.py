"""技能库：扫描 skills/ 目录，按 Agent Skills 通用规范装配进系统提示词。

## 规范兼容性（这里踩过的坑，改动前务必先读）

通用格式（Anthropic Agent Skills / OpenAI skills）只强制 `name` + `description`，
其余全靠约定。所以加载器**不能**把自己的私有字段当成必填，否则从 GitHub 拿来的
标准技能会整批失效。本模块遵守三条：

1. `metadata.applies-to` 是本项目的**可选**扩展。没有它不等于格式错误，只意味着
   该技能不参与「按类别自动装载」，等用户在前端勾选即可。
2. 正文**不截断**。通用规范允许 SKILL.md 写到 5000 token 量级，截断会把「完成标准」
   这类收尾约束砍掉，比不加载更糟。篇幅控制全部交给预算，超预算就整体跳过并回告。
3. 通用规范的核心是三层渐进披露：frontmatter（菜单）→ SKILL.md 正文（装载）→
   `references/*.md`（按需读取）。本项目当前没有文件读取工具，第三层用**内联展开**
   降级模拟：把正文里 `[文本](references/x.md)` 指向的文件内容按预算插到正文之后。
   等阶段 03 接上 Function Calling，把展开换成真 read_file 即可，上层不用改。

## 装载由谁决定

代码决定，不让模型自己挑——本阶段没有工具调用，模型无法主动读文件。等阶段 03
把 `catalog()` 当菜单给模型，即可切换成模型自选。

## 坏文件处理

单个技能格式错误只记录、不抛异常，不能让整个盘点接口挂掉。
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .config import settings

# 单技能块（正文 + 展开的 references）上限；超出即整体跳过，不切片
MAX_SKILL_BLOCK_CHARS = 32_000
# 一次注入的技能总字数上限；可用 LLM_SKILLS_BUDGET_CHARS 覆盖
DEFAULT_SECTION_BUDGET = 60_000
# references 递归展开深度：SKILL.md → ref → ref，两层足够覆盖「总纲→细则」
MAX_REF_DEPTH = 2

# 本项目的类别词表（只做校验提示与前端标签，不是白名单：声明了未知类别只告警）
ALLOWED_CATEGORIES = frozenset(
    {"characters", "props", "scenes", "storyboard", "blocking", "script",
     "review", "media", "global", "manual"}
)

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_KV_RE = re.compile(r"^[A-Za-z0-9_.\-]+\s*:")
_BLOCK_SCALAR = frozenset({">", "|", ">-", "|-", ">+", "|+"})
# Markdown 链接：[文本](目标 "标题")；目标里允许锚点 #xxx
_LINK_RE = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+?)(?:\s+\"[^\"]*\")?\s*\)")


class UnknownSkillError(ValueError):
    """显式指定的技能名不存在。"""

    def __init__(self, names: Sequence[str]):
        self.names = list(names)
        super().__init__("未找到技能：" + "、".join(self.names))


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    categories: tuple[str, ...]      # 显式声明（含项目绑定表）；空则 manual
    body: str
    path: Path
    version: str = ""
    license: str = ""
    display_name: str = ""
    refs: tuple[str, ...] = ()       # 正文引用到的本地 .md（相对技能目录）
    notes: tuple[str, ...] = ()      # 非致命提示（如 name 与目录名不一致）

    @property
    def dir(self) -> Path:
        return self.path.parent


@dataclass(frozen=True)
class SkillSection:
    """装进系统提示词的一段技能规范 + 装载结果，便于响应里回告。"""

    text: str
    loaded: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    refs: tuple[str, ...] = ()        # 实际展开了哪些 references
    refs_skipped: tuple[str, ...] = ()
    chars: int = field(default=0)


# --------------------------------------------------------------------------
# 最小 YAML 解析（只服务 frontmatter 与 openai.yaml，不引 PyYAML）
# --------------------------------------------------------------------------


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _split_list(value: str) -> tuple[str, ...]:
    value = value.strip().strip("[]")
    return tuple(
        item.strip().strip("\"'") for item in re.split(r"[,\s]+", value) if item.strip()
    )


def _collect_indented(lines: list[str], start: int) -> tuple[list[str], int]:
    """收集 start 起所有缩进行（含空行），返回 (块, 下一游标)。"""
    block: list[str] = []
    i, n = start, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            block.append("")
            i += 1
            continue
        if line[:1] in (" ", "\t"):
            block.append(line)
            i += 1
            continue
        break
    while block and not block[-1].strip():
        block.pop()
    return block, i


def _deindent(block: list[str]) -> list[str]:
    indents = [len(line) - len(line.lstrip()) for line in block if line.strip()]
    cut = min(indents) if indents else 0
    return [line[cut:] if len(line) >= cut else "" for line in block]


def _fold(block: list[str], style: str) -> str:
    """处理 | 与 > 两种块标量：竖线保换行，尖括号折叠成空格、空行分段。"""
    lines = _deindent(block)
    if style.startswith("|"):
        text = "\n".join(lines)
    else:
        paras: list[str] = []
        cur: list[str] = []
        for line in lines:
            if line.strip():
                cur.append(line.strip())
            elif cur:
                paras.append(" ".join(cur))
                cur = []
        if cur:
            paras.append(" ".join(cur))
        text = "\n\n".join(paras)
    # `-`（strip）与 `+`（keep）在提示词场景无差别，统一去掉首尾空行
    return text.strip()


def parse_yaml_lite(text: str) -> dict[str, object]:
    """解析 frontmatter 量级的 YAML：支持嵌套一层、块标量、多行续写、行内列表。

    刻意不引 PyYAML：这里只需要扁平键值，多一个依赖不划算。通用技能的
    `description` 常写成 `>-` 折叠块或多行续写，所以必须支持，否则会静默丢字。
    """
    lines = text.splitlines()
    out: dict[str, object] = {}
    i, n = 0, len(lines)
    while i < n:
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if raw[:1] in (" ", "\t"):
            i += 1  # 顶层意外缩进，忽略
            continue
        key, sep, value = raw.partition(":")
        if not sep:
            i += 1
            continue
        key = key.strip().strip("\"'")
        value = value.strip()

        if value in _BLOCK_SCALAR:
            block, i = _collect_indented(lines, i + 1)
            out[key] = _fold(block, value)
            continue

        if value:
            # 缩进续写的普通标量：YAML 会把下面缩进行折叠进同一个值
            block, nxt = _collect_indented(lines, i + 1)
            cont_lines = [line for line in block if line.strip()]
            if cont_lines and not all(_KV_RE.match(line.strip()) for line in cont_lines):
                out[key] = (_unquote(value) + " " + " ".join(l.strip() for l in cont_lines)).strip()
                i = nxt
                continue
            out[key] = _unquote(value)
            i += 1
            continue

        # 空值：可能是嵌套 map，也可能是多行 plain 标量的第一层
        block, nxt = _collect_indented(lines, i + 1)
        if not block:
            out[key] = ""
            i = nxt
            continue
        sub_lines = [line for line in block if line.strip()]
        if sub_lines and all(_KV_RE.match(line.strip()) for line in sub_lines):
            sub: dict[str, str] = {}
            for line in _deindent(sub_lines):
                k, s, v = line.partition(":")
                if s and k.strip():
                    sub[k.strip().strip("\"'")] = _unquote(v)
            out[key] = sub
        else:
            out[key] = " ".join(line.strip() for line in sub_lines)
        i = nxt
    return out


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """把 SKILL.md 拆成 (扁平元数据, 正文)。嵌套键用 `父.子` 表示。"""
    match = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(\r?\n(.*))?$", text, re.DOTALL)
    if not match:
        raise ValueError("缺少 YAML frontmatter（--- 包裹的 name / description）")

    raw, body = match.group(1), match.group(3) or ""
    parsed = parse_yaml_lite(raw)
    flat: dict[str, str] = {}
    for key, value in parsed.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                flat[f"{key}.{sub_key}"] = str(sub_value)
        else:
            flat[key] = str(value)
    return flat, body.strip()


# --------------------------------------------------------------------------
# 技能发现与校验
# --------------------------------------------------------------------------


def default_root() -> Path:
    """默认 backend/skills；可用配置 LLM_SKILLS_DIR 覆盖。"""
    if settings.skills_dir:
        return Path(settings.skills_dir).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "skills"


def bindings_path(root: Path | None = None) -> Path:
    """项目侧绑定表：不改技能文件也能声明「技能 → 类别」。"""
    if settings.skills_bindings:
        return Path(settings.skills_bindings).expanduser().resolve()
    base = root or default_root()
    return base.parent / "skill-bindings.yaml"


def load_bindings(root: Path | None = None) -> dict[str, tuple[str, ...]]:
    """读 skill-bindings.yaml；缺失或格式坏都返回空表，不影响技能加载。"""
    path = bindings_path(root)
    if not path.is_file():
        return {}
    try:
        parsed = parse_yaml_lite(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 绑定表坏了不该拖垮技能库
        return {}
    table: dict[str, tuple[str, ...]] = {}
    for key, value in parsed.items():
        if isinstance(value, dict):
            continue
        cats = _split_list(str(value))
        if cats:
            table[key] = cats
    return table


def _read_openai_meta(skill_dir: Path) -> dict[str, str]:
    """读 agents/openai.yaml 的 interface 段（通用补充层，缺失即跳过）。"""
    path = skill_dir / "agents" / "openai.yaml"
    if not path.is_file():
        return {}
    try:
        parsed = parse_yaml_lite(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    interface = parsed.get("interface")
    if not isinstance(interface, dict):
        return {}
    return {key: str(value) for key, value in interface.items()}


def _local_refs(body: str) -> tuple[str, ...]:
    """正文里指向本地 .md 的相对路径，按出现顺序去重。"""
    found: list[str] = []
    for match in _LINK_RE.finditer(body):
        target = match.group(1).strip()
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        target = target.split("#", 1)[0]
        if not target.endswith(".md"):
            continue
        if target not in found:
            found.append(target)
    return tuple(found)


def load_skill(path: Path, *, bindings: dict[str, tuple[str, ...]] | None = None) -> Skill:
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    notes: list[str] = []

    name = meta.get("name") or path.parent.name
    if not _NAME_RE.match(name):
        raise ValueError(f"name 需为小写 kebab-case（字母数字与连字符），实际为 {name!r}")
    if len(name) > 64:
        raise ValueError(f"name 超过规范上限 64 字符（{len(name)}）")
    if name != path.parent.name:
        # 通用规范用目录名做发现、name 做引用，不一致时仍可加载，只提示
        notes.append(f"name({name}) 与目录名({path.parent.name}) 不一致")

    openai_meta = _read_openai_meta(path.parent)
    description = meta.get("description", "").strip()
    if not description:
        description = openai_meta.get("short_description", "").strip()
    if not description:
        raise ValueError("缺少 description（frontmatter 与 agents/openai.yaml 都没有）")

    declared = (
        _split_list(meta.get("metadata.applies-to", ""))
        or _split_list(meta.get("applies-to", ""))
        or (bindings or {}).get(name, ())
        or _split_list(openai_meta.get("applies_to", ""))
    )
    categories = tuple(declared) or ("manual",)
    unknown = set(categories) - ALLOWED_CATEGORIES - {"manual"}
    if unknown:
        # 通用技能可能用别的词表；不认识就提示，但不拒绝——拒绝会让整个技能失效
        notes.append(f"类别 {sorted(unknown)} 不在本项目词表内，仅按名字手动选择")

    if not body:
        raise ValueError("正文为空")

    return Skill(
        name=name,
        description=description,
        categories=categories,
        body=body,
        path=path,
        version=meta.get("metadata.version", "") or meta.get("version", ""),
        license=meta.get("license", ""),
        display_name=openai_meta.get("display_name", ""),
        refs=_local_refs(body),
        notes=tuple(notes),
    )


_CACHE: dict[str, tuple[tuple[int, int], tuple[Skill, ...], tuple[str, ...]]] = {}


def load_all(root: Path | None = None) -> tuple[tuple[Skill, ...], tuple[str, ...]]:
    """返回 (技能列表, 加载错误列表)，按目录名排序；带 mtime 缓存，改文件即生效。"""
    base = root or default_root()
    paths = sorted(base.glob("*/SKILL.md"))
    bind = load_bindings(root)
    stamp = (
        len(paths),
        sum(p.stat().st_mtime_ns for p in paths),
        hash(tuple(sorted(bind.items()))),
    )

    cached = _CACHE.get(str(base))
    if cached and cached[0] == stamp:
        return cached[1], cached[2]

    skills: list[Skill] = []
    errors: list[str] = []
    for path in paths:
        try:
            skills.append(load_skill(path, bindings=bind))
        except Exception as exc:  # noqa: BLE001 - 单个坏文件不能拖垮整个服务
            errors.append(f"{path.parent.name}: {exc}")

    _CACHE[str(base)] = (stamp, tuple(skills), tuple(errors))
    return tuple(skills), tuple(errors)


def catalog(categories: Iterable[str] | None = None) -> list[dict[str, object]]:
    """给前端 / 模型看的技能菜单（只有元数据，不含正文）。

    categories 为空时返回全部；传了就按显式声明的类别过滤。没声明类别的技能
    （manual）只在不过滤时出现——前端默认不过滤，所以照样能看到、能勾选。
    """
    skills, _ = load_all()
    wanted = set(categories) if categories else None

    return [
        {
            "name": skill.name,
            "description": skill.description,
            "categories": list(skill.categories),
            "version": skill.version,
            "license": skill.license,
            "display_name": skill.display_name,
            "body_chars": len(skill.body),
            "refs": list(skill.refs),
            "notes": list(skill.notes),
        }
        for skill in skills
        if wanted is None or wanted & set(skill.categories)
    ]


# --------------------------------------------------------------------------
# references 内联展开（第三层渐进披露的降级实现）
# --------------------------------------------------------------------------


def _resolve_relative(skill_dir: Path, base: Path, target: str) -> Path | None:
    """把相对路径按 base 解析到技能目录内；越界、非 .md、不存在都返回 None。

    `..` 一律拒绝：references 里出现 `../../xxx` 只可能是配置错误或刻意越界，
    没有正当用途。
    """
    target = target.split("#", 1)[0].strip()
    if not target.endswith(".md"):
        return None
    if target.startswith(("/", "\\")) or ".." in Path(target).parts:
        return None
    candidate = (base / target).resolve()
    try:
        candidate.relative_to(skill_dir.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _expand_refs(
    body: str, skill_dir: Path, budget: int
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """把正文引用的本地 .md 按出现顺序内联到正文之后。

    返回 (追加段文本, 已展开的相对路径, 未展开的相对路径)。
    只认技能目录内的 .md；外链、脚本、jsonl 一律跳过——把 .py 塞进提示词没有意义。
    """
    queue: deque[tuple[Path, int]] = deque()
    for ref in _local_refs(body):
        resolved = _resolve_relative(skill_dir, skill_dir, ref)
        if resolved:
            queue.append((resolved, 1))

    visited: set[Path] = set()
    expanded: list[str] = []
    skipped: list[str] = []
    blocks: list[str] = []
    used = 0

    while queue:
        path, depth = queue.popleft()
        if path in visited:
            continue
        visited.add(path)
        rel = path.relative_to(skill_dir.resolve()).as_posix()
        try:
            content = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            skipped.append(rel)
            continue
        if not content:
            skipped.append(rel)
            continue
        block = f'<ref path="{rel}">\n{content}\n</ref>'
        if used + len(block) > budget:
            skipped.append(rel)
            continue
        used += len(block)
        expanded.append(rel)
        blocks.append(block)
        if depth < MAX_REF_DEPTH:
            for inner in _local_refs(content):
                # 引用文件里的相对链接，按它自己所在目录解析（references/ 内的兄弟文件）
                inner_resolved = _resolve_relative(skill_dir, path.parent, inner)
                if inner_resolved and inner_resolved not in visited:
                    queue.append((inner_resolved, depth + 1))

    if not blocks:
        return "", tuple(expanded), tuple(skipped)

    text = (
        "## 已展开的参考资料（原文来自 references/，等价于按需读取）\n"
        + "\n\n".join(blocks)
    )
    if skipped:
        text += "\n\n（以下参考文件因篇幅预算未展开，需要时按正文说明自行判断，不要编造其内容："
        text += "、".join(skipped) + "）"
    return text, tuple(expanded), tuple(skipped)


# --------------------------------------------------------------------------
# 装配
# --------------------------------------------------------------------------


def _select(
    skills: Sequence[Skill], categories: set[str], names: Sequence[str] | None
) -> list[Skill]:
    """显式点名优先（不做类别过滤，点了就装）；否则按**显式声明**的类别自动匹配。

    刻意不让关键词推断参与自动匹配：12 个阶段型技能里大半都在描述里提到「角色」
    「场景」，按关键词装会一次装进半个库，既烧 token 又互相干扰。
    """
    if names:
        wanted = list(dict.fromkeys(name.strip() for name in names if name.strip()))
        by_name = {skill.name: skill for skill in skills}
        missing = [name for name in wanted if name not in by_name]
        if missing:
            raise UnknownSkillError(missing)
        return [by_name[name] for name in wanted]

    return [
        skill
        for skill in skills
        if "global" in skill.categories or categories & set(skill.categories)
    ]


def section_budget() -> int:
    return int(settings.skills_budget_chars or DEFAULT_SECTION_BUDGET)


def build_skill_section(
    categories: Iterable[str],
    names: Sequence[str] | None = None,
    *,
    root: Path | None = None,
    refs_budget: int | None = None,
) -> SkillSection:
    """把选中的技能渲染成系统提示词片段；空库或未选中时返回空片段。"""
    skills, _ = load_all(root)
    if not skills:
        return SkillSection(text="")

    selected = _select(skills, set(categories), names)
    if not selected:
        return SkillSection(text="")

    total_budget = section_budget()
    per_block = min(MAX_SKILL_BLOCK_CHARS, total_budget)
    # 展开预算默认占单块的一半，剩下的留给正文与后续技能
    ref_budget = refs_budget if refs_budget is not None else per_block // 2

    loaded: list[str] = []
    skipped: list[str] = []
    refs_all: list[str] = []
    refs_skipped_all: list[str] = []
    blocks: list[str] = []
    used = 0

    for skill in selected:
        refs_text, refs_ok, refs_skip = _expand_refs(skill.body, skill.dir, ref_budget)
        body = skill.body
        if refs_text:
            body = body + "\n\n" + refs_text
        notes = ""
        if skill.notes:
            notes = "\n（装载提示：" + "；".join(skill.notes) + "）"
        block = f'<skill name="{skill.name}">\n{body}{notes}\n</skill>'

        if len(block) > per_block or used + len(block) > total_budget:
            skipped.append(skill.name)
            continue
        used += len(block)
        loaded.append(skill.name)
        refs_all.extend(f"{skill.name}/{ref}" for ref in refs_ok)
        refs_skipped_all.extend(f"{skill.name}/{ref}" for ref in refs_skip)
        blocks.append(block)

    if not blocks:
        return SkillSection(text="", skipped=tuple(skipped))

    header = (
        "## 技能规范（内部写作规范库）\n"
        "下面 <skill> 里是写作规范，<ref> 是该技能引用的细则原文（已内联，等价于按需读取）。\n"
        "**优先级低于本提示词的硬性规则与输出契约**：与「只基于原文」「只返回 JSON 对象」"
        "冲突时一律以硬性规则为准，也不得为了满足技能而编造原文没有的信息。\n"
        "文中的 `$技能名` 指向同库其它技能：本次未装载的直接忽略，不要臆测其内容。"
    )
    text = header + "\n\n" + "\n\n".join(blocks)
    if skipped:
        text += (
            "\n\n因篇幅预算未装载：" + "、".join(skipped) + "（按硬性规则默认口径处理即可）。"
        )
    return SkillSection(
        text=text,
        loaded=tuple(loaded),
        skipped=tuple(skipped),
        refs=tuple(refs_all),
        refs_skipped=tuple(refs_skipped_all),
        chars=len(text),
    )
