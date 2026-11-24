import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger
from starlette.concurrency import iterate_in_threadpool

from ..core.config import settings
from ..core.schemas import (
    ArchiveReport,
    ExtractAssetsRequest,
    ExtractAssetsResponse,
    NovelPipelineRequest,
    NovelPipelineResponse,
    ProgressEvent,
    SkillCatalogResponse,
    SkillInfo,
    chapter_output,
)
from ..generation.archive import archive_novel
from ..generation.graph import PROGRESS, run_novel_pipeline, stream_novel_pipeline
from ..generation.llm import extract_assets
from ..knowledge.skills import catalog, load_all
from ..observability.logging import get_request_id, new_request_id

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


def _run_id() -> str:
    """中间件入口已 set run_id；直接调路由函数的单测没有中间件，这里兜底生成。"""
    run_id = get_request_id()
    return new_request_id() if run_id == "-" else run_id


@router.get("/skills", response_model=SkillCatalogResponse)
def list_skills(categories: str | None = None):
    """技能菜单：只有元数据，不含正文。

    前端用它渲染多选；等阶段 03 接上 Function Calling，同一份菜单可以直接给模型
    做「自己决定加载哪个技能」的选项。categories 支持逗号分隔过滤，如 characters,scenes。
    """
    wanted = (
        {item.strip() for item in categories.split(",") if item.strip()} if categories else None
    )
    _, errors = load_all()
    return SkillCatalogResponse(
        skills=[SkillInfo(**item) for item in catalog(wanted)],
        errors=list(errors),
    )


@router.post("/generate", response_model=ExtractAssetsResponse)
@router.post("/extract-assets", response_model=ExtractAssetsResponse, deprecated=True)
def run(req: ExtractAssetsRequest):
    """章节 → 五类文生文产出：角色 / 角色道具 / 场景 / 站位 / 15s 分镜组表。

    `/generate` 是语义正确的主路径；`/extract-assets` 保留为兼容别名（老前端不用改）。
    一次请求内部按依赖顺序跑两段：盘资产 → 带着已定稿资产写站位与分镜。

    异常映射（400 / 500）由 `api/errors.py` 统一注册，这里只管业务。
    """
    run_id = _run_id()
    result = extract_assets(
        req.chapter,
        character_hint=req.character_hint,
        prop_hint=req.prop_hint,
        scene_hint=req.scene_hint,
        mode=req.mode,
        skills=req.skills,
        max_groups=req.max_groups,
        target_duration=req.target_duration,
        assets=req.assets,
        scene_filter=req.scene_filter,
    )

    pkg = result.package
    # INFO 摘要：一次请求的产出计数、token、耗时；不含章节原文与补充要求正文。
    logger.bind(
        event="generation_done",
        status=200,
        mode=result.mode,
        mock=result.mock,
        llm_calls=result.llm_calls,
        latency_ms=result.latency_ms,
        characters=len(pkg.characters),
        props=len(pkg.props),
        scenes=len(pkg.scenes),
        storyboards=len(pkg.storyboards),
        injection_warning=bool(result.injection_warning),
        gate_issues=len(result.gate_issues),
        skills_loaded=len(result.skills_loaded),
    ).info("generation done")
    return ExtractAssetsResponse(
        run_id=run_id,
        model=settings.model,
        mock=result.mock,
        characters=pkg.characters,
        props=pkg.props,
        scenes=pkg.scenes,
        blockings=pkg.blockings,
        storyboards=pkg.storyboards,
        latency_ms=result.latency_ms,
        prompt_version=result.prompt_version,
        mode=result.mode,
        llm_calls=result.llm_calls,
        hints_used=result.hints_used,
        injection_warning=result.injection_warning,
        skills_loaded=result.skills_loaded,
        skills_skipped=result.skills_skipped,
        skills_refs_expanded=result.skills_refs_expanded,
        skills_refs_skipped=result.skills_refs_skipped,
        stages=result.stages,
        gate_issues=result.gate_issues,
        total_groups_estimated=result.total_groups_estimated,
        truncated=result.truncated,
    )


def _novel_params(req: NovelPipelineRequest) -> dict:
    """多章请求 → 图表参数；老端点与流式端点共用，避免两处漂移。"""
    return {
        "character_hint": req.character_hint,
        "prop_hint": req.prop_hint,
        "scene_hint": req.scene_hint,
        "mode": req.mode,
        "target_duration": req.target_duration,
        "max_groups": req.max_groups,
        "skills": list(req.skills),
        "scene_filter": list(req.scene_filter),
    }


