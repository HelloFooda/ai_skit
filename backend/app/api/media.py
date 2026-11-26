"""出图接口：预览 → 确认 → 提交（SSE）→ 收集 / 查图 / 选定 / 读文件。

## 为什么 SSE 而不是长轮询

出图单张就要十几到几十秒，批量更久。前端已经有一套 `fetch` + `ReadableStream` 解析 SSE
的实现（生成主链路在用），这里直接复用同一套协议：`progress` → `media` → `done` / `error`，
不新引入轮询或 WebSocket。

## 为什么确认码由前端回传而不是服务端存

确认码是"人点了确认"的凭证。服务端只做**校验**（与当前指纹逐字比对），不做"记住你确认过"。
这样刷新页面、换浏览器都不会绕过确认，也不会有"上次确认过的状态还留着"的隐式放行。

## 路径安全

`/file` 的 `path` 必须是小说目录内的相对路径，后缀只允许 png/jpg/webp，
`resolve()` 后仍须落在小说目录内；越界一律 400。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, StreamingResponse
from loguru import logger
from starlette.concurrency import iterate_in_threadpool

from ..core.schemas import (
    MediaGenerateRequest,
    MediaImagesResponse,
    MediaJobStatusResponse,
    MediaPreviewRequest,
    MediaPreviewResponse,
    MediaRefineRequest,
    MediaRefineResponse,
)
from ..media import refine, service
from ..media.store import read_sidecar, resolve_image_file, select_image

router = APIRouter(prefix="/api/media", tags=["media"])


def _sse(event: str, payload) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


@router.post("/preview", response_model=MediaPreviewResponse)
def preview(req: MediaPreviewRequest):
    """算账不花钱：算指纹、给确认码、报阻塞项与今日额度。"""
    result = service.preview(
        None, req.prompt_id, seed=req.seed, reference_image_id=req.reference_image_id
    )
    logger.bind(
        event="media_preview",
        job_id=result["job_id"],
        provider=result["provider"],
        blocking=len(result["blocking"]),
        cached=bool(result["cached"]),
    ).info("media preview")
    return result


@router.post("/refine", response_model=MediaRefineResponse)
def refine_prompt(req: MediaRefineRequest):
    """追加描述 → LLM 补丁式融合成新正文草案。不落盘、不出图、不扣图费。

    用户看到 diff 并确认后，前端把 `after_markdown` 原样 PUT 到
    `/api/assets/prompts/{id}` 落盘；正文一改，出图确认码自动失效，
    必须重新 preview 才能带新正文出图。
    """
    result = refine.draft(None, req.prompt_id, req.addition)
    logger.bind(
        event="media_refine_draft",
        prompt_id=req.prompt_id,
        patches=len(result["patches"]),
        tokens=result["usage"].get("total_tokens", 0),
    ).info("media refine draft")
    return result


@router.post("/generate")
async def generate(req: MediaGenerateRequest):
    """提交出图。事件：`progress` 若干 → `media` 每张一条 → `done`；失败走 `error`。"""

    async def events():
        try:
            async for kind, payload in iterate_in_threadpool(
                service.generate_stream(None, req.prompt_id, req.confirmation)
            ):
                yield _sse(kind, payload)
        except Exception as exc:  # noqa: BLE001 - SSE 用 error 事件回告，不能让连接无声断开
            logger.bind(event="media_stream_error", error=str(exc)).exception("media stream failed")
            yield _sse("error", {"message": f"{type(exc).__name__}: {exc}", "submitted": False})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/collect")
def collect(novel: str, job_id: str):
    """只取既有远端结果，绝不重复提交（不重复计费）。"""
    return service.collect(None, novel, job_id)


@router.get("/images", response_model=MediaImagesResponse)
def images(novel: str, prompt_id: str = ""):
    """某本小说已出的图；带 `prompt_id` 时只列该条目。"""
    return service.images_for(None, novel, prompt_id=prompt_id)


@router.post("/images/{image_id}/select")
def select(image_id: str):
    """选定当前版本；同条目其它版本自动取消选用（旧图不删）。"""
    record = select_image(None, image_id)
    logger.bind(event="media_image_selected", image_id=image_id).info("media image selected")
    return record


@router.get("/images/{image_id}/sidecar")
def sidecar(image_id: str):
    """单张图的完整技术档案（指纹 / seed / 参数 / 成本 / provider 追加尾巴）。"""
    return read_sidecar(None, image_id)


@router.get("/jobs", response_model=MediaJobStatusResponse)
def jobs(novel: str):
    """任务状态与今日额度。"""
    return service.job_status(None, novel)


@router.get("/file")
def file(novel: str, path: str = Query(description="相对小说目录的图片路径")):
    """读图片本体。路径必须落在小说目录内，只允许 png/jpg/webp。"""
    target = resolve_image_file(None, novel, path)
    return FileResponse(target, media_type=None, filename=target.name)


def _iter_events(events: Iterator[tuple[str, dict]]) -> Iterator[str]:
    for kind, payload in events:
        yield _sse(kind, payload)
