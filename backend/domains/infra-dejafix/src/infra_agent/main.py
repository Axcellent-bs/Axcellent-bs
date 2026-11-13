"""ax-agent-infra 서버 진입점.

템플릿 `create_agent_app(card, HANDLERS)`가 `/.well-known/agent-card.json`, `/health`,
`POST /a2a`를 준다. 여기에 시연 준비 `POST /api/infra/demo-deploy`(운영자 admin만)를 더한다.
시작할 때 DejaFix 현재 장애 감시(`dejafix_watch`, `INFRA_DEJAFIX_URL`이 있을 때만)를 켠다.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import AwareDatetime, BaseModel, Field

from ax_agent_template.server import create_agent_app, verify_token
from ax_contracts.schemas import AgentCard
from infra_agent import dejafix_watch
from infra_agent.chat.a2a import CHAT_HANDLERS
from infra_agent.deployments import Deployment
from infra_agent.handlers import HANDLERS
from infra_agent.runtime import get_runtime

ADMIN = "admin"


def _card_path() -> Path:
    """`agents/infra-dejafix/agent-card.json`을 찾는다. 컨테이너·레포 루트 실행 모두 동작한다."""
    override = os.getenv("INFRA_AGENT_CARD")
    candidates = [
        Path(override) if override else None,
        Path.cwd() / "domains" / "infra-dejafix" / "agent-card.json",
        Path(__file__).resolve().parents[2] / "agent-card.json",
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    raise FileNotFoundError("agent-card.json을 찾을 수 없다 (INFRA_AGENT_CARD로 경로를 지정한다)")


def load_card() -> AgentCard:
    return AgentCard.model_validate_json(_card_path().read_text(encoding="utf-8"))


def require_admin(authorization: str | None = Header(default=None)) -> None:
    """운영자(admin) 에이전트 토큰만. 없거나 모르면 401, 다른 에이전트면 403."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "토큰이 필요하다 (Authorization: Bearer 토큰)")
    try:
        name = verify_token(token.strip())
    except PermissionError as exc:
        raise HTTPException(401, "토큰을 검증할 수 없다") from exc
    if name != ADMIN:
        raise HTTPException(403, "운영자(admin) 토큰이 필요하다")


class DemoDeployInput(BaseModel):
    service: str = "shop-api"
    components: list[str] = Field(default_factory=lambda: ["payment"], min_length=1)
    change_summary: str = "결제 타임아웃 재조정 (시연 배포)"
    # 아래는 잘못된 배포 시연(`demo_deploy.sh --bad`)용. 없으면 예전처럼 버전을 하나 올려
    # 기록만 한다
    version: str | None = Field(default=None, min_length=1, max_length=32)
    # 이 배포 직전에 돌던 버전과 그 버전이 된 시각(쇼핑몰이 알려 준 값). 기록이 없으면 기준으로
    # 남긴다
    previous_version: str | None = Field(default=None, min_length=1, max_length=32)
    previous_at: AwareDatetime | None = None
    applies_to: Literal["shop_payment_config"] | None = None


class DemoResetInput(BaseModel):
    service: str = "shop-api"
    # 사람이 되돌린 뒤 지금 돌고 있는 버전(쇼핑몰이 알려 준 값)
    running_version: str = Field(min_length=1, max_length=32)


# 사람과 직접 대화 skill(`infra_chat_*`)은 조회·설명만 한다
app = create_agent_app(load_card(), {**HANDLERS, **CHAT_HANDLERS})
_template_lifespan = app.router.lifespan_context


@contextlib.asynccontextmanager
async def _lifespan(a: FastAPI) -> AsyncIterator[None]:
    async with _template_lifespan(a):
        watch = dejafix_watch.start()
        try:
            yield
        finally:
            if watch:
                watch.cancel()


app.router.lifespan_context = _lifespan


@app.post("/api/infra/demo-deploy", dependencies=[Depends(require_admin)])
def demo_deploy(body: DemoDeployInput | None = None) -> dict:
    """시연 준비: 지금 시각으로 배포 하나를 기록한다 (`scripts/demo_deploy.sh`).

    실제 모드 진단은 장애 직전 60분 안의 배포만 원인 후보로 본다. 시연 데이터의 마지막 배포는
    오래전이라, 장애를 넣기 직전에 이 기록을 남겨야 원인 배포 → 자동 롤백 흐름이 나온다.
    프로세스 메모리에만 남고 재시작하면 사라진다.
    """
    body = body or DemoDeployInput()
    rt = get_runtime()
    if (body.previous_version is None) != (body.previous_at is None):
        raise HTTPException(422, "previous_version과 previous_at은 같이 준다")
    baseline = None
    if body.previous_version is not None and body.previous_at is not None:
        before = rt.history.running(body.service)
        if before is None or before.version != body.previous_version:
            # 직전에 돌던 버전 기록이 없다: 기준으로 남긴다. 바꾼 구성요소가 없어 원인 후보가
            # 아니고, 되돌릴 때 돌아갈 버전이 된다
            baseline = rt.history.record(
                service=body.service,
                version=body.previous_version,
                components=(),
                change_summary=f"{body.previous_version} (기준: 이 배포 전에 돌던 버전)",
                at=body.previous_at,
                applies_to=body.applies_to,
            )
    deployment = rt.history.record(
        service=body.service,
        version=body.version or rt.history.next_version(body.service),
        components=tuple(body.components),
        change_summary=body.change_summary,
        at=rt.clock(),
        applies_to=body.applies_to,
    )
    data = _deployment_view(deployment)
    if baseline is not None:
        data["baseline"] = _deployment_view(baseline)
    return {"data": data}


@app.post("/api/infra/demo-deploy/reset", dependencies=[Depends(require_admin)])
def demo_deploy_reset(body: DemoResetInput) -> dict:
    """시연 정리: 사람이 결제 설정을 손으로 되돌렸다(`demo_deploy.sh --reset`).

    지금 돌고 있다고 기록된 배포가 쇼핑몰의 실제 버전과 다르면 그 배포를 "되돌림"으로 표시한다.
    그래야 다음 진단이 이미 내린 배포를 원인 후보로 보지 않고, 다음 롤백이 맞는 버전으로 간다.
    """
    rt = get_runtime()
    marked: list[dict[str, Any]] = []
    while (top := rt.history.running(body.service)) is not None and (
        top.version != body.running_version
    ):
        rt.history.mark_rolled_back(top)
        marked.append(_deployment_view(top))
    return {"data": {"service": body.service, "rolled_back": marked}}


def _deployment_view(deployment: Deployment) -> dict[str, Any]:
    return {
        "deploy_id": deployment.deploy_id,
        "service": deployment.service,
        "version": deployment.version,
        "deployed_at": deployment.deployed_at.isoformat().replace("+00:00", "Z"),
        "components": list(deployment.components),
        "applies_to": deployment.applies_to,
    }
