"""出图落盘：`图片/` 目录、版本号、sidecar、清单索引、路径安全。

## 目录与命名

```
assets/<小说名>/<分类目录>/<条目名>/
├── <条目名>.md
├── <条目名>-图片提示词.md
└── 图片/                        # ★ 本模块负责
    ├── <条目名>-v1.png
    ├── <条目名>-v1.json         # sidecar：完整技术档案
    ├── <条目名>-v2.png
    └── <条目名>-v2.json
```

**换版不删旧图**（技能包 S3 硬规则：`男主-v2.png` 留着能退）。版本号取该条目已有最大
版本 +1，永不复用；因此"这张图是哪次生成的"永远可回溯。

## 职责切分

- **manifest.images[]**：索引，只放列表页要用的字段（路径 / 版本 / 尺寸 / 摘要 / 是否选用）。
  与既有 `entries[]` 同口径——清单存路径与 hash，不存正文。
- **sidecar `<条目名>-vN.json`**：单张图的完整技术档案（指纹、seed、参数、耗时、成本、
  provider 追加尾巴、提示词 hash）。技术细节只在这里，清单不被撑爆。

## 路径安全

所有对外路径（`GET /api/media/file?path=`）都必须 resolve 后落在**该小说目录内**，
且后缀只允许 png/jpg/webp；清单里没有登记的路径同样拒绝读。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core.schemas import MediaImageRecord
from ..generation.asset_store import (
    _atomic_write,
    _find_entry,
    _resolve_entry_path,
    body_hash,
    load_manifest,
    novel_dir,
    resolve_root,
    save_manifest,
)

IMAGE_DIR = "图片"
SIDECAR_SUFFIX = ".json"
ALLOWED_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class ImageNotFound(ValueError):
    """清单里没有这张图，或文件已不在磁盘上。"""


class ImagePathRejected(ValueError):
    """路径越界或后缀不允许。"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".img-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def image_dir_for(root: str | Path | None, prompt_id: str) -> tuple[str, Path]:
    """提示词所在条目目录下的 `图片/`。返回 (小说名, 绝对目录)。"""
    novel, _, entry = _find_entry(root, prompt_id)
    prompt_path = _resolve_entry_path(root, novel, entry)
    return novel, prompt_path.parent / IMAGE_DIR


def next_version(directory: Path, asset_name: str) -> int:
    """扫描同条目下已有 `-vN.*`，返回 max+1。不复用版本号。"""
    if not directory.is_dir():
        return 1
    highest = 0
    prefix = f"{asset_name}-v"
    for path in directory.iterdir():
        if not path.is_file() or not path.name.startswith(prefix):
            continue
        tail = path.stem[len(prefix) :]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return highest + 1


def image_id_for(prompt_id: str, version: int) -> str:
    """稳定 id：同一条目的同一个版本永远同一个 id，重跑不会产生重复登记。"""
    return "mi-" + hashlib.sha1(f"{prompt_id}:v{version}".encode()).hexdigest()[:10]


