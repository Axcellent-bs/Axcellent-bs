"""ax-dashboard-api 서버 진입점."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ax_dashboard_api import handoffs
from ax_dashboard_api.envelope import install_error_handlers
from ax_dashboard_api.routes import router

DEFAULT_CORS_ORIGINS = "http://localhost:3001,http://127.0.0.1:3001"

app = FastAPI(title="ax-dashboard-api")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip()
        for o in (os.getenv("DASHBOARD_CORS_ORIGINS") or DEFAULT_CORS_ORIGINS).split(",")
        if o.strip()
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)
install_error_handlers(app)
app.include_router(router)
# 사람 할 일함: 에이전트가 넣는 창구(에이전트 토큰)와 사람이 보는 창구(쓰기는 사람 토큰)
app.include_router(handoffs.intake)
app.include_router(handoffs.inbox)


@app.get("/health")
def health() -> dict:
    return {"data": {"status": "ok"}, "meta": {}}
