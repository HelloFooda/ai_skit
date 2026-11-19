from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目唯一的配置文件。锚定 backend/ 而不是工作目录，
# 这样从仓库根目录或 backend/ 启动读到的都是同一份 .env。
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """从 backend/.env 读配置，统一 LLM_ 前缀。

    文生文通道由 LangChain 承载，配置分三组：
    - 模型与端点：provider / base_url / api_key / model，换厂商只改这几项，
      业务代码不写死厂商（见 chains.build_chat_model）。
    - 调用参数：temperature / timeout / max_retries 在模型工厂里设；
      max_concurrency 在 RunnableConfig 里设（对应 per_type 的 batch 并发上限）。
    - 技能库：skills_*，与 LLM 通道正交。
    """

    model_config = SettingsConfigDict(env_file=str(ENV_FILE), env_prefix="LLM_", extra="ignore")

    # ---- 模型与端点
    # 统一由 init_chat_model 按 provider 名装配。
    # openai = OpenAI 兼容端点（含 DeepSeek / 通义 / vLLM 等），额外用 base_url；
    # 其它值（anthropic / google_genai / ...）忽略 base_url，需另装集成包。
    provider: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    mock: bool = False

    # ---- 单次调用参数（模型级）与并发上限（运行级）
    temperature: float = Field(default=0.2, ge=0, le=2)
    timeout: float = Field(default=120.0, gt=0, description="单次请求超时秒数")
    max_retries: int = Field(default=2, ge=0, le=10, description="SDK 内置重试次数")
    max_concurrency: int = Field(default=3, ge=1, le=16, description="Runnable.batch 并发上限")

    # ---- 技能库根目录；留空则用 backend/skills
    skills_dir: str = ""
    # 项目侧「技能 → 类别」绑定表；留空则用 backend/skill-bindings.yaml。
    # 用处：通用技能不写私有字段 metadata.applies-to 时，靠这张表参与自动装载。
    skills_bindings: str = ""
    # 一次注入的技能总字数预算（约等于 0.6 token/字符的成本，按需调）
    skills_budget_chars: int = 60_000


settings = Settings()
