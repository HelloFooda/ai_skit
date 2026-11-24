from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import assets, media, pipeline
from .api.errors import register_error_handlers
from .observability.logging import configure_logging
from .observability.middleware import RequestLoggingMiddleware

# 启动时配置一次日志：stderr 必开，log_dir 非空时追加 JSON 文件。
configure_logging()

app = FastAPI(title="ai_skit backend", version="0.1.0")

# 异常 → 状态码的映射集中在这里，路由里不再各写 try/except。
register_error_handlers(app)

# 前端跑在 5174，放行跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# 日志中间件最后 add → 最外层执行：先绑 run_id，再进 CORS 与路由。
app.add_middleware(RequestLoggingMiddleware)

app.include_router(pipeline.router)
app.include_router(assets.router)
app.include_router(media.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
