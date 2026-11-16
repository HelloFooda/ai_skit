import uuid

from fastapi import APIRouter, HTTPException

from ..config import settings
from ..llm import extract_assets
from ..schemas import ExtractAssetsRequest, ExtractAssetsResponse

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.post("/extract-assets", response_model=ExtractAssetsResponse)
def run(req: ExtractAssetsRequest):
    """阶段 02 的唯一接口：输入章节 → 抽资产。

    后续阶段会在这里长出：工具调用、状态机、停点、机检门禁、Vidu 出片。
    """
    run_id = uuid.uuid4().hex[:12]
    try:
        assets, is_mock, latency_ms = extract_assets(req.chapter)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM 调用或解析失败：{e}")
    return ExtractAssetsResponse(
        run_id=run_id,
        model=settings.model,
        mock=is_mock,
        assets=assets,
        latency_ms=latency_ms,
    )
