"""厂商装配：按 `MEDIA_IMAGE_PROVIDER` 选实现，业务层永远只见端口。

## 两道闸门

1. `allow_paid=False` 时，除 `fixture` 之外一律拒绝装配，错误在本地抛出且
   `submitted=False`——不花一分钱、不核销确认码。
2. 真实厂商要求 key 与模型名齐全，缺了同样在本地拒绝。

这样"要不要花钱"是一个显式配置动作，而不是某段代码碰巧走到了网络。
"""

from __future__ import annotations

from functools import lru_cache

from ..contract import MediaError, MediaProvider
from ..settings import settings
from .fixture import FixtureProvider

#: 允许在 `allow_paid=False` 下运行的离线实现。
OFFLINE_PROVIDERS = frozenset({"fixture"})


def _load_ark() -> MediaProvider:
    # 延迟导入：不装 openai 或不用 ark 时，模块级 import 不该有副作用。
    from .ark_seedream import ArkSeedreamProvider

    return ArkSeedreamProvider()


#: 注册表：新增厂商只加一行。键是 `MEDIA_IMAGE_PROVIDER` 的取值。
PROVIDERS = {
    "fixture": FixtureProvider,
    "ark": _load_ark,
}


@lru_cache(maxsize=4)
def _build(name: str, allow_paid: bool) -> MediaProvider:
    factory = PROVIDERS.get(name)
    if factory is None:
        raise MediaError(
            f"未知的图片 provider「{name}」，可选：{'、'.join(sorted(PROVIDERS))}",
            category="invalid_request",
            retryable=False,
            submitted=False,
        )
    if not allow_paid and name not in OFFLINE_PROVIDERS:
        raise MediaError(
            f"MEDIA_ALLOW_PAID 未开启，拒绝调用付费厂商「{name}」",
            category="invalid_request",
            retryable=False,
            submitted=False,
            detail="MEDIA_ALLOW_PAID is false",
        )
    return factory()


def build_image_provider() -> MediaProvider:
    """按配置装配当前图片 provider（带缓存；配置变了重启进程生效）。"""
    return _build(settings.image_provider, settings.allow_paid)


def clear_provider_cache() -> None:
    _build.cache_clear()
