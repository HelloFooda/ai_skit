"""火山方舟 Seedream 图片生成：走 OpenAI 兼容端点，只借 SDK 的传输层。

## 为什么能复用 openai SDK

方舟的图片接口是 OpenAI 兼容的 `POST {base}/images/generations`，Bearer 鉴权，
返回 `data[].url` / `data[].b64_json`。所以连接池、超时、重试、错误类型全部交给
`openai` SDK（项目已通过 langchain-openai 传递依赖装了它），只把 Seedream 独有参数
（`watermark` / `optimize_prompt_options`）走 `extra_body`。

## 能力事实（provider 声明）

- 画幅：档位 `1K / 1.5K / 2K / 3K / 4K`，或显式 `宽x高`（总像素 1280×720 ~ 2048×2048×1.1025，
  宽高比 1/16–16），两种方式不可混用；
- 参考图：`image` 可传多张（图生图 / 一致性参考）；
- 同步返回，无任务号；产物是临时 URL，必须立刻下载落盘；
- **`watermark` 默认是开的**，本项目一律显式关掉。

## 画幅比例怎么落地

档位模式下模型自行决定宽高，官方建议在提示词里用自然语言描述比例。所以当
`request.ratio` 非空且 size 是档位时，这里追加一句最小技术尾巴 `画面比例 16:9。`，
并把追加内容如实写进 `MediaResult.appended_suffix`——业务层的提示词一个字没改，
回溯时能看清 provider 加了什么。

## 错误映射

`submitted` 的语义：本地预检（缺 key / 缺模型）为 False，不核销确认码；一旦请求发出
（无论成败）为 True。这正是技能包 `adapter-contract.md` 的口径。
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    RateLimitError,
)

from ..contract import (
    Capabilities,
    HandleSink,
    MediaAsset,
    MediaError,
    MediaRequest,
    MediaResult,
    mime_for,
    sanitize_detail,
    sha256_file,
)
from ..settings import settings

DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
SIZE_TIERS = ("1K", "1.5K", "2K", "3K", "4K")


def _is_tier(size: str) -> bool:
    return (size or "").strip() in SIZE_TIERS


def _compose_prompt(prompt: str, ratio: str, size: str) -> tuple[str, str]:
    """档位模式下补一句比例尾巴；显式像素模式下不加（模型已按像素出图）。"""
    if not ratio or not _is_tier(size):
        return prompt, ""
    suffix = f"画面比例 {ratio}。"
    if ratio in prompt:
        return prompt, ""
    return f"{prompt}{suffix}", suffix


def _map_error(exc: BaseException, *, submitted: bool) -> MediaError:
    """把 SDK 异常压成统一分类；detail 已脱敏。"""
    if isinstance(exc, BadRequestError):
        return MediaError(
            "请求参数被厂商拒绝",
            category="invalid_request",
            retryable=False,
            submitted=submitted,
            http_status=getattr(exc, "status_code", None),
            request_id=getattr(exc, "request_id", None),
            detail=sanitize_detail(exc),
        )
    if isinstance(exc, AuthenticationError):
        return MediaError(
            "厂商鉴权失败，请检查 API Key",
            category="auth",
            retryable=False,
            submitted=submitted,
            http_status=getattr(exc, "status_code", None),
            detail=sanitize_detail(exc),
        )
    if isinstance(exc, RateLimitError):
        return MediaError(
            "厂商限流或额度不足",
            category="rate_limit",
            retryable=True,
            submitted=submitted,
            http_status=getattr(exc, "status_code", None),
            request_id=getattr(exc, "request_id", None),
            detail=sanitize_detail(exc),
        )
    if isinstance(exc, APITimeoutError):
        return MediaError(
            "厂商响应超时",
            category="timeout",
            retryable=True,
            submitted=submitted,
            detail=sanitize_detail(exc),
        )
    if isinstance(exc, APIConnectionError):
        return MediaError(
            "无法连接厂商服务",
            category="network",
            retryable=True,
            submitted=submitted,
            detail=sanitize_detail(exc),
        )
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None)
        category = "content_policy" if status == 400 else "provider_error"
        return MediaError(
            "厂商返回错误状态",
            category=category,
            retryable=bool(status and status >= 500),
            submitted=submitted,
            http_status=status,
            request_id=getattr(exc, "request_id", None),
            detail=sanitize_detail(exc),
        )
    return MediaError(
        "厂商调用失败",
        category="provider_error",
        retryable=False,
        submitted=submitted,
        detail=sanitize_detail(exc),
    )


class ArkSeedreamProvider:
    """Seedream 实现。厂商名只出现在这个文件里。"""

    name = "ark"
    capabilities = Capabilities(
        modalities=frozenset({"image"}),
        sizes=SIZE_TIERS,
        custom_size=True,
        ratios=(),
        ref_roles=frozenset({"reference_image"}),
        max_refs=14,
        max_n=1,
        async_submit=False,
        supports_seed=True,
        billing_unit="image",
    )

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        # None = 未传，回退配置；显式传 "" = 故意置空，用于触发本地预检。
        # 不能用 `or`：那样显式空串会被吞掉回退到 settings，导致缺 key 预检失效。
        self.model = settings.image_model if model is None else model
        configured_url = settings.image_base_url or DEFAULT_BASE_URL
        self._base_url = configured_url if base_url is None else base_url
        self._api_key = settings.image_api_key if api_key is None else api_key

    # ---------------------------------------------------------------- 预检
    def _client(self) -> OpenAI:
        """本地预检失败必须报 `submitted=False`，否则会白白烧掉确认码。"""
        if not self._api_key:
            raise MediaError(
                "缺少 API Key（MEDIA_IMAGE_API_KEY 或 ARK_API_KEY）",
                category="auth",
                retryable=False,
                submitted=False,
                detail="MEDIA_IMAGE_API_KEY is required",
            )
        if not self.model:
            raise MediaError(
                "缺少模型名（MEDIA_IMAGE_MODEL）",
                category="invalid_request",
                retryable=False,
                submitted=False,
                detail="MEDIA_IMAGE_MODEL is required",
            )
        return OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=settings.timeout,
            max_retries=settings.max_retries,
        )

    # ---------------------------------------------------------------- 生成
    def generate(
        self, request: MediaRequest, *, out_dir: Path, handle: HandleSink
    ) -> MediaResult:
        client = self._client()
        prompt, suffix = _compose_prompt(
            request.prompt, request.ratio or settings.image_ratio, request.size
        )
        extra: dict[str, object] = {
            "watermark": bool(
                request.extra.get("watermark", settings.image_watermark)
            ),
        }
        # 刻意不发 sequential_image_generation：该参数仅部分 Seedream 版本支持，
        # Seedream 5.0 flash 会直接 400 拒绝。一张一交由上层 max_n=1 保证。
        if request.seed is not None:
            extra["seed"] = request.seed
        extra.update(
            {
                key: value
                for key, value in request.extra.items()
                if key not in {"watermark"}
            }
        )
        payload: dict[str, object] = {
            "model": self.model,
            "prompt": prompt,
            "size": request.size or settings.image_size,
            "response_format": "url",
            "extra_body": extra,
        }
        if request.refs:
            # 参考图必须是公网可访问 URL 或 base64；本地文件走 data URL。
            payload["image"] = [self._data_url(Path(ref.path)) for ref in request.refs]

        started = time.monotonic()
        try:
            response = client.images.generate(**payload)  # type: ignore[arg-type]
        except BaseException as exc:
            raise _map_error(exc, submitted=True) from exc

        assets = self._save(response, out_dir)
        return MediaResult(
            provider=self.name,
            model=self.model,
            assets=assets,
            usage={"images": len(assets)},
            cost_estimate=round(settings.cost_per_image * len(assets), 4) or None,
            latency_ms=int((time.monotonic() - started) * 1000),
            meta={"size": payload["size"], "watermark": extra["watermark"]},
            appended_suffix=suffix,
        )

    def collect(
        self, provider_job_id: str, request: MediaRequest, *, out_dir: Path
    ) -> MediaResult:
        raise MediaError(
            "Seedream 图片接口是同步返回的，没有可找回的远端任务",
            category="invalid_request",
            retryable=False,
            submitted=False,
        )

    # ---------------------------------------------------------------- 内部
    @staticmethod
    def _data_url(path: Path) -> str:
        mime = mime_for(path)
        encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _save(self, response: object, out_dir: Path) -> list[MediaAsset]:
        """把 `data[]` 里的 url / b64_json 立刻落盘。临时 URL 会过期，必须当场下载。"""
        data = getattr(response, "data", None) or []
        if not data:
            raise MediaError(
                "厂商返回了空结果",
                category="provider_error",
                retryable=True,
                submitted=True,
                detail="empty data array",
            )
        out_dir.mkdir(parents=True, exist_ok=True)
        assets: list[MediaAsset] = []
        for index, item in enumerate(data, 1):
            b64 = getattr(item, "b64_json", None)
            url = getattr(item, "url", None)
            if b64:
                content = base64.b64decode(b64)
                suffix = ".png"
            elif url:
                content = self._download(str(url))
                suffix = Path(str(url).split("?")[0]).suffix.lower() or ".png"
            else:
                raise MediaError(
                    "厂商返回的条目既无 url 也无 b64_json",
                    category="provider_error",
                    retryable=False,
                    submitted=True,
                )
            target = out_dir / f"generated-{index}{suffix}"
            target.write_bytes(content)
            assets.append(
                MediaAsset(
                    path=target,
                    mime=mime_for(target),
                    bytes=len(content),
                    sha256=sha256_file(target),
                )
            )
        return assets

    @staticmethod
    def _download(url: str) -> bytes:
        try:
            response = httpx.get(url, timeout=settings.timeout, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MediaError(
                "产物下载失败（远端任务已提交，勿重复提交）",
                category="network",
                retryable=True,
                submitted=True,
                detail=sanitize_detail(exc),
            ) from exc
        return response.content
