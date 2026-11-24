"""媒体通道配置：独立的 `MEDIA_` 前缀，与文生文的 `LLM_` 前缀互不污染。

`core/config.py` 的 `Settings` 只有一个 `env_prefix="LLM_"`，把媒体参数塞进去会得到
`LLM_IMAGE_MODEL` 这种读起来是"语言模型图片模型"的键名。所以这里另立一个 Settings 类，
读同一份 `backend/.env`（全项目唯一配置文件），前缀 `MEDIA_`。

## 厂商无关

这个文件**不认识任何具体厂商**：provider 名字是字符串、模型名是字符串、地址是字符串，
默认值一律留空或给离线实现。厂商事实（默认地址、参数映射、能力声明）全部在
`app/media/providers/` 里。

唯一的例外是 `image_api_key` 的别名：它额外认一个业界常见的环境变量名，方便已有部署
不用把 key 填两遍。这属于配置兼容，不是业务耦合——换厂商时删掉那一行别名即可。

## 安全默认

`allow_paid` 默认 **False**：未显式打开时，`providers.registry` 会拒绝装配任何真实厂商，
只允许 `fixture`（离线假图）。这样开发与单测阶段一分钱都花不出去，也是"确认后才扣费"
之外的第二道物理闸门。
"""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..core.config import ENV_FILE


class MediaSettings(BaseSettings):
    """媒体生成通道配置。改完 `.env` 重启进程生效。"""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE), env_prefix="MEDIA_", extra="ignore"
    )

    # ---- 选哪家、哪个模型
    image_provider: str = Field(
        default="fixture", description="provider 名；可选值见 app/media/providers/registry.py"
    )
    image_model: str = Field(default="", description="模型名或接入点 ID；由 provider 解释")
    image_base_url: str = Field(
        default="", description="OpenAI 兼容端点；留空则由 provider 用自己的默认地址"
    )
    image_api_key: str = Field(
        default="",
        # 唯一的厂商相关配置：兼容已有部署把图片 key 放在通用 ARK_API_KEY 里的习惯。
        validation_alias=AliasChoices("MEDIA_IMAGE_API_KEY", "ARK_API_KEY"),
        description="优先 MEDIA_IMAGE_API_KEY，回退 ARK_API_KEY",
    )

    # ---- 画幅与产物
    image_size: str = Field(
        default="2K", description="画幅档位或 宽x高；空串表示交给 provider 默认"
    )
    image_ratio: str = Field(default="16:9", description="画幅比例，仅作为技术参数传给 provider")
    image_watermark: bool = Field(
        default=False, description="必须显式关掉；多数厂商的默认值是带水印"
    )

    # ---- 调用参数
    timeout: float = Field(default=180.0, gt=0, description="单次请求超时秒数")
    max_retries: int = Field(default=1, ge=0, le=5, description="SDK 内置重试次数，不叠加")
    max_concurrency: int = Field(default=2, ge=1, le=8, description="批量提交并发上限")
    poll_interval: float = Field(default=5.0, gt=0, description="异步任务轮询间隔秒数")
    poll_timeout: float = Field(default=1800.0, gt=0, description="异步任务最长等待秒数")

    # ---- 额度与闸门
    daily_quota: int = Field(default=0, ge=0, description="每日最多提交次数；0 表示不限")
    allow_paid: bool = Field(
        default=False, description="是否允许调用真实付费厂商；False 时只允许离线 fixture"
    )

    # ---- 成本估算（仅用于预览展示，不参与计费）
    cost_per_image: float = Field(default=0.0, ge=0, description="单张图预估成本，0 表示未配置")


settings = MediaSettings()


def reset_settings() -> None:
    """测试用：重新从环境变量读一份（改 env 后调用）。"""
    global settings
    settings = MediaSettings()
