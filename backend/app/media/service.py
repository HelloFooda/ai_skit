"""出图编排：preview → confirm → generate → 落盘 → 清单。

## 为什么这一层不做成 LangGraph 图

S7 出图**没有决策点**：拿到 `IMG-...` 提示词就调一次 provider、落盘、登记，没有"模型自己
决定下一步"的余地。套 `create_agent` 只会白烧一次 LLM 调用，套 StateGraph 也只是把一个
线性流程画成图。项目里 LangGraph 的用途是"顺序集中表达"，而这里的顺序只有三步，且必须
由确认码守住——确认码是**人**的动作，不是图的状态转移。所以本层是普通函数，
真正需要图的批量编排（多条目、失败重跑、`interrupt()` 人工确认）留给后续阶段。

## 与技能包的三步对应

| 技能包 | 这里 | 花钱 |
| --- | --- | --- |
| `prepare`（校验 + 预览计费） | `preview()` | 否 |
| `confirm`（写确认码） | 前端拿到确认码，用户点击后回传 | 否 |
| `run`（提交） | `generate_stream()` | 是 |
| `status` / `collect` | `collect()` | 否 |

## 幂等与缓存

同一指纹（提示词正文 + provider + model + size + ratio + seed + 参考图）已经成功出过图，
就**直接返回那张图**，不再提交。这既是省钱，也是"重跑只覆盖该 stage 产物"不变式的实现。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from loguru import logger

from ..generation.asset_store import _find_entry, _resolve_entry_path
from . import jobs as job_store
from .contract import MediaError, MediaRequest, MediaResult
from .providers.registry import build_image_provider
from .settings import settings
from .store import (
    ImageNotFound,
    current_body_hash,
    image_dir_for,
    list_images,
    read_sidecar,
    save_image,
    select_image,
)

_COPY_BLOCK = re.compile(r"###\s*可复制提示词\s*\n+(.*?)(?=\n##\s|\n###\s|\Z)", re.DOTALL)


def extract_prompt_body(markdown: str) -> str:
    """从 `IMG-...` 文档里取出 `### 可复制提示词` 下的引用块正文，逐字保留。"""
    match = _COPY_BLOCK.search(markdown)
    if not match:
        raise ValueError("该条目缺少 `### 可复制提示词` 段落，无法出图")
    lines: list[str] = []
    for raw in match.group(1).splitlines():
        line = raw.strip()
        if not line:
            if lines:
                break
            continue
        if not line.startswith(">"):
            break
        lines.append(line.lstrip(">").strip())
    body = "\n".join(lines).strip()
    if not body:
        raise ValueError("`### 可复制提示词` 段落为空，无法出图")
    return body


def _prompt_context(root: str | Path | None, prompt_id: str) -> tuple[str, Any, str, Path]:
    """(小说名, 清单条目, 提示词正文, 条目目录)。正文每次从文件读，清单只给定位。"""
    novel, _, entry = _find_entry(root, prompt_id)
    path = _resolve_entry_path(root, novel, entry)
    if not path.is_file():
        raise ImageNotFound(f"提示词文件不存在：{entry.path}")
    return novel, entry, path.read_text(encoding="utf-8"), path.parent


def _cached_image(root: str | Path | None, novel: str, prompt_id: str, fingerprint: str) -> Any:
    for image in list_images(root, novel, prompt_id=prompt_id):
        if image.fingerprint == fingerprint:
            return image
    return None


def preview(
    root: str | Path | None, prompt_id: str, *, seed: int | None = None
) -> dict[str, Any]:
    """算账不花钱：算指纹、给确认码、报能力缺口与额度。"""
    novel, entry, markdown, _ = _prompt_context(root, prompt_id)
    body = extract_prompt_body(markdown)
    provider = build_image_provider()
    _, image_dir = image_dir_for(root, prompt_id)

    job = job_store.build_job(
        novel=novel,
        prompt_id=prompt_id,
        img_id=entry.img_id,
        title=entry.asset_name,
        category=entry.category,
        asset_name=entry.asset_name,
        body=body,
        provider=provider.name,
        model=provider.model,
        size=settings.image_size,
        ratio=settings.image_ratio,
        seed=seed,
        refs=[],
        output_dir=str(image_dir),
    )
    previous = job_store.load_job(root, novel, job.job_id)
    # 已有同指纹的完成记录 → 直接把状态带过来，前端可以显示"无需重跑"。
    if previous and previous.fingerprint == job.fingerprint and previous.state == "done":
        job = previous
    else:
        job_store.save_job(root, job)

    request = job_store.to_request(job, body)
    issues = provider.capabilities.problems(request)
    usage = job_store.today_usage(root)
    cached = _cached_image(root, novel, prompt_id, job.fingerprint)
    estimate = round(settings.cost_per_image * request.n, 4) if settings.cost_per_image else None

    return {
        "job_id": job.job_id,
        "novel": novel,
        "prompt_id": prompt_id,
        "img_id": entry.img_id,
        "title": entry.asset_name,
        "category": entry.category,
        "confirmation": job.confirmation,
        "fingerprint": job.fingerprint,
        "prompt_hash": job.prompt_hash,
        "prompt_chars": len(body),
        "provider": provider.name,
        "model": provider.model,
        "size": job.size,
        "ratio": job.ratio,
        "seed": job.seed,
        "n": request.n,
        "state": job.state,
        "blocking": issues,
        "warnings": [item for item in [provider_watermark_warning()] if item],
        "cost_estimate": estimate,
        "allow_paid": settings.allow_paid,
        "quota": usage,
        "cached": cached.model_dump(mode="json") if cached else None,
        "images": [item.model_dump(mode="json") for item in list_images(root, novel, prompt_id=prompt_id)],
        "prompt_stale": bool(cached and cached.fingerprint != job.fingerprint),
    }


def provider_watermark_warning() -> str:
    if settings.image_watermark:
        return "MEDIA_IMAGE_WATERMARK 为开启状态：产物会带厂商水印，交付前需关闭重出"
    return ""


def generate_stream(
    root: str | Path | None, prompt_id: str, confirmation: str
) -> Iterator[tuple[str, dict[str, Any]]]:
    """提交出图，边做边报进度。产出 `(event, payload)` 供路由层包成 SSE。"""
    novel, entry, markdown, _ = _prompt_context(root, prompt_id)
    body = extract_prompt_body(markdown)
    provider = build_image_provider()

    job = job_store.load_job(root, novel, job_store.job_id_for(novel, prompt_id))
    if job is None:
        raise ValueError("没有预览记录，请先预览再确认")

    # 正文可能在上次预览后被改过：指纹重算，旧确认码自然失效。
    _, image_dir = image_dir_for(root, prompt_id)
    fresh = job_store.build_job(
        novel=novel,
        prompt_id=prompt_id,
        img_id=entry.img_id,
        title=entry.asset_name,
        category=entry.category,
        asset_name=entry.asset_name,
        body=body,
        provider=provider.name,
        model=provider.model,
        size=job.size,
        ratio=job.ratio,
        seed=job.seed,
        refs=[],
        output_dir=str(image_dir),
    )
    job_store.check_confirmation(fresh, confirmation)

    cached = _cached_image(root, novel, prompt_id, fresh.fingerprint)
    if cached is not None:
        yield ("progress", {"phase": "cache", "text": "命中同指纹的已有产物，未重新提交", "step": 1, "total": 3})
        yield ("media", cached.model_dump(mode="json"))
        yield ("done", {"cached": True, "image": cached.model_dump(mode="json")})
        return

    request = job_store.to_request(fresh, body)
    issues = provider.capabilities.problems(request)
    if issues:
        raise ValueError("；".join(issues))

    # 额度是本地闸门：超限时**不核销**确认码。
    job_store.check_quota(root)

    yield ("progress", {"phase": "submit", "text": f"提交给 {provider.name} / {provider.model}", "step": 1, "total": 3})
    job_store.mark_submitted(root, fresh)

    result: MediaResult | None = None
    error: MediaError | None = None
    try:
        result = provider.generate(
            request,
            out_dir=image_dir,
            handle=lambda provider_job_id: _record_handle(root, fresh, provider_job_id),
        )
    except MediaError as exc:
        error = exc
    except Exception as exc:  # noqa: BLE001 - 任何异常都要落状态，不能让任务卡在 submitted
        error = MediaError(
            f"出图异常：{type(exc).__name__}",
            category="provider_error",
            retryable=False,
            submitted=True,
            detail=str(exc),
        )

    if error is not None:
        # 请求已发出（或状态未知）→ 核销并记一次额度；本地预检类错误不核销，确认码仍可用。
        if error.submitted:
            job_store.record_usage(root, cost=0.0)
        job_store.mark_failed(root, fresh, error.as_public(), submitted=error.submitted)
        logger.bind(
            event="media_generate_failed",
            job_id=fresh.job_id,
            category=error.category,
            submitted=error.submitted,
            http_status=error.http_status,
            detail=error.detail,
        ).error(f"media generate failed: {error.detail}")
        yield ("error", error.as_public())
        return

    assert result is not None
    if not result.assets:
        failure = MediaError("厂商未返回任何产物", category="provider_error", submitted=True)
        job_store.record_usage(root, cost=0.0)
        job_store.mark_failed(root, fresh, failure.as_public(), submitted=True)
        yield ("error", failure.as_public())
        return

    job_store.record_usage(root, cost=float(result.cost_estimate or 0.0), count=len(result.assets))
    yield ("progress", {"phase": "save", "text": "落盘并登记清单", "step": 2, "total": 3})

    records = []
    for asset in result.assets:
        record = save_image(
            root,
            prompt_id=prompt_id,
            source=asset.path,
            job=fresh.model_dump(mode="json"),
            provider_result=asset.model_dump(mode="json")
            | {
                "usage": result.usage,
                "cost_estimate": result.cost_estimate,
                "latency_ms": result.latency_ms,
                "appended_suffix": result.appended_suffix,
                "meta": result.meta,
            },
        )
        records.append(record)
        yield ("media", record.model_dump(mode="json"))

    job_store.mark_done(
        root,
        fresh,
        images=[item.model_dump(mode="json") for item in records],
        provider_job_id=result.provider_job_id,
    )
    logger.bind(
        event="media_generate_done",
        job_id=fresh.job_id,
        provider=result.provider,
        model=result.model,
        images=len(records),
        latency_ms=result.latency_ms,
    ).info("media generate done")
    yield (
        "done",
        {
            "cached": False,
            "images": [item.model_dump(mode="json") for item in records],
            "latency_ms": result.latency_ms,
            "cost_estimate": result.cost_estimate,
            "appended_suffix": result.appended_suffix,
        },
    )


def _record_handle(root: str | Path | None, job: job_store.MediaJob, provider_job_id: str) -> None:
    """异步厂商拿到任务号：**先落盘再轮询**，断线也能 collect 找回。"""
    job.provider_job_id = provider_job_id
    job_store.save_job(root, job)


def collect(root: str | Path | None, novel: str, job_id: str) -> dict[str, Any]:
    """只取既有远端结果，绝不重复提交。"""
    job = job_store.load_job(root, novel, job_id)
    if job is None:
        raise ImageNotFound(f"未找到任务：{job_id}")
    if not job.provider_job_id:
        raise ValueError("该任务没有远端任务号，无可收集的结果（同步厂商请直接重跑）")
    provider = build_image_provider()
    _, _entry, markdown, _ = _prompt_context(root, job.prompt_id)
    body = extract_prompt_body(markdown)
    _, image_dir = image_dir_for(root, job.prompt_id)
    result = provider.collect(job.provider_job_id, job_store.to_request(job, body), out_dir=image_dir)
    records = [
        save_image(
            root,
            prompt_id=job.prompt_id,
            source=asset.path,
            job=job.model_dump(mode="json"),
            provider_result=asset.model_dump(mode="json")
            | {
                "usage": result.usage,
                "cost_estimate": result.cost_estimate,
                "latency_ms": result.latency_ms,
                "appended_suffix": result.appended_suffix,
                "meta": result.meta,
            },
        )
        for asset in result.assets
    ]
    job_store.mark_done(
        root, job, images=[item.model_dump(mode="json") for item in records]
    )
    return {"job_id": job_id, "images": [item.model_dump(mode="json") for item in records]}


def job_status(root: str | Path | None, novel: str) -> dict[str, Any]:
    jobs = job_store.list_jobs(root, novel)
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.state] = counts.get(job.state, 0) + 1
    return {
        "novel": novel,
        "jobs": [job.model_dump(mode="json") for job in jobs],
        "counts": counts,
        "quota": job_store.today_usage(root),
    }


def images_for(root: str | Path | None, novel: str, *, prompt_id: str = "") -> dict[str, Any]:
    records = list_images(root, novel, prompt_id=prompt_id)
    hashes = {}
    if prompt_id:
        hashes[prompt_id] = current_body_hash(root, prompt_id)
    return {
        "novel": novel,
        "images": [item.model_dump(mode="json") for item in records],
        "current_body_hash": hashes,
    }


__all__ = [
    "MediaRequest",
    "collect",
    "extract_prompt_body",
    "generate_stream",
    "images_for",
    "job_status",
    "preview",
    "read_sidecar",
    "select_image",
]