def _archive_final(final: dict, run_id: str) -> ArchiveReport:
    """归档副作用：任何异常都只回告，不回滚生成、不抛 500。"""
    try:
        return archive_novel(final)
    except Exception as exc:  # noqa: BLE001 - 归档是副作用，任何异常都不该拖垮已成功的生成
        return ArchiveReport(
            root="", novel_dir="", errors=[f"归档异常：{type(exc).__name__}: {exc}"]
        )


def _build_novel_response(final: dict, report: ArchiveReport, run_id: str) -> NovelPipelineResponse:
    """final + 归档报告 → 响应契约；两条端点共用同一份组装逻辑。"""
    if report.errors:
        logger.bind(event="archive_degraded", run_id=run_id, errors=report.errors).warning(
            "asset archive partially failed"
        )
    logger.bind(
        event="novel_generation_done",
        status=200,
        novel_title_chars=len(final["novel_title"]),
        chapters=final["chapter_count"],
        llm_calls=final["total_llm_calls"],
        agent_calls=final["total_agent_calls"],
        latency_ms=final["total_latency_ms"],
        gate_issues=final["gate_issue_count"],
        archive_files=report.files_written,
    ).info("novel response ready")
    return NovelPipelineResponse(
        run_id=run_id,
        model=settings.model,
        mock=settings.mock or not settings.api_key,
        novel_title=final["novel_title"],
        chapter_count=final["chapter_count"],
        chapters=[chapter_output(item) for item in final["chapters"]],
        total_llm_calls=final["total_llm_calls"],
        total_agent_calls=final["total_agent_calls"],
        total_latency_ms=final["total_latency_ms"],
        total_characters=final["total_characters"],
        total_props=final["total_props"],
        total_scenes=final["total_scenes"],
        total_storyboards=final["total_storyboards"],
        injection_warning=final["injection_warning"],
        gate_issue_count=final["gate_issue_count"],
        archive=report,
    )


def _sse(event: str, payload) -> str:
    """一条 SSE 帧：event 名 + JSON data（ensure_ascii 便于中文直读）。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


@router.post("/generate-novel", response_model=NovelPipelineResponse)
def run_novel(req: NovelPipelineRequest):
    """小说名 + 多章原文 → LangGraph 逐章编排 → 按章五类产出。

    章节边界由代码确定性识别并逐字回拼，模型不参与拆分；顺序由 StateGraph 的边决定，
    不在各节点里写「自约束」前置检查。旧的单章 `/generate` 保持不动。
    """
    run_id = _run_id()
    final = run_novel_pipeline(
        req.novel_title,
        req.content,
        _novel_params(req),
        run_id=run_id,
    )
    report = _archive_final(final, run_id)
    return _build_novel_response(final, report, run_id)


@router.post("/generate-novel/stream")
async def run_novel_stream(req: NovelPipelineRequest):
    """与 `/generate-novel` 结果完全相同，但用 SSE 逐阶段回报进度。

    事件顺序：若干 `progress`（阶段文案 + 步数）→ 一条 `done`（完整响应，与老端点同构）。
    异常走 `error` 事件而非断连。生成节点是同步的，用 starlette 线程池包装迭代器，
    避免阻塞事件循环；LangGraph 的进度事件依赖 contextvar，实测跨线程不丢失。
    """

    async def events():
        run_id = _run_id()
        try:
            final = None
            # 同步图在线程池里跑；iterate_in_threadpool 逐步产出 (kind, payload)。
            async for kind, payload in iterate_in_threadpool(
                stream_novel_pipeline(
                    req.novel_title,
                    req.content,
                    _novel_params(req),
                    run_id=run_id,
                )
            ):
                if kind == "progress":
                    yield _sse("progress", ProgressEvent(**payload).model_dump())
                else:
                    final = payload

            if final is None:
                raise RuntimeError("生成图未返回最终结果")

            # 归档阶段在路由层（不在图节点内），单独报一条进度。
            yield _sse(
                "progress",
                ProgressEvent(phase="archive", text=PROGRESS["archive_start"], step=0, total=0).model_dump(),
            )
            report = _archive_final(final, run_id)
            response = _build_novel_response(final, report, run_id)
            yield _sse("done", response.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001 - SSE 里用 error 事件回告，不能让连接无声断开
            logger.bind(event="novel_stream_error", run_id=run_id, error=str(exc)).exception(
                "novel stream failed"
            )
            yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
