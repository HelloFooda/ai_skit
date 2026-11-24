"""资产库清单：稳定 id、三态状态机、路径安全的读写原语。

## 目录口径

三类资产各有一个固定的中文分类目录，其下才是条目名目录，再往下才是文档：

```
assets/<小说名>/角色/<角色名>/<角色名>.md + <角色名>-图片提示词.md
assets/<小说名>/场景/<场景名>/...
assets/<小说名>/道具/<道具名>/...
assets/<小说名>/章节/<章节标题>/...
```

分类目录名固定（`CATEGORY_DIRS`），不参与撞名分配；道具一律进 `道具/`，不再挂到角色目录。

## 事实源分工

- **正文只在文件里**：`assets/<小说名>/<分类目录>/<条目名>/<条目名>-图片提示词.md` 是唯一事实源，
  清单不存正文，只存路径与 hash。手工改文件仍生效，清单只是索引。
- **清单记状态**：`assets/<小说名>/_manifest.json` 记每条提示词的 `id / img_id /
  category / asset_name / path / status / body_hash / updated_at`，让前端能列出、筛选、
  知道哪条被人工改过、哪条已定稿。

## 稳定 id 与三态

- `id = img-<category>-<hash8>`，`hash8 = sha1(f"{category}:{asset_name}")[:8]`。
  资产顺序变化不会让编辑挂错条目；`img_id`（`IMG-CHAR-001`）只做展示标签。
- `draft → edited → approved`：`archive.py` 对 draft 覆盖、对 edited/approved 跳过并写
  `.generated.md` 对照副本；`approve` 只能从 edited 迁移，`regenerate` 用对照副本回到 draft。

## 路径安全

所有来自 API 的 `novel` 与 `id` 都先经 `resolve()` 越界校验：解析后的路径必须仍在
`<asset_root>` 内，且 novel 只能是 asset_root 的直接子目录、id 只能命中清单里的条目。
禁止通过 id 读到 `<asset_root>` 外或清单未登记的文件。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from ..core.schemas import (
    AssetDocument,
    AssetManifest,
    ChapterDoc,
    ChapterEntry,
    NovelAssetSummary,
    PromptDetail,
    PromptEntry,
)

MANIFEST_NAME = "_manifest.json"
GENERATED_SUFFIX = ".generated"
# 三类资产的固定分类目录名（同时也是 API category 的中文展示名）。
# 章节目录名「章节」同属保留名，由 archive.py 直接使用。
CATEGORY_DIRS: dict[str, str] = {"character": "角色", "scene": "场景", "prop": "道具"}
CHAPTER_DIR = "章节"
# 保留的顶层目录名：资产条目名撞上这些名字时加序号，避免把条目铺进分类目录里。
RESERVED_DIRS = frozenset([*CATEGORY_DIRS.values(), CHAPTER_DIR])
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_NAME_CHARS = 80
MAX_PROMPT_CHARS = 20_000


class AssetNotFound(ValueError):
    """清单里没有该小说或该提示词条目。"""


class InvalidPrompt(ValueError):
    """提示词正文不符合 IMG-... 结构契约。"""


class InvalidTransition(ValueError):
    """状态迁移非法（如 draft 直接 approve）。"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _utc_from(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=UTC).isoformat(timespec="seconds")


def _safe_name(raw: str, fallback: str) -> str:
    text = _ILLEGAL.sub("_", str(raw or "")).strip().strip(".").strip()
    return (text or fallback)[:MAX_NAME_CHARS]


def stable_id(category: str, asset_name: str) -> str:
    """稳定 id：category + 资产名的 sha1 前 8 位；同名同类别永远得到同一个 id。"""
    digest = hashlib.sha1(f"{category}:{asset_name}".encode()).hexdigest()[:8]
    return f"img-{category}-{digest}"


def body_hash(text: str) -> str:
    return hashlib.sha256(text.rstrip().encode("utf-8")).hexdigest()


def generated_filename(path: str) -> str:
    """由主文件路径推出对照副本文件名：`X.md` → `X.generated.md`。"""
    candidate = Path(path)
    return str(candidate.with_name(candidate.stem + GENERATED_SUFFIX + candidate.suffix))


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".asset-", suffix=".tmp", dir=str(path.parent))
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


def resolve_root(root: str | Path | None) -> Path:
    """归档根目录：参数 > settings.asset_root > <仓库>/assets；相对路径按仓库根解析。"""
    from ..core.config import settings  # 延迟导入，避免 config 与生成层互相引用

    raw = root if root is not None else settings.asset_root
    repo = Path(__file__).resolve().parents[3]
    if not raw:
        return (repo / "assets").resolve()
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else repo / path).resolve()


