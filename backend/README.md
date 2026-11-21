# ai_skit backend

FastAPI 后端：小说章节 → 五类文生文描述（角色 / 道具 / 场景 / 站位 / 分镜），走 LangChain 1.x，由 uv 管理 Python 3.12 隔离环境。

- 跑起来、配置、接口、分层调用链、机检规则、技能装载、日志分析、目录结构：见[后端技术文档](../docs/backend.md)。
- 产品效果与快速开始：见[根 README](../README.md)。
- 项目约定与红线：见[AGENTS.md](../AGENTS.md)。

最小启动：

```bash
uv sync --frozen
uv run uvicorn app.main:app --reload --port 8000
```
