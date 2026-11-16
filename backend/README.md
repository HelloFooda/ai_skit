# ai_skit backend

最小前后端闭环的后端。本阶段只做一件事：输入小说章节 → 调 LLM → 结构化输出角色 / 道具 / 场景。
这是 agent 最底下那一层：一次 LLM 调用 + Pydantic 强校验。先不上 Function Calling、不上 LangGraph。

## 跑起来

```bash
cd backend
uv sync
cp .env.example .env     # 填 LLM_API_KEY；留空会自动走 mock，也能跑通联调
uv run uvicorn app.main:app --reload --port 8000
```

- 未填 key：自动 mock，返回假数据，用于跑通前后端联调与演示。
- 填了 key：真实调用 LLM（OpenAI 兼容端点，可换 base_url 接其它模型）。

## 接口

- `GET /api/health` → `{"status":"ok"}`
- `POST /api/pipeline/extract-assets`，body `{"chapter":"..."}`，返回结构化资产 + run_id + 耗时。

## 目录

```
app/
├── main.py        # FastAPI 入口 + CORS
├── config.py      # 配置（base_url / api_key / model / mock）
├── schemas.py     # Pydantic 数据契约（Character/Prop/Scene/Assets）
├── llm.py         # 最底层：一次 LLM 调用 + json 解析 + Pydantic 校验
└── routers/
    └── pipeline.py  # /api/pipeline/extract-assets
```