def novel_dir(root: str | Path | None, novel: str) -> Path:
    """解析并校验小说目录：必须是 asset_root 的直接子目录，不许越界。"""
    base = resolve_root(root)
    name = _safe_name(novel, "未命名小说")
    candidate = (base / name).resolve()
    if candidate.parent != base:
        raise AssetNotFound(f"小说名非法：{novel}")
    return candidate


def manifest_path(root: str | Path | None, novel: str) -> Path:
    return novel_dir(root, novel) / MANIFEST_NAME


def load_manifest(root: str | Path | None, novel: str) -> AssetManifest:
    path = manifest_path(root, novel)
    if not path.exists():
        return AssetManifest(novel=_safe_name(novel, "未命名小说"), entries=[])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return AssetManifest.model_validate(payload)
    except (OSError, ValueError) as exc:
        raise AssetNotFound(f"小说「{novel}」的清单损坏：{exc}") from exc


def save_manifest(root: str | Path | None, novel: str, manifest: AssetManifest) -> None:
    manifest.updated_at = utc_now()
    path = manifest_path(root, novel)
    _atomic_write(path, manifest.model_dump_json(indent=2, ensure_ascii=False))


def list_novels(root: str | Path | None = None) -> list[NovelAssetSummary]:
    """列出 asset_root 下所有含清单的小说目录。清单不存在的不算资产库条目。

    只有章节目录、没有提示词条目的小说也算（`assets_only` 之外仍可能只落了章节），
    章节数按磁盘统计。
    """
    base = resolve_root(root)
    if not base.exists():
        return []
    summaries: list[NovelAssetSummary] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        try:
            manifest = load_manifest(base, child.name)
        except AssetNotFound:
            continue
        chapters = _count_chapters(base, child.name)
        if not manifest.entries and not chapters:
            continue
        counts: dict[str, int] = {"draft": 0, "edited": 0, "approved": 0}
        for entry in manifest.entries:
            counts[entry.status] = counts.get(entry.status, 0) + 1
        summaries.append(
            NovelAssetSummary(
                name=child.name,
                title=manifest.novel,
                updated_at=manifest.updated_at or _utc_from(child.stat().st_mtime),
                entries=len(manifest.entries),
                draft=counts.get("draft", 0),
                edited=counts.get("edited", 0),
                approved=counts.get("approved", 0),
                chapters=chapters,
            )
        )
    return summaries


def list_prompts(root: str | Path | None, novel: str) -> list[PromptEntry]:
    manifest = load_manifest(root, novel)
    return sorted(manifest.entries, key=lambda e: (e.category, e.asset_name))


def _chapter_sort_key(name: str) -> tuple[int, int, str]:
    """章节排序：先按标题里的数字（第1章 < 第2章 < 第10章），没有数字的排后面按名。"""
    match = re.search(r"\d+", name)
    return (0, int(match.group(0)), name) if match else (1, 0, name)


def list_chapters(root: str | Path | None, novel: str) -> list[ChapterEntry]:
    """列出 `章节/<章节标题>/` 下的全部文档。

    章节不进清单（没有 `IMG-...` 编号、没有三态），所以直接读磁盘目录。只认 `章节/`
    这一层固定目录下的直接子目录，非目录与隐藏项跳过。
    """
    base = novel_dir(root, novel)
    chapter_root = base / CHAPTER_DIR
    if not chapter_root.is_dir():
        return []
    entries: list[ChapterEntry] = []
    for child in sorted(chapter_root.iterdir(), key=lambda p: _chapter_sort_key(p.name)):
        if not child.is_dir() or child.name.startswith("."):
            continue
        docs: list[ChapterDoc] = []
        for doc in sorted(child.iterdir()):
            if not doc.is_file() or doc.suffix.lower() != ".md":
                continue
            docs.append(
                ChapterDoc(
                    name=doc.name,
                    path=f"{CHAPTER_DIR}/{child.name}/{doc.name}",
                    size=doc.stat().st_size,
                    updated_at=_utc_from(doc.stat().st_mtime),
                )
            )
        entries.append(
            ChapterEntry(title=child.name, path=f"{CHAPTER_DIR}/{child.name}", docs=docs)
        )
    return entries


def read_document(root: str | Path | None, novel: str, path: str) -> AssetDocument:
    """读小说目录下任意一份归档 Markdown（章节文档只读；提示词走 `read_prompt`）。

    路径安全：解析后必须仍在小说目录内，且只接受 `.md`，禁止借 `..` 跳出。
    """
    base = novel_dir(root, novel)
    relative = str(path or "").replace("\\", "/").strip("/")
    if not relative:
        raise AssetNotFound("文档路径不能为空")
    target = (base / relative).resolve()
    if base not in target.parents:
        raise AssetNotFound(f"文档路径越界：{path}")
    if target.suffix.lower() != ".md":
        raise AssetNotFound(f"只允许读取 Markdown 文档：{path}")
    if not target.is_file():
        raise AssetNotFound(f"文档不存在：{path}")
    return AssetDocument(
        path=relative,
        name=target.name,
        body=target.read_text(encoding="utf-8"),
    )


