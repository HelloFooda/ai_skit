from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从 .env 读配置，统一 LLM_ 前缀。"""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="LLM_", extra="ignore")

    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    mock: bool = False
    # 技能库根目录；留空则用 backend/skills
    skills_dir: str = ""
    # 项目侧「技能 → 类别」绑定表；留空则用 backend/skill-bindings.yaml。
    # 用处：通用技能不写私有字段 metadata.applies-to 时，靠这张表参与自动装载。
    skills_bindings: str = ""
    # 一次注入的技能总字数预算（约等于 0.6 token/字符的成本，按需调）
    skills_budget_chars: int = 60_000


settings = Settings()
