"""资产库读写接口：列小说 / 列提示词 / 读单条 / 保存编辑 / 定稿 / 重渲染。

## 为什么单独一个 router

骨架 `/api/pipeline` 只管"生成"，这里管"生成之后的人工维护"：读的是归档目录 + 清单，
写的是提示词 md。两者数据源不同（一个在内存链路，一个在磁盘），分开挂便于独立测试与
独立演进。异常 → 状态码沿用 `api/errors.py` 的全局映射：ValueError 子类统一 400。

## 路径安全

小说名与提示词 id 都不直接拼进文件系统：`asset_store` 层做 resolve() 越界校验，
id 必须命中清单条目，禁止读 `<asset_root>` 外或清单未登记的文件。
"""

from __future__ import annotations

from fastapi import APIRouter
from loguru import logger

from ..core.schemas import (
    AssetDocument,
    ChapterEntry,
    NovelAssetSummary,
    PromptDetail,
    PromptEntry,
    PromptSaveRequest,
)
from ..generation.asset_store import (
    approve_prompt,
    list_chapters,
    list_novels,
    list_prompts,
    read_document,
    read_prompt,
    regenerate_prompt,
    save_prompt,
)

router = APIRouter(prefix="/api/assets", tags=["assets"])


@router.get("/novels", response_model=list[NovelAssetSummary])
def get_novels():
    """列出已归档的小说：目录名 + 提示词状态计数，供前端下拉选择。"""
    return list_novels()


@router.get("/novels/{novel}/prompts", response_model=list[PromptEntry])
def get_prompts(novel: str):
    """某本小说的全部提示词条目（正文不在这里，读单条才返回）。"""
    return list_prompts(None, novel)


@router.get("/novels/{novel}/chapters", response_model=list[ChapterEntry])
def get_chapters(novel: str):
    """某本小说的章节目录与文档清单（只列文件名，正文走 /document）。"""
    return list_chapters(None, novel)


@router.get("/novels/{novel}/document", response_model=AssetDocument)
def get_document(novel: str, path: str):
    """读小说目录下任意一份归档 Markdown（章节站位/分镜只读）。`path` 相对小说目录。"""
    return read_document(None, novel, path)


@router.get("/prompts/{prompt_id}", response_model=PromptDetail)
def get_prompt(prompt_id: str):
    """单条提示词全文；`generated_body` 非空表示有模板对照版本。"""
    return read_prompt(None, prompt_id)


@router.put("/prompts/{prompt_id}", response_model=PromptDetail)
def update_prompt(prompt_id: str, req: PromptSaveRequest):
    """保存人工编辑：校验 IMG-... 结构、原子写回、状态置 edited。"""
    detail = save_prompt(None, prompt_id, req.body)
    logger.bind(event="asset_prompt_saved", prompt_id=prompt_id, chars=len(detail.body)).info(
        "asset prompt saved"
    )
    return detail


@router.post("/prompts/{prompt_id}/approve", response_model=PromptDetail)
def approve(prompt_id: str):
    """定稿：edited → approved；之后归档重跑不会再碰这条。"""
    detail = approve_prompt(None, prompt_id)
    logger.bind(event="asset_prompt_approved", prompt_id=prompt_id).info("asset prompt approved")
    return detail


@router.post("/prompts/{prompt_id}/regenerate", response_model=PromptDetail)
def regenerate(prompt_id: str):
    """人工接受模板版本：用 `.generated.md` 覆盖主文件并回到 draft。"""
    detail = regenerate_prompt(None, prompt_id)
    logger.bind(event="asset_prompt_regenerated", prompt_id=prompt_id).info(
        "asset prompt regenerated"
    )
    return detail