def _safe_relative(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError as exc:
        raise ImagePathRejected(f"路径越界：{path}") from exc


def save_image(
    root: str | Path | None,
    *,
    prompt_id: str,
    source: Path,
    job: dict[str, Any],
    provider_result: dict[str, Any],
) -> MediaImageRecord:
    """把 provider 产出的文件收进 `图片/`，写 sidecar，登记清单。

    `source` 是 provider 写在自己临时目录里的文件；这里**移动**（不是复制）到最终位置，
    避免同一张图在磁盘上留两份。文件名由版本号决定，与源文件名无关。
    """
    novel, directory = image_dir_for(root, prompt_id)
    _, manifest, entry = _find_entry(root, prompt_id)
    directory.mkdir(parents=True, exist_ok=True)
    version = next_version(directory, entry.asset_name)
    suffix = Path(source).suffix.lower() or ".png"
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        suffix = ".png"
    target = directory / f"{entry.asset_name}-v{version}{suffix}"
    sidecar = directory / f"{entry.asset_name}-v{version}{SIDECAR_SUFFIX}"
    base = novel_dir(root, novel)

    # 同一文件系统内 rename 即可；跨盘退化为拷贝 + 删除。
    try:
        os.replace(source, target)
    except OSError:
        target.write_bytes(Path(source).read_bytes())
        try:
            Path(source).unlink()
        except OSError:
            pass

    content = target.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    record = MediaImageRecord(
        id=image_id_for(prompt_id, version),
        prompt_id=prompt_id,
        novel=novel,
        category=entry.category,
        asset_name=entry.asset_name,
        img_id=entry.img_id,
        version=version,
        path=_safe_relative(target, base),
        sidecar=_safe_relative(sidecar, base),
        mime=str(provider_result.get("mime") or "image/png"),
        bytes=len(content),
        sha256=digest,
        width=provider_result.get("width"),
        height=provider_result.get("height"),
        provider=str(job.get("provider") or ""),
        model=str(job.get("model") or ""),
        size=str(job.get("size") or ""),
        ratio=str(job.get("ratio") or ""),
        seed=job.get("seed"),
        fingerprint=str(job.get("fingerprint") or ""),
        job_id=str(job.get("job_id") or ""),
        selected=False,
        created_at=utc_now(),
    )
    _atomic_json(
        sidecar,
        {
            **record.model_dump(mode="json"),
            "prompt_hash": str(job.get("prompt_hash") or ""),
            "provider_job_id": job.get("provider_job_id"),
            "usage": provider_result.get("usage") or {},
            "cost_estimate": provider_result.get("cost_estimate"),
            "latency_ms": provider_result.get("latency_ms"),
            "appended_suffix": provider_result.get("appended_suffix") or "",
            "meta": provider_result.get("meta") or {},
        },
    )

    # 新出的版本即当前选用版（旧版留着能退）；人工可用 /select 切回任意旧版。
    record.selected = True
    for item in manifest.images:
        if item.prompt_id == prompt_id:
            item.selected = False
    manifest.images = [item for item in manifest.images if item.id != record.id] + [record]
    save_manifest(root, novel, manifest)
    return record


def list_images(
    root: str | Path | None, novel: str, *, prompt_id: str = ""
) -> list[MediaImageRecord]:
    manifest = load_manifest(root, novel)
    items = [item for item in manifest.images if not prompt_id or item.prompt_id == prompt_id]
    return sorted(items, key=lambda item: (item.asset_name, item.version))


def select_image(root: str | Path | None, image_id: str) -> MediaImageRecord:
    """选定某条目的当前版本：同条目其它版本自动取消选用（一条目只有一个当前版）。"""
    novel, manifest, target = _locate(root, image_id)
    for item in manifest.images:
        if item.prompt_id == target.prompt_id:
            item.selected = item.id == image_id
    save_manifest(root, novel, manifest)
    return target


def _locate(root: str | Path | None, image_id: str) -> tuple[str, Any, MediaImageRecord]:
    base = resolve_root(root)
    if not base.is_dir():
        raise ImageNotFound(f"未找到图片：{image_id}")
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        try:
            manifest = load_manifest(base, child.name)
        except ValueError:
            continue
        for item in manifest.images:
            if item.id == image_id:
                return child.name, manifest, item
    raise ImageNotFound(f"未找到图片：{image_id}")


def resolve_image_file(root: str | Path | None, novel: str, relative: str) -> Path:
    """`GET /api/media/file` 的路径校验：必须在小说目录内、后缀合法、文件存在。"""
    base = novel_dir(root, novel)
    raw = (relative or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in raw.split("/"):
        raise ImagePathRejected(f"路径非法：{relative}")
    target = (base / raw).resolve()
    if base not in target.parents:
        raise ImagePathRejected(f"路径越界：{relative}")
    if target.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
        raise ImagePathRejected("只允许读取 png / jpg / webp")
    if not target.is_file():
        raise ImageNotFound(f"文件不存在：{relative}")
    return target


def read_sidecar(root: str | Path | None, image_id: str) -> dict[str, Any]:
    novel, _, record = _locate(root, image_id)
    path = (novel_dir(root, novel) / record.sidecar).resolve()
    if not path.is_file():
        return record.model_dump(mode="json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return record.model_dump(mode="json")
    return payload if isinstance(payload, dict) else record.model_dump(mode="json")


def current_body_hash(root: str | Path | None, prompt_id: str) -> str:
    """当前提示词正文 hash：正文改了，旧图就不再代表当前提示词（前端据此提示"已过期"）。"""
    novel, _, entry = _find_entry(root, prompt_id)
    target = _resolve_entry_path(root, novel, entry)
    if not target.is_file():
        return ""
    return body_hash(target.read_text(encoding="utf-8"))


def write_text_atomic(path: Path, text: str) -> None:
    _atomic_write(path, text)
