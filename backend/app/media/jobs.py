"""出图任务闸门：指纹、确认码、状态机、日额度。

## 三条硬语义（对齐技能包 `$short-drama-produce` 的 prepare→confirm→run）

1. **不确认不提交**：`preview` 只算账、不花钱；必须拿到与当前指纹绑定的确认码
   `CONFIRM <job_id> <fingerprint[:12]>` 才允许 `generate`。
2. **改了东西旧码自动失效**：指纹 = sha256(提示词正文 hash + provider + model +
   size + ratio + seed + 参考图 hash)。改一个字、换个模型、换个尺寸，指纹就变，
   旧确认码立刻作废——不需要额外的失效通知。
3. **`submitted` 决定是否核销**：本地预检失败（缺 key / 缺模型 / 超额度 / 能力不符）
   一律 `submitted=False`，确认码保留，修好环境可直接重跑；请求一旦发出即为
   `submitted=True`，重试必须重新确认。这是防重复扣费的唯一闸门。

## 为什么状态落文件

出图是**付费且不可撤销**的动作，进程重启、前端刷新、SSE 断开都不该让"这张图到底
提交没有"变成一个谜。所以每次状态变化都原子写 `assets/<小说>/_media/jobs/<job_id>.json`，
`handle_path`（远端任务号）也落在这里——断线后能 `collect` 找回，而不是重新提交。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..generation.asset_store import novel_dir, resolve_root
from .contract import MediaRef, MediaRequest, sanitize_detail
from .settings import settings

JobState = Literal["planned", "confirmed", "submitted", "done", "failed", "collected"]

MEDIA_DIR = "_media"
LEDGER_NAME = "_media_ledger.json"
MAX_JOBS = 500


class ConfirmationRequired(ValueError):
    """确认码与当前指纹不匹配（或没确认）。"""


class QuotaExceeded(ValueError):
    """日额度已用完，熔断提交。"""


class MediaJob(BaseModel):
    """一条出图任务的持久记录。正文不存这里，只存 hash 与指纹。"""

    job_id: str = Field(description="稳定 id：mj-<sha1(novel:prompt_id)[:10]>")
    novel: str
    prompt_id: str
    img_id: str = ""
    title: str = ""
    category: str = ""
    asset_name: str = ""
    prompt_hash: str = Field(description="提示词正文 sha256；正文本身不进任务文件")
    fingerprint: str = Field(description="prompt+provider+model+size+ratio+seed+refs 的 sha256")
    provider: str = ""
    model: str = ""
    size: str = ""
    ratio: str = ""
    seed: int | None = None
    refs: list[dict[str, Any]] = Field(default_factory=list)
    state: JobState = "planned"
    provider_job_id: str | None = None
    output_dir: str = ""
    images: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    attempts: int = 0
    created_at: str = ""
    updated_at: str = ""

    @property
    def confirmation(self) -> str:
        return f"CONFIRM {self.job_id} {self.fingerprint[:12]}"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def job_id_for(novel: str, prompt_id: str) -> str:
    """一条提示词条目 = 一条稳定任务；版本靠指纹区分，不靠 id 漂移。"""
    digest = hashlib.sha1(f"{novel}:{prompt_id}".encode()).hexdigest()[:10]
    return f"mj-{digest}"


def compute_fingerprint(
    *,
    prompt_hash: str,
    provider: str,
    model: str,
    size: str,
    ratio: str,
    seed: int | None,
    refs: list[dict[str, Any]],
) -> str:
    payload = json.dumps(
        {
            "prompt": prompt_hash,
            "provider": provider,
            "model": model,
            "size": size,
            "ratio": ratio,
            "seed": seed,
            "refs": sorted(
                [{"role": r.get("role"), "sha256": r.get("sha256")} for r in refs],
                key=lambda item: (str(item["role"]), str(item["sha256"])),
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ 路径


def media_dir(root: str | Path | None, novel: str) -> Path:
    return novel_dir(root, novel) / MEDIA_DIR


def jobs_dir(root: str | Path | None, novel: str) -> Path:
    return media_dir(root, novel) / "jobs"


def job_path(root: str | Path | None, novel: str, job_id: str) -> Path:
    if not job_id.startswith("mj-") or not job_id[3:].isalnum():
        raise ValueError("任务 id 非法")
    return jobs_dir(root, novel) / f"{job_id}.json"


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".media-", suffix=".tmp", dir=str(path.parent))
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


def load_job(root: str | Path | None, novel: str, job_id: str) -> MediaJob | None:
    path = job_path(root, novel, job_id)
    if not path.exists():
        return None
    try:
        return MediaJob.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_job(root: str | Path | None, job: MediaJob) -> MediaJob:
    job.updated_at = utc_now()
    _atomic_json(job_path(root, job.novel, job.job_id), job.model_dump(mode="json"))
    return job


def list_jobs(root: str | Path | None, novel: str) -> list[MediaJob]:
    directory = jobs_dir(root, novel)
    if not directory.is_dir():
        return []
    jobs: list[MediaJob] = []
    for path in sorted(directory.glob("mj-*.json")):
        try:
            jobs.append(MediaJob.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return jobs


# ------------------------------------------------------------------ 日额度


def ledger_path(root: str | Path | None = None) -> Path:
    return resolve_root(root) / LEDGER_NAME


def read_ledger(root: str | Path | None = None) -> dict[str, Any]:
    path = ledger_path(root)
    if not path.exists():
        return {"days": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"days": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("days"), dict):
        return {"days": {}}
    return payload


def today_usage(root: str | Path | None = None) -> dict[str, Any]:
    day = date.today().isoformat()
    entry = read_ledger(root)["days"].get(day) or {}
    return {
        "date": day,
        "submitted": int(entry.get("submitted") or 0),
        "cost": float(entry.get("cost") or 0.0),
        "quota": settings.daily_quota,
        "remaining": max(0, settings.daily_quota - int(entry.get("submitted") or 0))
        if settings.daily_quota
        else None,
    }


def check_quota(root: str | Path | None = None) -> None:
    """提交前调用。超限抛 `QuotaExceeded`——这属于本地预检，不核销确认码。"""
    if settings.daily_quota <= 0:
        return
    usage = today_usage(root)
    if usage["submitted"] >= settings.daily_quota:
        raise QuotaExceeded(
            f"今日提交已达上限 {settings.daily_quota} 次，熔断停止；明日再跑或调整 MEDIA_DAILY_QUOTA"
        )


def record_usage(root: str | Path | None, *, cost: float = 0.0, count: int = 1) -> None:
    """只在请求**真正发出后**调用（成功或失败都算一次提交）。"""
    path = ledger_path(root)
    document = read_ledger(root)
    day = date.today().isoformat()
    entry = document["days"].setdefault(day, {"submitted": 0, "cost": 0.0})
    entry["submitted"] = int(entry.get("submitted") or 0) + count
    entry["cost"] = round(float(entry.get("cost") or 0.0) + cost, 4)
    document["updated_at"] = utc_now()
    # 只保留最近 60 天，避免账本无限增长
    days = document["days"]
    for stale in sorted(days)[:-60]:
        days.pop(stale, None)
    _atomic_json(path, document)


# ------------------------------------------------------------------ 建任务


def build_job(
    *,
    novel: str,
    prompt_id: str,
    img_id: str,
    title: str,
    category: str,
    asset_name: str,
    body: str,
    provider: str,
    model: str,
    size: str,
    ratio: str,
    seed: int | None,
    refs: list[MediaRef],
    output_dir: str,
) -> MediaJob:
    """由提示词正文与当前配置算出一条任务；指纹变化即视为新版本。"""
    ref_payload: list[dict[str, Any]] = []
    for ref in refs:
        path = Path(ref.path)
        ref_payload.append(
            {
                "role": ref.role,
                "path": str(path),
                "label": ref.label,
                "sha256": _file_sha256(path),
            }
        )
    prompt_hash = sha256_text(body)
    return MediaJob(
        job_id=job_id_for(novel, prompt_id),
        novel=novel,
        prompt_id=prompt_id,
        img_id=img_id,
        title=title,
        category=category,
        asset_name=asset_name,
        prompt_hash=prompt_hash,
        fingerprint=compute_fingerprint(
            prompt_hash=prompt_hash,
            provider=provider,
            model=model,
            size=size,
            ratio=ratio,
            seed=seed,
            refs=ref_payload,
        ),
        provider=provider,
        model=model,
        size=size,
        ratio=ratio,
        seed=seed,
        refs=ref_payload,
        output_dir=output_dir,
        created_at=utc_now(),
        updated_at=utc_now(),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def to_request(job: MediaJob, prompt: str) -> MediaRequest:
    """任务记录 + 当前正文 → provider 请求。正文每次从文件读，任务里只有 hash。"""
    return MediaRequest(
        modality="image",
        prompt=prompt,
        refs=[
            MediaRef(
                # role 是 Literal 别名，不能当函数调用；交给 pydantic 校验取值。
                role=ref["role"],
                path=Path(ref["path"]),
                label=str(ref.get("label") or ""),
            )
            for ref in job.refs
        ],
        size=job.size,
        ratio=job.ratio,
        seed=job.seed,
        n=1,
    )


def check_confirmation(job: MediaJob, confirmation: str) -> None:
    """确认码必须与**当前**指纹逐字一致；不匹配一律拒绝，不做模糊匹配。"""
    expected = job.confirmation
    if (confirmation or "").strip() != expected:
        raise ConfirmationRequired(
            f"确认码不匹配。当前任务指纹已变化，请重新预览并确认：{expected}"
        )


def mark_submitted(root: str | Path | None, job: MediaJob) -> MediaJob:
    job.state = "submitted"
    job.attempts += 1
    job.error = None
    return save_job(root, job)


def mark_done(
    root: str | Path | None,
    job: MediaJob,
    *,
    images: list[dict[str, Any]],
    provider_job_id: str | None = None,
) -> MediaJob:
    job.state = "done"
    job.images = images
    job.provider_job_id = provider_job_id or job.provider_job_id
    job.error = None
    return save_job(root, job)


def mark_failed(
    root: str | Path | None, job: MediaJob, error: dict[str, Any], *, submitted: bool
) -> MediaJob:
    """`submitted=False` 的失败**不核销**：状态退回 confirmed，确认码仍有效。"""
    job.state = "failed" if submitted else "confirmed"
    job.error = {**error, "detail": sanitize_detail(error.get("detail"))}
    return save_job(root, job)
