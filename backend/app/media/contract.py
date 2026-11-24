"""媒体生成端口：模型商无关的请求 / 能力 / 结果 / 错误契约。

## 为什么不套 LangChain 的模型抽象

LangChain 1.x 只有 chat 与 embeddings 两类模型抽象，**没有通用的图像 / 视频生成接口**。
硬造一个假的 LangChain ImageModel 只会把厂商参数塞进一个不存在的抽象里：既换不了厂商，
也复用不了它任何能力。所以这一层只借三样标准件——

- `openai` SDK：HTTP 连接池、超时、重试（多数图片生成厂商提供 OpenAI 兼容端点）；
- `pydantic`：请求 / 结果 / 能力声明的强校验，与项目既有 `core/schemas.py` 同口径；
- `httpx`：产物下载（供应商返回的是临时 URL）。

端口本身用 `Protocol` 定义，面尽量小：`generate` + `collect` 两个方法。

## 三条不可动摇的口径

1. **业务层不认识厂商**：具体厂商名只允许出现在 `app/media/providers/` 里。
   换厂商 = 改 `.env` 的 `MEDIA_IMAGE_PROVIDER`。
2. **能力由 provider 声明，约束由业务层兜底**：能出多大的图、要不要异步、能挂几个参考
   素材是厂商事实，写进 `Capabilities`；4–15 秒单条上限、16:9 画幅这类创作约束是项目
   规则（技能包 `VID-13`），不交给 provider 判断。
3. **提示词逐字**：`MediaRequest.prompt` 是 `IMG-...` 条目的 `### 可复制提示词` 原文，
   业务层一个字都不改。画幅比例只作为技术参数传 `ratio`，由 provider 决定怎么落地
   （具体 provider 决定是追加一句技术尾巴还是映射成 `size`），并把追加内容如实记进
   `MediaResult.appended_suffix` 供回溯。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Modality = Literal["image", "video", "tts", "music"]
RefRole = Literal["reference_image", "first_frame", "last_frame", "reference_audio"]
ErrorCategory = Literal[
    "invalid_request",
    "auth",
    "rate_limit",
    "quota",
    "timeout",
    "content_policy",
    "network",
    "provider_error",
]

#: 异步接口拿到远端任务号后，**在开始轮询之前**调用它落盘 handle（对齐技能包
#: `adapter-contract.md` 的硬要求）。同步接口不调用。
HandleSink = Callable[[str], None]

# 错误 detail 的白名单口径：单行、≤400 字、剔除密钥 / Bearer / URL / 本机绝对路径。
# 400 字是为了留住厂商报文末尾的处理指引（如方舟 429 提示去开通管理页关闭安全体验模式）。
MAX_DETAIL_CHARS = 400
_SECRET_RE = re.compile(r"(sk-|ark-|Bearer\s+)[A-Za-z0-9._\-]{4,}", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)
_ABS_PATH_RE = re.compile(r"(?<![\w.])/(?:[^\s/]+/){2,}[^\s]*")


def sanitize_detail(value: object) -> str:
    """把厂商报文压成一行、去掉密钥与路径，供 `MediaError.detail` 与日志使用。"""
    text = " ".join(str(value or "").split())
    text = _SECRET_RE.sub("<redacted>", text)
    text = _URL_RE.sub("<url>", text)
    text = _ABS_PATH_RE.sub("<path>", text)
    return text[:MAX_DETAIL_CHARS]


class MediaRef(BaseModel):
    """一条参考素材绑定；`role` 决定 provider 把它放在哪个槽位。"""

    role: RefRole
    path: Path
    label: str = ""
    may_control: list[str] = Field(default_factory=list)
    must_not_control: list[str] = Field(default_factory=list)


class MediaRequest(BaseModel):
    """一次生成请求。`prompt` 逐字来自 `IMG-...` 条目，业务层不改写。"""

    modality: Modality = "image"
    prompt: str
    refs: list[MediaRef] = Field(default_factory=list)
    size: str = Field(default="", description="画幅档位或 宽x高；空表示用 provider 默认")
    ratio: str = Field(default="", description="画幅比例，如 16:9；由 provider 决定怎么落地")
    seed: int | None = None
    n: int = Field(default=1, ge=1)
    duration: float | None = Field(default=None, description="视频/音频时长（秒）")
    extra: dict[str, Any] = Field(
        default_factory=dict, description="provider 专属透传参数；业务层不解读"
    )


class Capabilities(BaseModel):
    """provider 自报能力。业务层据此做提交前校验，校验不过就不发请求、不扣费。"""

    modalities: frozenset[Modality] = frozenset({"image"})
    sizes: tuple[str, ...] = ()
    custom_size: bool = Field(default=False, description="是否接受 宽x高 形式的显式像素")
    ratios: tuple[str, ...] = ()
    ref_roles: frozenset[RefRole] = frozenset()
    max_refs: int = 0
    max_n: int = 1
    duration_range: tuple[float, float] | None = None
    async_submit: bool = Field(default=False, description="是否先返回任务号再轮询")
    supports_seed: bool = False
    billing_unit: Literal["image", "second", "call"] = "image"

    def problems(self, request: MediaRequest) -> list[str]:
        """返回阻塞原因；空列表表示可以提交。文案直接给前端看。"""
        issues: list[str] = []
        if request.modality not in self.modalities:
            issues.append(f"该模型不支持 {request.modality} 生成")
        if request.n < 1 or request.n > self.max_n:
            issues.append(f"单次张数需在 1–{self.max_n} 之间")
        if request.size and not self._size_ok(request.size):
            allowed = "、".join(self.sizes) or "不限档位"
            issues.append(f"尺寸「{request.size}」不在允许范围（{allowed}）")
        if request.ratio and self.ratios and request.ratio not in self.ratios:
            issues.append(f"画幅「{request.ratio}」不在允许范围（{'、'.join(self.ratios)}）")
        if len(request.refs) > self.max_refs:
            issues.append(f"参考素材最多 {self.max_refs} 个")
        for ref in request.refs:
            if ref.role not in self.ref_roles:
                issues.append(f"该模型不支持参考角色 {ref.role}")
            elif not Path(ref.path).is_file():
                issues.append(f"参考素材不存在：{ref.path.name}")
        if request.seed is not None and not self.supports_seed:
            issues.append("该模型不支持 seed")
        if request.duration is not None and self.duration_range is not None:
            low, high = self.duration_range
            if not low <= request.duration <= high:
                issues.append(f"时长需在 {low:g}–{high:g} 秒之间")
        return issues

    def _size_ok(self, size: str) -> bool:
        if size in self.sizes:
            return True
        if not self.custom_size:
            return False
        parts = size.lower().split("x")
        return (
            len(parts) == 2
            and all(part.isdigit() and int(part) > 0 for part in parts)
        )


class MediaAsset(BaseModel):
    """provider 落到 `out_dir` 下的一个产物文件。"""

    path: Path
    mime: str
    bytes: int = 0
    sha256: str = ""
    width: int | None = None
    height: int | None = None


class MediaResult(BaseModel):
    """一次生成的结果摘要。`meta` 只放脱敏后的技术信息，禁止放密钥 / 请求头。"""

    provider: str
    model: str
    assets: list[MediaAsset] = Field(default_factory=list)
    provider_job_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    cost_estimate: float | None = None
    latency_ms: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)
    appended_suffix: str = Field(
        default="", description="provider 为落地技术参数追加在提示词后的尾巴（如实记录）"
    )


class MediaError(RuntimeError):
    """统一的厂商错误。

    `submitted` 是确认码是否核销的唯一判据：**本地预检阶段报错（没发请求）为 False**，
    修好配置可用同一确认码重跑；一旦远端请求已发出即为 True，重试需要重新确认。
    """

    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory = "provider_error",
        retryable: bool = False,
        submitted: bool = True,
        http_status: int | None = None,
        request_id: str | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.category: ErrorCategory = category
        self.retryable = retryable
        self.submitted = submitted
        self.http_status = http_status
        self.request_id = request_id
        self.detail = sanitize_detail(detail)

    def as_public(self) -> dict[str, Any]:
        return {
            "message": sanitize_detail(str(self)),
            "category": self.category,
            "retryable": self.retryable,
            "submitted": self.submitted,
            "http_status": self.http_status,
            "request_id": self.request_id,
            "detail": self.detail,
        }


class MediaProvider(Protocol):
    """媒体生成端口。实现只允许出现在 `app/media/providers/` 下。"""

    name: str
    model: str
    capabilities: Capabilities

    def generate(
        self, request: MediaRequest, *, out_dir: Path, handle: HandleSink
    ) -> MediaResult:
        """提交一次生成，把产物写进 `out_dir`。异步实现必须在轮询前调用 `handle`。"""
        ...

    def collect(
        self, provider_job_id: str, request: MediaRequest, *, out_dir: Path
    ) -> MediaResult:
        """只查询下载既有远端任务，**严禁重新提交**（不重复计费）。"""
        ...


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


MIME_BY_SUFFIX: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}


def mime_for(path: Path) -> str:
    return MIME_BY_SUFFIX.get(Path(path).suffix.lower(), "application/octet-stream")
