"""离线 fixture 厂商：不联网、不花钱，产出确定性 PNG。

用途有两个：

1. **单测**：把 `preview → confirm → generate → 落盘 → 清单回写` 全链路跑通，且断言的是
   真实字节（真 PNG 文件、真 sha256），不是 mock 出来的假路径；
2. **联调**：`MEDIA_IMAGE_PROVIDER=fixture` 时前端能走完整交互，不消耗额度。

产物是按 seed 与提示词哈希决定的纯色 PNG，宽高随 `size` 变化——这样"换 size 出不同图"
也是可验证的。PNG 用标准库 zlib + struct 手写，不引入 Pillow。
"""

from __future__ import annotations

import hashlib
import struct
import time
import zlib
from pathlib import Path

from ..contract import (
    Capabilities,
    HandleSink,
    MediaAsset,
    MediaRequest,
    MediaResult,
    mime_for,
    sha256_file,
)

_SIZE_TIERS: dict[str, tuple[int, int]] = {
    "1K": (1024, 1024),
    "1.5K": (1536, 1536),
    "2K": (2048, 1152),
    "3K": (3072, 1728),
    "4K": (4096, 2304),
}


def _parse_size(size: str) -> tuple[int, int]:
    text = (size or "").strip()
    if text in _SIZE_TIERS:
        return _SIZE_TIERS[text]
    parts = text.lower().split("x")
    if len(parts) == 2 and all(part.isdigit() and int(part) > 0 for part in parts):
        return int(parts[0]), int(parts[1])
    return _SIZE_TIERS["2K"]


def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """最小合法 PNG：单色，无依赖。"""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = bytes(rgb) * width
    raw = b"".join(b"\x00" + row for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


class FixtureProvider:
    """确定性假厂商：同名同参出同一张图，便于断言幂等与版本。"""

    name = "fixture"
    model = "fixture-image-v1"
    capabilities = Capabilities(
        modalities=frozenset({"image"}),
        sizes=tuple(_SIZE_TIERS),
        custom_size=True,
        ratios=(),
        ref_roles=frozenset({"reference_image"}),
        max_refs=4,
        max_n=4,
        async_submit=False,
        supports_seed=True,
        billing_unit="image",
    )

    def generate(
        self, request: MediaRequest, *, out_dir: Path, handle: HandleSink
    ) -> MediaResult:
        started = time.monotonic()
        width, height = _parse_size(request.size)
        digest = hashlib.sha256(
            f"{request.prompt}|{request.size}|{request.ratio}|{request.seed}".encode()
        ).digest()
        # 参考图也参与配色：挂了参考图但颜色不变，说明参考没接上。
        if request.refs:
            digest = hashlib.sha256(
                digest + b"".join(sha256_file(Path(ref.path)).encode() for ref in request.refs)
            ).digest()
        rgb = (digest[0], digest[1], digest[2])
        target = out_dir / f"fixture-{digest.hex()[:12]}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_png(width, height, rgb))
        return MediaResult(
            provider=self.name,
            model=self.model,
            assets=[
                MediaAsset(
                    path=target,
                    mime=mime_for(target),
                    bytes=target.stat().st_size,
                    sha256=sha256_file(target),
                    width=width,
                    height=height,
                )
            ],
            usage={"images": 1},
            cost_estimate=0.0,
            latency_ms=int((time.monotonic() - started) * 1000),
            meta={"fixture": True, "seed": request.seed},
        )

    def collect(
        self, provider_job_id: str, request: MediaRequest, *, out_dir: Path
    ) -> MediaResult:
        # fixture 是同步的，没有可找回的远端任务。
        return self.generate(request, out_dir=out_dir, handle=lambda _id: None)
