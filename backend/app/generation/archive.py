"""资产归档：把 `/generate-novel` 的逐章产出落成 `assets/<小说名>/` 目录树。

## 这一层只做「落盘视图」，不改生成契约

graph.finalize 交出的是 `final`：小说名 + 每章的 `package`（ScriptPackage 对象）与标题。
本模块把它**投影**成一套便于人读、便于后续阶段按名引用的 Markdown 目录树。
三类资产各有一个**固定分类目录**，其下才是「条目名」目录，再往下才是文档：

```
assets/<小说名>/
├── 角色/<角色名>/   <角色名>.md + <角色名>-图片提示词.md
├── 场景/<场景名>/   <场景名>.md + <场景名>-图片提示词.md
├── 道具/<道具名>/   <道具名>.md + <道具名>-图片提示词.md
└── 章节/<章节主名>/  角色站位.md + 分镜.md
```

三条硬约束（与 AGENTS.md 第 3 节「资产归档」对齐）：

1. **跨章按名合并**：角色 / 场景 / 道具以 `name` 为键，同名只落一份，文件里列「出现章节」。
   道具一律落 `道具/<道具名>/`，`owner` 只写进正文（归属角色），不再决定落盘位置——
   角色目录里只有角色自己的文档。
2. **文件名净化 + 冲突加序号**：任何来自输入或模型的名字都不许跳出 `<小说名>/`；
   同名条目（角色与场景撞名、两章同名等）按序加 `-2`、`-3`，绝不覆盖彼此；
   条目名撞上保留目录名（角色 / 场景 / 道具 / 章节）时同样加序号，不往分类目录里塞条目。
3. **幂等覆盖 + 失败不阻断**：同名文件用临时文件 + `os.replace` 原子覆盖，重跑不叠加；
   任何局部失败只记进 `ArchiveReport.errors`，生成结果照常返回。
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from loguru import logger

from ..core.config import settings
from ..core.schemas import ArchiveReport, AssetManifest, PromptEntry
from .asset_store import (
    CATEGORY_DIRS,
    CHAPTER_DIR,
    GENERATED_SUFFIX,
    RESERVED_DIRS,
    body_hash,
    load_manifest,
    save_manifest,
    stable_id,
    utc_now,
)
from .image_prompts import render_entry_document, render_image_prompts

# archive.py 在 backend/app/generation/ 下，回退三层到仓库根。
REPO_ROOT = Path(__file__).resolve().parents[3]

# 文件系统非法字符：Windows 保留字符 + 路径分隔符 + 控制字符。
# 虽然本机是 macOS，也一并挡掉，保证归档目录拷到任何机器都能用。
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# 章节标题主名：与 chapters.py 的识别口径一致（第X章/序章/楔子等），
# 用于把「第一章：雨夜来客」映射成目录名「第一章」。
_HEADING_MAIN = re.compile(
    r"(?:第[零〇一二三四五六七八九十百千万两0-9]+[章节回部篇集]"
    r"|序章|序幕|楔子|引子|终章|尾声|后记|番外)"
)
MAX_NAME_CHARS = 80


def resolve_asset_root(root: str | Path | None = None) -> Path:
    """归档根目录：`root` 参数 > `LLM_ASSET_ROOT` > `<仓库>/assets`。相对路径按仓库根解析。"""
    raw = root if root is not None else settings.asset_root
    if not raw:
        return (REPO_ROOT / "assets").resolve()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _safe_name(raw: str, fallback: str) -> str:
    """净化单个名字：去非法字符、去首尾点与空白、限长；空名用 fallback 占位。"""
    text = _ILLEGAL.sub("_", raw).strip().strip(".").strip()
    return (text or fallback)[:MAX_NAME_CHARS]


def _chapter_dir_name(title: str) -> str:
    """章节目录名取标题主名，取不到就用净化后的整行标题。"""
    match = _HEADING_MAIN.search(title or "")
    raw = match.group(0) if match else (title or "").strip()
    return _safe_name(raw, "未命名章节")


class _NameBook:
    """目录/文件名分配器：同名按序加后缀，保证一目录内不互相覆盖。"""

    def __init__(self, reserved: frozenset[str] | set[str] | None = None) -> None:
        self._used: set[str] = set(reserved or ())

    def take(self, base: str) -> str:
        name = base
        suffix = 2
        while name in self._used:
            name = f"{base}-{suffix}"
            suffix += 1
        self._used.add(name)
        return name


def _as_dict(value: object) -> dict:
    """兼容 package 是 Pydantic 对象（graph 直出）或 dict（单测/落盘回放）两种形态。"""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()  # type: ignore[union-attr]
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _merge_named(items: list, source: str, merged: dict[str, dict]) -> None:
    """按 name 合并一类资产；字段取更全者，state 追加不同值，并累计出现章节。"""
    for raw in items:
        data = _as_dict(raw)
        name = str(data.get("name") or "").strip()
        if not name:
            continue
        if name not in merged:
            merged[name] = {"data": dict(data), "sources": []}
        record = merged[name]
        if source not in record["sources"]:
            record["sources"].append(source)
        for key, value in data.items():
            if key == "name" or not value:
                continue
            current = record["data"].get(key)
            if key == "state" and current and value != current:
                record["data"][key] = f"{current}；{value}"
            elif not current:
                record["data"][key] = value


def _kv(label: str, value: object) -> str:
    text = str(value or "").strip()
    return f"- {label}：{text}\n" if text else ""


def _render_character(record: dict) -> str:
    data = record["data"]
    name = str(data.get("name") or "未命名角色")
    parts = [f"# 角色 · {name}\n\n"]
    parts.append(_kv("外貌与服饰", data.get("appearance")))
    parts.append(_kv("外观特征", data.get("stable_features")))
    parts.append(_kv("本集状态", data.get("state")))
    parts.append(_kv("角色声线", data.get("voice")))
    parts.append(_kv("资产描述", data.get("prompt")))
    parts.append("\n## 出现章节\n\n")
    parts.append("\n".join(f"- {s}" for s in record["sources"]) + "\n")
    return "".join(parts)


def _render_scene(record: dict) -> str:
    data = record["data"]
    parts = [f"# 场景 · {data.get('name') or '未命名场景'}\n\n"]
    for label, key in (("时间", "time"), ("光线", "light"), ("氛围", "atmosphere"), ("资产描述", "prompt")):
        parts.append(_kv(label, data.get(key)))
    parts.append("\n## 出现章节\n\n")
    parts.append("\n".join(f"- {s}" for s in record["sources"]) + "\n")
    return "".join(parts)


def _render_prop(record: dict, owner: str = "") -> str:
    data = record["data"]
    parts = [f"# 道具 · {data.get('name') or '未命名道具'}\n\n"]
    parts.append(_kv("归属角色", owner or data.get("owner") or "无（环境陈设）"))
    parts.append(_kv("外观", data.get("appearance")))
    parts.append(_kv("状态变化", data.get("state_changes")))
    parts.append(_kv("资产描述", data.get("prompt")))
    parts.append("\n## 出现章节\n\n")
    parts.append("\n".join(f"- {s}" for s in record["sources"]) + "\n")
    return "".join(parts)


def _render_blockings(title: str, groups: list) -> str:
    parts = [f"# 角色站位 · {title}\n\n", "> 由该章 blockings 聚合，与分镜同源；逐组列出。\n"]
    for raw in groups:
        data = _as_dict(raw)
        parts.append(f"\n## {data.get('group_id') or '组'} · {data.get('location') or ''}\n\n")
        parts.append(str(data.get("text") or "（本章该组无站位记录）").rstrip() + "\n")
    return "".join(parts)


def _render_storyboards(title: str, groups: list) -> str:
    parts = [f"# 分镜 · {title}\n\n", "> 组时长 = 各镜之和；台词逐字取自原文，未改一字。\n"]
    for raw in groups:
        data = _as_dict(raw)
        parts.append(
            f"\n## {data.get('group_id') or '组'} · {data.get('duration')}s · {data.get('location') or ''}\n\n"
        )
        if data.get("summary"):
            parts.append(f"> {str(data['summary']).strip()}\n\n")
        for shot_raw in data.get("shots") or []:
            shot = _as_dict(shot_raw)
            parts.append(
                f"### 镜 {shot.get('shot_no', '?')} · {shot.get('duration')}s · "
                f"{shot.get('shot_size') or ''} / {shot.get('angle') or ''}\n\n"
            )
            parts.append(_kv("运镜", shot.get("camera")))
            parts.append(_kv("目的", shot.get("function")))
            parts.append(_kv("画面", shot.get("content")))
            # 台词是红线：只输出原文字符，不加引号、不做任何润色。
            parts.append(_kv("台词", shot.get("dialogue")))
            parts.append(_kv("站位", shot.get("blocking")))
            parts.append("\n")
    return "".join(parts)


def _atomic_write(path: Path, text: str) -> None:
    """临时文件 + 原子重命名：写到一半断电也不会留下半份文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".archive-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text.rstrip() + "\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def archive_novel(final: dict, root: str | Path | None = None) -> ArchiveReport:
    """把 final 结果投影成归档目录树。任何局部失败都记入报告，不向外抛异常。"""
    base = resolve_asset_root(root)
    novel_title = str(final.get("novel_title") or "").strip()
    novel_dir = base / _safe_name(novel_title, "未命名小说")
    report = ArchiveReport(root=str(base), novel_dir=str(novel_dir))

    chapters = [_as_dict(c) for c in (final.get("chapters") or [])]
    if not chapters:
        logger.bind(event="archive_skip", novel_title_chars=len(novel_title)).debug(
            "no chapters to archive"
        )
        return report

    try:
        novel_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        report.errors.append(f"无法创建小说目录：{exc}")
        return report

    # 顶层只有固定的四个目录：角色 / 场景 / 道具 / 章节；条目名在各自分类目录内分配。
    # 分类目录名固定不参与撞名（`RESERVED_DIRS`），条目名撞上保留名时加序号。
    entry_books: dict[str, _NameBook] = {}
    dir_book: dict[Path, _NameBook] = {}  # 每个目录内部的文件名分配器

    def entry_dir(category: str, name: str, fallback: str) -> Path:
        """`<小说名>/<分类目录>/<条目名>`：分类目录固定，条目名在分类内去重。"""
        book = entry_books.setdefault(category, _NameBook(RESERVED_DIRS))
        return novel_dir / CATEGORY_DIRS[category] / book.take(_safe_name(name, fallback))

    def write(directory: Path, filename: str, text: str) -> Path:
        book = dir_book.setdefault(directory, _NameBook())
        # 先净化成合法文件名，再过 book：同目录内撞名加序号（先写者保留原名）。
        stem = book.take(_safe_name(filename, "未命名"))
        target = directory / f"{stem}.md"
        _atomic_write(target, text)
        report.files_written += 1
        return target

    # ---- 第一遍：跨章收集三类资产与章节数据
    characters: dict[str, dict] = {}
    scenes: dict[str, dict] = {}
    props: dict[str, dict] = {}
    chapter_data: list[tuple[str, dict]] = []

    try:
        for chapter in chapters:
            title = str(chapter.get("title") or "").strip() or "未命名章节"
            chapter_data.append((title, chapter))
            package = _as_dict(chapter.get("package"))
            _merge_named(package.get("characters") or [], title, characters)
            _merge_named(package.get("scenes") or [], title, scenes)
            _merge_named(package.get("props") or [], title, props)
    except Exception as exc:  # noqa: BLE001 - 数据形态异常也不该拖垮生成
        report.errors.append(f"资产聚合失败：{exc}")
        return report

    # S3 图片提示词：跨章合并后的资产事实 → 固定版式模板拼装，零 LLM 调用。
    # 与资产同目录落 `<名称>-图片提示词.md`，编号全局连续（IMG-CHAR-... / SCENE / PROP）。
    prompts = render_image_prompts(
        characters=[record["data"] for record in characters.values()],
        scenes=[record["data"] for record in scenes.values()],
        props=[record["data"] for record in props.values()],
    )
    report.image_prompt_entries = len(prompts.entries)
    report.errors.extend(prompts.warnings)
    prompts_by_name = {(entry.category, entry.asset_name): entry for entry in prompts.entries}

    # 清单：正文只在 md 文件里，这里维护 id / 路径 / 状态 / hash 索引。
    try:
        manifest = load_manifest(base, novel_title)
    except ValueError as exc:  # 清单损坏也降级：重建空清单，不拖垮归档
        report.errors.append(f"清单读取失败，已重建：{exc}")
        manifest = AssetManifest(novel=_safe_name(novel_title, "未命名小说"), entries=[])
    by_id = {item.id: item for item in manifest.entries}

    def write_prompt(directory: Path, category: str, name: str) -> None:
        entry = prompts_by_name.get((category, name))
        if entry is None:  # 渲染侧会去掉名称尾部标点，这里用同样口径再查一次
            entry = prompts_by_name.get((category, name.strip().rstrip("。；;，, ")))
        if entry is None:
            return
        document = render_entry_document(entry)
        eid = stable_id(category, entry.asset_name)
        current_hash = body_hash(document)
        prior = by_id.get(eid)

        if prior is not None and prior.status in ("edited", "approved"):
            # 人工修订/已定稿：主文件不碰；模板 hash 变了就写对照副本供人工采用。
            report.prompts_skipped += 1
            main_path = novel_dir / prior.path
            generated = main_path.with_name(main_path.stem + GENERATED_SUFFIX + main_path.suffix)
            if prior.body_hash != current_hash:
                try:
                    _atomic_write(generated, document)
                except OSError as exc:
                    report.errors.append(f"对照副本写入失败：{prior.path}：{exc}")
            return

        target = write(directory, f"{directory.name}-图片提示词", document)
        relative = str(target.relative_to(novel_dir))
        if prior is None:
            prior = PromptEntry(
                id=eid,
                img_id=entry.id,
                category=category,
                asset_name=entry.asset_name,
                path=relative,
                status="draft",
                body_hash=current_hash,
                updated_at=utc_now(),
            )
            manifest.entries.append(prior)
            by_id[eid] = prior
        else:
            # draft 覆盖：hash 没变也刷新一次相对路径（目录名可能因撞名变化）。
            prior.path = relative
            prior.img_id = entry.id
            prior.asset_name = entry.asset_name
            prior.body_hash = current_hash
            prior.updated_at = utc_now()
        report.image_prompts_written += 1

    report.characters = len(characters)
    report.scenes = len(scenes)
    report.props = len(props)

    # 角色落盘固定为 角色/<角色名>/；道具归属只写正文，不再按 owner 决定目录。
    try:
        for name, record in characters.items():
            directory = entry_dir("character", name, "未命名角色")
            # 文件名取目录名（可能与资产名不同：撞保留目录名时带序号），保证两者一致。
            write(directory, directory.name, _render_character(record))
            report.characters_written += 1
            write_prompt(directory, "character", name)
    except Exception as exc:  # noqa: BLE001 - 单个角色失败不阻断其余归档
        report.errors.append(f"角色归档失败：{exc}")

    try:
        for name, record in scenes.items():
            directory = entry_dir("scene", name, "未命名场景")
            write(directory, directory.name, _render_scene(record))
            report.scenes_written += 1
            write_prompt(directory, "scene", name)
    except Exception as exc:  # noqa: BLE001 - 单个场景失败不阻断其余归档
        report.errors.append(f"场景归档失败：{exc}")

    # 道具一律落「道具/<道具名>/」；owner 只写进正文（归属角色），不再决定落盘位置，
    # 所以角色目录里只有角色自己的文档，不会混进道具。
    try:
        for name, record in props.items():
            data = record["data"]
            owner = str(data.get("owner") or "").strip()
            directory = entry_dir("prop", name, "未命名道具")
            write(directory, directory.name, _render_prop(record, owner))
            report.props_written += 1
            write_prompt(directory, "prop", name)
    except Exception as exc:  # noqa: BLE001 - 单个道具失败不阻断其余归档
        report.errors.append(f"道具归档失败：{exc}")

    # ---- 第二遍：逐章落角色站位与分镜（assets_only 模式没有这两类，则跳过）
    chapter_root = novel_dir / CHAPTER_DIR
    chapter_book = _NameBook(RESERVED_DIRS)
    try:
        for title, chapter in chapter_data:
            package = _as_dict(chapter.get("package"))
            blockings = package.get("blockings") or []
            storyboards = package.get("storyboards") or []
            if not blockings and not storyboards:
                continue
            directory = chapter_root / chapter_book.take(_chapter_dir_name(title))
            if blockings:
                write(directory, "角色站位", _render_blockings(title, blockings))
            if storyboards:
                write(directory, "分镜", _render_storyboards(title, storyboards))
            report.chapters += 1
    except Exception as exc:  # noqa: BLE001 - 单章失败不阻断其余归档
        report.errors.append(f"章节归档失败：{exc}")

    # 清单在所有 write_prompt 之后统一落盘，统计三态计数写进回告。
    try:
        save_manifest(base, novel_title, manifest)
    except Exception as exc:  # noqa: BLE001 - 清单落盘失败不阻断主归档
        report.errors.append(f"清单写入失败：{exc}")
    for item in manifest.entries:
        if item.status == "draft":
            report.prompt_draft += 1
        elif item.status == "edited":
            report.prompt_edited += 1
        elif item.status == "approved":
            report.prompt_approved += 1

    logger.bind(
        event="archive_done",
        novel_title_chars=len(novel_title),
        files=report.files_written,
        chapters=report.chapters,
        errors=len(report.errors),
    ).info("asset archive done")
    return report
