import uuid

from fastapi import APIRouter, HTTPException

from ..config import settings
from ..llm import extract_assets
from ..schemas import (
    ExtractAssetsRequest,
    ExtractAssetsResponse,
    SkillCatalogResponse,
    SkillInfo,
)
from ..skills import UnknownSkillError, catalog, load_all

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


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
    """
    run_id = uuid.uuid4().hex[:12]
    try:
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
    except UnknownSkillError as e:
        # 技能名写错属于请求问题，给 400，不要伪装成服务端故障
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        # 参数语义问题（如 visual_only 缺 assets）同样是请求错误
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001 - 路由层统一兜底：LLM/解析/校验异常都转成 500，不向前端透堆栈
        raise HTTPException(status_code=500, detail=f"LLM 调用或解析失败：{e}")

    pkg = result.package
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
