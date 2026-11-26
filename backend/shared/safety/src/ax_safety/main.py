"""ax-safety 서버 진입점 (포트 8001).

| 메서드·경로 | 용도 | 토큰 |
| --- | --- | --- |
| `POST /api/safety/executions` | 실행 관문 (`ExecuteRequest` → `ExecuteDecision`) | 에이전트 |
| `GET /api/safety/executions?context_id=` | 실행 기록 | 없음(조회) |
| `GET /api/safety/executions/{execution_id}` | 실행 기록 하나 (없으면 404) | 없음(조회) |
| `GET /api/safety/reviews?status=pending` | C 보류 큐 | 없음(조회) |
| `GET /api/safety/reviews/{review_id}` | 보류 건과 근거 리포트 | 없음(조회) |
| `POST /api/safety/reviews/{review_id}/decision` | 승인·거절 (`decided_as` 선택) | **사람만** |
| `GET /api/safety/audit-logs?context_id=` | 감사 로그 | 없음(조회) |
| `GET /api/safety/thresholds`, `GET /api/safety/registry` | 기준표·레지스트리 | 없음(조회) |
| `GET /api/safety/chat`, `GET /api/safety/chat/{id}` | 1:1 대화 목록·상세 | 사람(선택) |
| `POST /api/safety/chat`, `POST .../chat/{id}/close`·`/remove` | 대화 보내기·끝내기 | **사람만** |

안전 처리 로그 1:1 대화는 기준·보류 기록을 설명만 하고 아무것도 바꾸지 않는다 (`chat/turn.py`).
남의 대화는 요약만 보인다.

`ExecuteRequest.requester`는 믿지 않는다. 토큰에서 꺼낸 이름과 다르면 403이다.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from ax_agent_template import human_auth
from ax_agent_template.config import is_mock, refuse_mock_outside_tests, set_agent_name
from ax_agent_template.logging import log_event
from ax_contracts.schemas import ConnectionHoldNotice, ExecuteRequest
from ax_safety import cost_policy
from ax_safety.auth import agent_from_token, bearer, human_from_token, human_tokens
from ax_safety.chat import threads as chat_threads
from ax_safety.chat.turn import ChatRefused, ChatService
from ax_safety.gate import ConfirmError, Gate, Publisher, SelfApprovalError, Verifier
from ax_safety.rules import load_registry, load_thresholds
from ax_safety.store import Store, StoreWriteError
from ax_safety.verify import call_verifier, publish_safety_event

logger = logging.getLogger("ax.safety")

EXPIRE_INTERVAL_SECONDS = 15

# 대시보드(dashboard-web)가 보류 큐·실행 기록을 읽고 사람 결정을 보낸다
DEFAULT_CORS_ORIGINS = "http://localhost:3001,http://127.0.0.1:3001"


def cors_origins() -> list[str]:
    raw = os.getenv("SAFETY_CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


class ReviewDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    note: str | None = None
    # 공용 토큰으로 결정할 때 화면이 받은 승인자 이름(자기 신고). 개인 토큰이면 버린다
    decided_as: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)] | None
    ) = None
    # 서비스 이름 확인(선택). 승인일 때만 보류의 서비스 이름과 견준다. 안 보내면 예전과 같고,
    # 거절에는 검사하지 않는다. 감사 로그에는 남기지 않는다
    confirm: Annotated[str, StringConstraints(max_length=64)] | None = None


class CostPolicyRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_key: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    requested_value: int
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ChatSendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = None
    message: str
    # 11/20: 단톡방(전체 대화방 "all"·이슈 방 context_id)에서 보낼 때만 화면이 넣는다.
    # 새 대화를 만들 때만 저장(이어 쓰기에서는 무시). 단톡방 대화는 누구나 원문을 읽는다
    room_id: str | None = Field(default=None, min_length=1, max_length=64)


def _ok(data: Any) -> dict[str, Any]:
    return {"data": data, "meta": {}}


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _log_refusal(request: ExecuteRequest, reason_code: str) -> None:
    """인증 단계 거부를 공통 로그 `rejected`로 남긴다 (data `blocked_counts`가 읽는다).

    본문의 requester는 믿을 수 없으므로 `requester` 칸에는 넣지 않는다.
    """
    log_event(
        request.context_id,
        request.tenant_id,
        "rejected",
        {"reason_code": reason_code, "action": request.action},
        source="safety",
        skill=request.action,
        task_status="rejected",
    )


def _review_scope(authorization: str | None, x_session: str | None = None) -> Any:
    """승인 대기·실행 기록을 회사로 거르는 함수(11/24 회사 격리, 격리 시험 5번. 실행 기록 11/25).

    - 다른 회사 로그인 세션: 그 회사 건만
    - 캣북스 세션: 다른 회사로 등록된 건만 빼고(예전 건은 회사 값이 기본값이라 그대로 캣북스)
    - 공용 토큰·토큰 없음: 예전처럼 전부(운영 도구·옮기는 동안)
    """
    from ax_agent_template.companies import canonical_company_id, company_ids

    # 같은 출처 nginx가 읽기의 Authorization을 공용 토큰으로 바꿔 붙여서,
    # 세션 회사는 X-AX-Session으로도 받는다(11/25)
    token = human_auth.screen_session_token(authorization, x_session)
    company: str | None = None
    if token:
        try:
            company = human_auth.verify_session(token).company_id
        except human_auth.HumanAuthError as err:
            if err.code == "NO_COMPANY":  # 소속 회사가 없는 계정은 아무 회사 건도 못 본다
                return lambda _tenant: False
            company = None
    if company is None:
        return lambda _tenant: True
    mine = canonical_company_id(company) or "catbooks"
    if mine != "catbooks":
        return lambda tenant: canonical_company_id(tenant) == mine
    others = {c for c in company_ids() if c != "catbooks"}
    return lambda tenant: canonical_company_id(tenant) not in others


def create_app(
    gate: Gate | None = None,
    *,
    verifier: Verifier = call_verifier,
    publisher: Publisher = publish_safety_event,
    expire_interval: float | None = EXPIRE_INTERVAL_SECONDS,
    chat: ChatService | None = None,
) -> FastAPI:
    set_agent_name("safety")
    gate = gate or Gate(load_registry(), load_thresholds(), Store(), verifier, publisher)
    chat = chat or ChatService(chat_threads.get_store, gate)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        refuse_mock_outside_tests("safety")  # 실제 서버에 목 스위치가 남아 있으면 뜨지 않는다
        # 비용 정책 파일: 회사 폴더로 옮기기(또는 off면 옛 경로 되살리기)는 켤 때 한 번만.
        # 판정 경로(`effective`)는 경로만 읽어 잠금을 기다리지 않는다 (여러 회사 1-4)
        cost_policy.prepare()
        task = asyncio.create_task(_expire_loop(gate, expire_interval)) if expire_interval else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="ax-safety", lifespan=lifespan)
    app.state.gate = gate
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health")
    def health() -> dict:
        return {"data": {"status": "ok"}, "meta": {}}

    @app.post("/api/safety/executions")
    async def execute(
        http_request: Request,
        authorization: str | None = Header(default=None),
        x_ax_session: str | None = Header(default=None, alias=human_auth.SESSION_HEADER),
    ) -> JSONResponse:
        try:
            raw = await http_request.json()
            request = ExecuteRequest.model_validate(raw)
        except (ValueError, ValidationError) as exc:
            return _error(400, "INVALID_REQUEST", str(exc)[:500])
        try:
            requester = agent_from_token(bearer(authorization))
        except PermissionError as exc:
            gate.store.append_audit(
                gate._now(),
                request.context_id,
                request.tenant_id,
                "auth_rejected",
                detail={"claimed_requester": request.requester, "reason": str(exc)},
            )
            _log_refusal(request, "UNAUTHORIZED")
            return _error(401, "UNAUTHORIZED", str(exc))
        if requester != request.requester:
            gate.store.append_audit(
                gate._now(),
                request.context_id,
                request.tenant_id,
                "requester_mismatch",
                detail={"claimed_requester": request.requester, "token_requester": requester},
            )
            _log_refusal(request, "FORBIDDEN")
            return _error(403, "FORBIDDEN", "본문의 requester가 토큰과 다르다")

        decision = await gate.execute(request)
        # 공통 로그 종류는 계약 `LogKind`만 저장소에 남는다. 판정은 `execute_decided`다.
        log_event(
            request.context_id,
            request.tenant_id,
            "execute_decided",
            decision.model_dump(mode="json", exclude_none=True),
            source="safety",
            skill=request.action,
            task_status=decision.status,
            grade=decision.grade,
            requester=request.requester,
        )
        return JSONResponse(content=_ok(decision.model_dump(mode="json")))

    @app.post("/api/safety/connection-holds")
    async def connection_hold(
        http_request: Request,
        authorization: str | None = Header(default=None),
        x_ax_session: str | None = Header(default=None, alias=human_auth.SESSION_HEADER),
    ) -> JSONResponse:
        """개발 서비스의 긴급 취약 판정: 이 연결의 쓰기를 보류하거나 해제한다."""
        try:
            sender = agent_from_token(bearer(authorization))
        except PermissionError as exc:
            return _error(401, "UNAUTHORIZED", str(exc))
        if sender != "dev":
            return _error(403, "FORBIDDEN", "연결 쓰기 보류는 개발 서비스만 알린다")
        try:
            notice = ConnectionHoldNotice.model_validate(await http_request.json())
        except (ValueError, ValidationError) as exc:
            return _error(400, "INVALID_REQUEST", str(exc)[:500])
        gate.store.set_connection_hold(notice)
        gate.store.append_audit(
            gate._now(),
            f"connection:{notice.connection_id}",
            "default",
            "connection_hold" if notice.hold else "connection_release",
            detail=notice.model_dump(mode="json"),
        )
        return JSONResponse(content=_ok(notice.model_dump(mode="json")))

    @app.get("/api/safety/connection-holds")
    def connection_holds() -> dict:
        return _ok([n.model_dump(mode="json") for n in gate.store.list_connection_holds()])

    @app.get("/api/safety/executions")
    def list_executions(
        context_id: str | None = None,
        authorization: str | None = Header(default=None),
        x_ax_session: str | None = Header(default=None, alias=human_auth.SESSION_HEADER),
    ) -> dict:
        """실행 기록. 로그인 세션이면 그 회사 것만(승인 대기와 같은 규칙, 11/25 회사 분리)."""
        sees = _review_scope(authorization, x_ax_session)
        records = gate.store.list_executions(context_id)
        return _ok([r.model_dump(mode="json") for r in records if sees(r.request.tenant_id)])

    @app.get("/api/safety/executions/{execution_id}", response_model=None)
    def get_execution(
        execution_id: str,
        authorization: str | None = Header(default=None),
        x_ax_session: str | None = Header(default=None, alias=human_auth.SESSION_HEADER),
    ) -> dict | JSONResponse:
        """실행 기록 하나 (목록과 같은 모양). 없거나 다른 회사 것이면 404 `NOT_FOUND`."""
        record = gate.store.get_execution(execution_id)
        if record is None or not _review_scope(authorization, x_ax_session)(
            record.request.tenant_id
        ):
            return _error(404, "NOT_FOUND", "실행 기록이 없다")
        return _ok(record.model_dump(mode="json"))

    @app.get("/api/safety/reviews")
    async def list_reviews(
        status: Literal["pending", "approved", "rejected", "expired"] | None = None,
        authorization: str | None = Header(default=None),
        x_ax_session: str | None = Header(default=None, alias=human_auth.SESSION_HEADER),
    ) -> dict:
        await gate.expire_reviews()
        sees = _review_scope(authorization, x_ax_session)
        return _ok(
            [
                r.model_dump(mode="json")
                for r in gate.store.list_reviews(status)
                if sees(r.tenant_id)
            ]
        )

    @app.get("/api/safety/reviews/{review_id}", response_model=None)
    async def get_review(
        review_id: str,
        authorization: str | None = Header(default=None),
        x_ax_session: str | None = Header(default=None, alias=human_auth.SESSION_HEADER),
    ) -> dict | JSONResponse:
        await gate.expire_reviews()
        review = gate.store.get_review(review_id)
        if review is None or not _review_scope(authorization, x_ax_session)(review.tenant_id):
            return _error(404, "NOT_FOUND", "보류 건이 없다")
        return _ok(review.model_dump(mode="json"))

    @app.post("/api/safety/reviews/{review_id}/decision", response_model=None)
    async def decide_review(
        review_id: str,
        body: ReviewDecisionBody,
        authorization: str | None = Header(default=None),
        x_ax_session: str | None = Header(default=None, alias=human_auth.SESSION_HEADER),
    ) -> dict | JSONResponse:
        try:
            person = human_from_token(bearer(authorization))
        except PermissionError as exc:
            return _error(403, "HUMAN_ONLY", str(exc))
        found = gate.store.get_review(review_id)
        if found is not None and not _review_scope(authorization, x_ax_session)(found.tenant_id):
            return _error(404, "NOT_FOUND", "보류 건이 없다")  # 다른 회사 승인 대기는 없는 것처럼
        try:
            review = await gate.decide_review(
                review_id, body.decision, person, body.note, body.decided_as, body.confirm
            )
        except KeyError:
            return _error(404, "NOT_FOUND", "보류 건이 없다")
        except SelfApprovalError as exc:
            return _error(403, "SELF_APPROVAL", str(exc))
        except ConfirmError as exc:
            return _error(422, exc.code, str(exc))
        except StoreWriteError as exc:
            logger.exception("보류 결정 기록 실패: %s", review_id)
            return _error(503, "STATE_UNAVAILABLE", str(exc))
        except ValueError as exc:
            return _error(409, "ALREADY_DECIDED", str(exc))
        # 사람 결정을 공통 로그에 남긴다 (data `kpi_summary`의 승인 거절률).
        # 누가 결정했는지는 남기지 않는다
        log_event(
            review.context_id,
            review.tenant_id,
            "task_status",
            {
                "review_id": review.review_id,
                "execution_id": review.execution_id,
                "outcome": f"review_{review.status}",
            },
            source="safety",
            skill=review.action,
            task_status=review.status,
        )
        return _ok(review.model_dump(mode="json"))

    @app.get("/api/safety/audit-logs")
    def audit_logs(context_id: str | None = None) -> dict:
        return _ok([e.model_dump(mode="json") for e in gate.store.list_audit(context_id)])

    @app.get("/api/safety/thresholds")
    def thresholds() -> dict:
        return _ok(gate.thresholds.model_dump(mode="json"))

    # ---- 비용 임계값 화면 변경 (cost_policy). 값은 승인된 보류 건의 요청 기록에서만 적용된다
    def _policy_editor(authorization: str | None) -> human_auth.Human:
        """로그인 세션의 운영자·관리자만. 공용 토큰·에이전트·일반 회원은 못 한다."""
        token = bearer(authorization)
        if not token or token.startswith("mock:"):
            raise PermissionError("사람 로그인 토큰이 필요하다")
        try:
            human = human_auth.find_human(
                token, shared_tokens=human_tokens(), mock=is_mock("safety")
            )
        except human_auth.HumanAuthError as exc:
            raise PermissionError(exc.message) from None
        if human is None or human.shared or human.role not in ("operator", "admin"):
            raise PermissionError("정책 변경은 로그인한 운영자·관리자만 할 수 있다")
        return human

    def _policy_requests() -> list[dict[str, Any]]:
        applied = cost_policy.applied_review_ids()
        rows: list[dict[str, Any]] = []
        for review in gate.store.list_reviews(None):
            if review.action != cost_policy.ACTION or review.review_id in applied:
                continue
            record = gate.store.get_execution(review.execution_id)
            params = record.request.params if record else {}
            rows.append(
                {
                    "review_id": review.review_id,
                    "status": review.status,
                    "requested_at": review.created_at.isoformat(),
                    "decided_by": review.decided_by,
                    "rule_key": params.get("rule_key"),
                    "requested_value": params.get("requested_value"),
                    "requested_by": params.get("requested_by"),
                    "reason": params.get("reason"),
                }
            )
        return rows

    @app.get("/api/safety/cost-policy")
    async def cost_policy_view() -> dict:
        await gate.expire_reviews()
        data = cost_policy.describe(gate.base_thresholds)
        data["requests"] = _policy_requests()
        return _ok(data)

    @app.post("/api/safety/cost-policy/requests", response_model=None)
    async def cost_policy_request(
        body: CostPolicyRequestBody,
        authorization: str | None = Header(default=None),
        x_ax_session: str | None = Header(default=None, alias=human_auth.SESSION_HEADER),
    ) -> dict | JSONResponse:
        try:
            human = _policy_editor(authorization)
        except PermissionError as exc:
            return _error(403, "HUMAN_ONLY", str(exc))
        try:
            cost_policy.check_request(gate.base_thresholds, body.rule_key, body.requested_value)
        except cost_policy.PolicyError as exc:
            return _error(409 if str(exc) == "no_change" else 400, str(exc).upper(), str(exc))
        digest = hashlib.sha256(
            f"{body.reason}|{len(cost_policy.applied_review_ids())}".encode()
        ).hexdigest()[:16]
        decision = await gate.execute(
            ExecuteRequest(
                action=cost_policy.ACTION,
                requester="safety",
                context_id="cost_policy",
                idempotency_key=f"cost-policy:{body.rule_key}:{body.requested_value}:{digest}",
                params={
                    "rule_key": body.rule_key,
                    "requested_value": body.requested_value,
                    "reason": body.reason,
                    "requested_by": human.name,
                },
            )
        )
        return _ok(decision.model_dump(mode="json"))

    @app.post("/api/safety/cost-policy/requests/{review_id}/apply", response_model=None)
    async def cost_policy_apply(
        review_id: str,
        authorization: str | None = Header(default=None),
        x_ax_session: str | None = Header(default=None, alias=human_auth.SESSION_HEADER),
    ) -> dict | JSONResponse:
        try:
            _policy_editor(authorization)
        except PermissionError as exc:
            return _error(403, "HUMAN_ONLY", str(exc))
        review = gate.store.get_review(review_id)
        if review is None or review.action != cost_policy.ACTION:
            return _error(404, "NOT_FOUND", "정책 변경 보류 건이 없다")
        record = gate.store.get_execution(review.execution_id)
        if record is None:
            return _error(404, "NOT_FOUND", "정책 변경 요청 기록이 없다")
        # 같은 요청을 관문에 다시 물어 승인(executed)을 확인한다. 값은 기록된 요청에서만 읽는다
        decision = await gate.execute(record.request)
        if decision.status != "executed":
            return _error(409, "NOT_APPROVED", f"아직 승인되지 않았다: {decision.status}")
        params = record.request.params
        try:
            line = cost_policy.apply_change(
                gate.base_thresholds,
                key=str(params.get("rule_key")),
                value=params.get("requested_value"),  # type: ignore[arg-type]
                requested_by=str(params.get("requested_by") or ""),
                approved_by=str(review.decided_by or ""),
                review_id=review_id,
                reason=str(params.get("reason") or ""),
            )
        except cost_policy.PolicyError as exc:
            status = 409 if str(exc) == "already_applied" else 400
            return _error(status, str(exc).upper(), str(exc))
        return _ok(line)

    @app.get("/api/safety/registry")
    def registry() -> dict:
        return _ok(gate.registry.model_dump(mode="json"))

    # ---- 안전 처리 로그 1:1 대화 (조회·설명만). 업무 상태가 아니라 execute()를 거치지 않는다
    def _who(authorization: str | None, required: bool) -> str | None:
        try:
            return human_from_token(bearer(authorization))
        except PermissionError:
            if required:
                raise
            return None

    def _refused(exc: ChatRefused) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message)

    @app.get("/api/safety/chat", response_model=None)
    def chat_list(
        thread_id: str | None = None,
        authorization: str | None = Header(default=None),
        x_ax_session: str | None = Header(default=None, alias=human_auth.SESSION_HEADER),
    ) -> dict | JSONResponse:
        """목록. `?thread_id=`면 그 대화 하나 (`/chat/{id}`와 같다).

        공유 서버(251) nginx는 사람 토큰을 정해 둔 경로에만 붙여서, 화면은 상세도 이 주소로 읽는다.
        """
        who = _who(authorization, False)
        if thread_id is None:
            return _ok(chat.list(who))
        try:
            return _ok(chat.get(who, thread_id))
        except ChatRefused as exc:
            return _refused(exc)

    @app.get("/api/safety/chat/{thread_id}", response_model=None)
    def chat_get(
        thread_id: str,
        authorization: str | None = Header(default=None),
        x_ax_session: str | None = Header(default=None, alias=human_auth.SESSION_HEADER),
    ) -> dict | JSONResponse:
        try:
            return _ok(chat.get(_who(authorization, False), thread_id))
        except ChatRefused as exc:
            return _refused(exc)

    @app.post("/api/safety/chat", response_model=None)
    async def chat_send(
        body: ChatSendBody,
        authorization: str | None = Header(default=None),
        x_ax_session: str | None = Header(default=None, alias=human_auth.SESSION_HEADER),
    ) -> dict | JSONResponse:
        try:
            who = _who(authorization, True)
            return _ok(chat.send(who, body.thread_id, body.message, body.room_id))
        except PermissionError as exc:
            return _error(403, "HUMAN_ONLY", str(exc))
        except ChatRefused as exc:
            return _refused(exc)

    @app.post("/api/safety/chat/{thread_id}/close", response_model=None)
    def chat_close(
        thread_id: str,
        authorization: str | None = Header(default=None),
        x_ax_session: str | None = Header(default=None, alias=human_auth.SESSION_HEADER),
    ) -> dict | JSONResponse:
        try:
            return _ok(chat.close(_who(authorization, True), thread_id))
        except PermissionError as exc:
            return _error(403, "HUMAN_ONLY", str(exc))
        except ChatRefused as exc:
            return _refused(exc)

    @app.post("/api/safety/chat/{thread_id}/remove", response_model=None)
    def chat_remove(
        thread_id: str,
        authorization: str | None = Header(default=None),
        x_ax_session: str | None = Header(default=None, alias=human_auth.SESSION_HEADER),
    ) -> dict | JSONResponse:
        try:
            removed = chat.remove(_who(authorization, True), thread_id)
            return _ok({"thread_id": removed, "removed": True})
        except PermissionError as exc:
            return _error(403, "HUMAN_ONLY", str(exc))
        except ChatRefused as exc:
            return _refused(exc)

    return app


async def _expire_loop(gate: Gate, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await gate.expire_reviews()
        except Exception:
            logger.exception("보류 건 자동 거절 실패")


app = create_app()