def _count_chapters(root: str | Path | None, novel: str) -> int:
    try:
        return len(list_chapters(root, novel))
    except (AssetNotFound, OSError):
        return 0


def _resolve_entry_path(root: str | Path | None, novel: str, entry: PromptEntry) -> Path:
    """清单里的相对路径 → 绝对路径，仍需在小说目录内。"""
    base = novel_dir(root, novel)
    target = (base / entry.path).resolve()
    if target.parent == base.parent or base not in target.parents:
        raise AssetNotFound(f"提示词路径越界：{entry.path}")
    return target


def _find_entry(root: str | Path | None, prompt_id: str) -> tuple[str, AssetManifest, PromptEntry]:
    """按 id 在全部小说清单里定位条目；id 必须命中清单，不接受任意路径。"""
    base = resolve_root(root)
    if not base.exists():
        raise AssetNotFound(f"未找到提示词：{prompt_id}")
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        try:
            manifest = load_manifest(base, child.name)
        except AssetNotFound:
            continue
        for entry in manifest.entries:
            if entry.id == prompt_id:
                return child.name, manifest, entry
    raise AssetNotFound(f"未找到提示词：{prompt_id}")


def read_prompt(root: str | Path | None, prompt_id: str) -> PromptDetail:
    novel, _, entry = _find_entry(root, prompt_id)
    path = _resolve_entry_path(root, novel, entry)
    if not path.exists():
        raise AssetNotFound(f"提示词文件不存在：{entry.path}")
    body = path.read_text(encoding="utf-8")
    generated = Path(generated_filename(str(path)))
    detail = PromptDetail(
        **entry.model_dump(),
        body=body,
        generated_body=generated.read_text(encoding="utf-8") if generated.exists() else "",
    )
    return detail


def validate_body(text: str) -> str:
    """保存前校验：非空、长度上限、保留 `## IMG-...` 与 `### 可复制提示词` 结构。"""
    body = str(text or "").strip()
    if not body:
        raise InvalidPrompt("提示词正文不能为空")
    if len(body) > MAX_PROMPT_CHARS:
        raise InvalidPrompt(f"提示词正文超过 {MAX_PROMPT_CHARS} 字符上限")
    if not re.search(r"^## IMG-[A-Z]+-[0-9]{3}", body, re.MULTILINE):
        raise InvalidPrompt("正文缺少 `## IMG-...` 条目标题，结构校验失败")
    if "### 可复制提示词" not in body:
        raise InvalidPrompt("正文缺少 `### 可复制提示词` 段落，结构校验失败")
    return body


def save_prompt(root: str | Path | None, prompt_id: str, text: str) -> PromptDetail:
    """保存编辑：校验结构、原子写回、状态置 edited、刷新 hash。approved 也允许改，改后回 edited。"""
    body = validate_body(text)
    novel, manifest, entry = _find_entry(root, prompt_id)
    path = _resolve_entry_path(root, novel, entry)
    _atomic_write(path, body)
    entry.body_hash = body_hash(body)
    entry.status = "edited"
    entry.updated_at = utc_now()
    save_manifest(root, novel, manifest)
    return PromptDetail(**entry.model_dump(), body=body, generated_body="")


def approve_prompt(root: str | Path | None, prompt_id: str) -> PromptDetail:
    """定稿：只允许 edited → approved；draft 必须先保存编辑。"""
    novel, manifest, entry = _find_entry(root, prompt_id)
    if entry.status != "edited":
        raise InvalidTransition(f"只有 edited 状态可定稿，当前为 {entry.status}")
    entry.status = "approved"
    entry.updated_at = utc_now()
    save_manifest(root, novel, manifest)
    return read_prompt(root, prompt_id)


def regenerate_prompt(root: str | Path | None, prompt_id: str) -> PromptDetail:
    """人工接受模板版本：用 `.generated.md` 覆盖主文件，状态回 draft。没有对照副本则拒绝。"""
    novel, manifest, entry = _find_entry(root, prompt_id)
    path = _resolve_entry_path(root, novel, entry)
    generated = Path(generated_filename(str(path)))
    if not generated.exists():
        raise InvalidTransition("没有模板对照版本，无法重渲染")
    body = generated.read_text(encoding="utf-8")
    _atomic_write(path, body)
    generated.unlink()
    entry.body_hash = body_hash(body)
    entry.status = "draft"
    entry.updated_at = utc_now()
    save_manifest(root, novel, manifest)
    return PromptDetail(**entry.model_dump(), body=body, generated_body="")
