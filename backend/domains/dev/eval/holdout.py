"""개발 AI A2A holdout 평가 (처음 보는 무작위 기록): uv run python domains/dev/eval/holdout.py

무엇을 재나
- 개발 AI A2A skill 중 **정답을 코드 규칙으로 계산할 수 있는 6개**:
  `find_related_prs` · `verify_rollback_target` · `list_in_progress` · `verify_handover` ·
  `link_incident` · `list_dev_outcomes`.
- 빼는 것: `start_dev`·`receive_release_order`·`receive_request_pause`(기획 → 개발 흐름 이어 가기,
  정답이 "다음 단계로 넘어감"이라 숫자로 맞고 틀림을 셀 수 없음), `dev_chat_*`(LLM 대화).

어떻게 재나
- 세계(기록 묶음) 하나 = 요청·기획서·작업·PR·사건·코딩 기록을 seed로 무작위로 만든다.
  실제로 생기는 모양을 넣는다: 같은 시각 머지, 같은 버전 두 번 배포, 중단한 작업·닫은 PR,
  `./`로 시작하는 경로, 접두사가 비슷한 경로(`shop-api/payments_old`), 떼어 온 레포 경로
  (`shop-web/src/pages/Cart.tsx`), 기획서 문단이 없는 작업, 끝난·중복 연결 요청 등.
- 개발 AI는 **실서버와 같은 경로**로 부른다: 개발 서버 앱(`dev_agent.main:app`)의
  `POST /a2a` → handlers → service → 규칙. 실행 관문은 시험과 같은 목 관문이다(관문 판정은
  안전 관문 쪽 평가 대상이라 여기서는 늘 통과).
- 정답은 개발 코드(`ax_workflow.rules`·`ax_workflow.handover`·`dev_agent.outcomes`)를 쓰지 않고
  **문서(README 3장 표·계약 docstring)대로 이 파일에서 따로 계산**한다. 시연값 표(영역 표·
  담당자 대응·30분 구간)만 정책 파일처럼 가져다 쓴다.
- 문서가 정하지 않은 순서·동점 처리는 코드 docstring을 정답 규칙으로 삼는다(아래 `# 규칙:`).

규칙: 하네스는 dev seed(`dev`)로만 고치고, holdout seed(`20261110_01`)는 한 번 돌려 숫자만 본다.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

os.environ["AGENT_TEMPLATE_MOCK"] = "1"  # 시험과 같은 목 토큰·목 관문 (비밀값 없음)
os.environ.pop("PLANNING_STATE_DIR", None)  # 상태 파일에 쓰지 않는다
os.environ.pop("PLANNING_ROLE", None)

from fastapi.testclient import TestClient  # noqa: E402

from ax_agent_template import config  # noqa: E402
from ax_workflow import base as workflow_base  # noqa: E402
from ax_workflow.handover import (  # noqa: E402  정책 표만 쓴다 (판정 함수는 쓰지 않음)
    AREA_BY_PATH,
    AREA_BY_PATH_WORD,
    AREA_BY_WORD,
    OWNER_MEMBER_IDS,
    ROSTER_AREAS,
)
from ax_workflow.models import (  # noqa: E402
    PR,
    AutoPolicy,
    AutoReview,
    CodingRun,
    Incident,
    IncidentCandidate,
    Para,
    PrGate,
    Request,
    Spec,
    State,
    Task,
)
from ax_workflow.rules import ROLLBACK_LOOKBACK  # noqa: E402  시연값 30분
from dev_agent import runtime as dev_runtime  # noqa: E402
from dev_agent.main import app  # noqa: E402
from dev_agent.outcomes import SIMPLE_FILES, SIMPLE_LINES  # noqa: E402  시연값 50줄·3파일
from dev_agent.service import DevService  # noqa: E402

HOLDOUT_SEED = "20261110_01"
DEV_SEED = "dev"
BASE = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)
SPAN_MIN = 8 * 60  # 기록이 흩어지는 시간 (8시간)

PATHS = [
    "shop-api/payment/config.py",
    "shop-api/payment",
    "./shop-api/cart/add.py",
    "shop-api/payments_old/legacy.py",
    "shop-api/orders/cancel.py",
    "shop-web/checkout/Page.tsx",
    "shop-api/search/index.py",
    "shop-web/search/Box.tsx",
    "shop-api/inventory/stock.py",
    "shop-api/products/list.py",
    "shop-web/catalog/Grid.tsx",
    "shop-web/help/Faq.tsx",
    "shop-api/support/claims.py",
    "infra/deploy.yaml",
    "infrastructure/main.tf",
    "shop-web/src/pages/Cart.tsx",
    "shop-web/src/components/ProductCard.tsx",
    "shop-web/src/StockBadge.tsx",
    "shop-web/src/pages/Home.tsx",
    "docs/readme.md",
]
COMPONENTS = [
    None,
    None,
    "shop-api/",
    "shop-web/",
    "shop-api/payment",
    "shop-web/src",
    "payment",
    "catalog",
    "search",
    "support-desk",
    "inventory",
    "infra",
    "infra-ops",
]
TITLES = [
    "결제 실패 문구 정리",
    "장바구니 수량 버튼 정렬",
    "검색 결과 라벨 오타",
    "신간 배너 링크",
    "품절 표시 개선",
    "고객센터 문의 양식",
    "배포 스크립트 점검",
    "주문 취소 안내",
    "상품 카드 이미지",
    "홈 화면 여백",
    "로그 정리",
    "환불 안내 문구",
]
PARA_TEXTS = [
    "결제 실패 시 다시 시도 안내를 보여 준다",
    "검색어가 없으면 추천어를 보여 준다",
    "재고가 0이면 품절로 표시한다",
    "문의 접수 뒤 번호를 알려 준다",
    "서버 응답이 늦으면 안내를 띄운다",
    "버튼 간격을 맞춘다",
    "상품 상세에 저자 소개를 넣는다",
    "글자 크기를 키운다",
]
OWNERS = ["백엔드", "프론트", "백엔드", "프론트", "에이전트", "디자인"]
TASK_STATES = ["진행 중", "PR 검수", "머지 대기", "완료", "완료", "C 보류", "중단"]
REQ_STATES = ["접수", "기획 중", "작업 중", "완료", "완료", "중복 연결", "기각"]
CODING_STATUS = ["merged", "merged", "opened", "failed", "covered", "canceled", "waiting_human"]
MEMBERS = ["emp_01", "emp_02", "emp_03"]
SERVICES = ["shop-api", "shop-web"]

# README 3장 표: 진행 중 작업 = 이 상태들 (`완료`·`중단`은 끝난 작업)
OPEN_TASK_STATES = {"진행 중", "PR 검수", "머지 대기", "C 보류"}
CLOSED_REQ = {"완료", "기각"}
ITEM_PREFIXES = ("tkt_", "pr_", "dep_pr_")


def iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def at(minute: int) -> datetime:
    return BASE + timedelta(minutes=minute)


# ---------------------------------------------------------------- 세계 만들기


def make_world(rng: random.Random) -> State:
    n_req = rng.randint(3, 7)
    requests: list[Request] = []
    for i in range(1, n_req + 1):
        state = rng.choice(REQ_STATES)
        requests.append(
            Request(
                id=f"REQ-{i}",
                context_id=f"ctx_req_{i:04d}",
                **{"from": rng.choice(["support", "data", "infra", "marketing"])},
                via="eval",
                title=rng.choice(TITLES),
                evidence="eval",
                state=state,
            )
        )
    for r in requests:  # 중복 연결 요청은 앞 요청을 원본으로 둔다
        if r.state == "중복 연결":
            others = [o for o in requests if o.id != r.id and o.state != "중복 연결"]
            if others:
                r.duplicate_of = rng.choice(others).id

    specs: list[Spec] = []
    for i, r in enumerate([r for r in requests if r.state != "중복 연결"], start=1):
        paras = [
            Para(id=f"R{j}", kind="req", text=rng.choice(PARA_TEXTS))
            for j in range(1, rng.randint(1, 3) + 1)
        ]
        spec = Spec(
            id=f"SPEC-{i}",
            title=rng.choice(TITLES),
            request=r.id,
            status="확정",
            problem="eval",
            goal="eval",
            paras=paras,
        )
        if rng.random() < 0.8:  # 요청 → 기획서 연결이 양쪽에 다 있는 경우가 대부분
            r.spec = spec.id
        specs.append(spec)

    tasks: list[Task] = []
    for i in range(1, rng.randint(3, 9) + 1):
        spec = rng.choice(specs)
        para = rng.choice(spec.paras).id if rng.random() < 0.9 else "R9"  # 문단 없는 작업
        state = rng.choice(TASK_STATES)
        task = Task(
            id=f"T-{i}",
            spec=spec.id,
            para=para,
            title=rng.choice(TITLES),
            owner=rng.choice(OWNERS),
            state=state,
            discarded=state == "중단" and rng.random() < 0.5,
        )
        if rng.random() < 0.6:
            mode = "subscription" if rng.random() < 0.3 else "agent"
            attempts = rng.randint(0, 3)
            task.coding = CodingRun(
                status=rng.choice(CODING_STATUS),
                model="claude-sonnet" if mode == "agent" else None,
                attempts=attempts,
                updated_at=at(rng.randint(0, SPAN_MIN)),
                mode=mode,
                claimed_by=rng.choice(["예진", "병환"]) if mode == "subscription" else None,
                llm_calls=rng.randint(0, 6),
                llm_tokens=rng.randint(0, 40_000),
                hold_key="hold:1" if rng.random() < 0.15 else None,
            )
        tasks.append(task)

    prs: list[PR] = []
    number = rng.randint(20, 40)
    merge_minutes = [rng.randint(0, SPAN_MIN) for _ in range(rng.randint(4, 12))]
    for k, minute in enumerate(merge_minutes):
        if k and rng.random() < 0.15:
            minute = merge_minutes[k - 1]  # 같은 시각 머지
            merge_minutes[k] = minute
        merged = rng.random() < 0.65
        closed = (not merged) and rng.random() < 0.25
        task_id = rng.choice(tasks).id if rng.random() < 0.85 else "시연"
        paths = rng.sample(PATHS, rng.randint(1, 3))
        reviews = rng.choice([["통과"], ["불일치", "통과"], ["불일치", "불일치", "통과"]])
        lines = rng.choice([4, 18, 45, 50, 51, 120])
        files = rng.choice([1, 2, 3, 4])
        pr = PR(
            id=f"PR #{number}",
            number=number,
            task=task_id,
            title=rng.choice(TITLES),
            branch=f"gen/pr-{number}",
            reviews=reviews,
            gate=PrGate(result="통과", lines=lines, files=files, paths=", ".join(paths)),
            merged=merged,
            merged_at=at(minute) if merged else None,
            changed_paths=paths,
            auto=merged and rng.random() < 0.3,
            closed=closed,
            vcs_number=number + 800 if rng.random() < 0.3 else None,
        )
        if merged and rng.random() < 0.6:
            pr.deployed_version = f"{rng.choice(SERVICES)} v1.{rng.randint(0, 4)}"
        if not merged and rng.random() < 0.2:
            pr.auto_review = AutoReview(state="사람 확인", at=at(minute))
        prs.append(pr)
        number += rng.randint(1, 3)

    incidents: list[Incident] = []
    merged_prs = [p for p in prs if p.merged]
    for i in range(rng.randint(0, 2)):
        k = min(len(merged_prs), rng.randint(1, 2))
        cands = rng.sample(merged_prs, k) if merged_prs else []
        incidents.append(
            Incident(
                context_id=f"ctx_inc_{i + 1:04d}",
                title=f"사건 {i + 1}",
                opened_at=at(rng.randint(0, SPAN_MIN)),
                candidates=[
                    IncidentCandidate(pr=p.id, spec="-", para="-", merged_at=p.merged_at)
                    for p in cands
                ],
                linked=bool(cands),
                follow_up=rng.choice(requests).id if rng.random() < 0.7 else None,
            )
        )
    return State(
        requests=requests,
        specs=specs,
        tasks=tasks,
        prs=prs,
        incidents=incidents,
        auto_policy=AutoPolicy(level=rng.randint(0, 3), paths="shop-web/"),
    )


# ---------------------------------------------------------------- 정답 (문서대로 따로 계산)


def _under(path: str, prefix: str) -> bool:
    """README: 경로 단위 비교, `./`는 떼고 본다."""
    p = path.strip()
    if p.startswith("./"):
        p = p[2:]
    return p == prefix or p.startswith(prefix.rstrip("/") + "/")


def prefix_area(path: str) -> str | None:
    for prefix, area in AREA_BY_PATH:  # 규칙: 표 앞에서부터 처음 맞는 것
        if _under(path, prefix):
            return area
    return None


def path_area(path: str) -> str | None:
    """사건 component용: 접두사 표 → 없으면 폴더·파일 이름 낱말(CamelCase·-·_ 쪼개기) 앞부분."""
    found = prefix_area(path)
    if found:
        return found
    stem = path.rsplit(".", 1)[0] if re.search(r"\.[A-Za-z0-9]+$", path) else path
    words = re.split(r"[^A-Za-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", stem).lower())
    for w in (w for w in words if w):
        for key, area in AREA_BY_PATH_WORD:
            if w.startswith(key):
                return area
    return None


def text_area(*texts: str) -> str | None:
    for text in texts:
        for word, area in AREA_BY_WORD:
            if word in text:
                return area
    return None


def spec_para(s: State, task_id: str) -> tuple[str | None, str | None]:
    task = next((t for t in s.tasks if t.id == task_id), None)
    return (task.spec, task.para) if task else (None, None)


def related_row(s: State, p: PR) -> dict[str, Any]:
    spec_id, para_id = spec_para(s, p.task)
    return {
        "pr_number": p.number,
        "spec_id": spec_id,
        "para_id": para_id,
        "changed_paths": list(p.changed_paths),
    }


def merged_between(
    s: State, start: datetime, end: datetime, component: str | None = None
) -> list[PR]:
    def comp_ok(p: PR) -> bool:
        if not component:
            return True
        # README: component는 `changed_paths` 접두사. 총괄 사건 component(영역 값)도 받는다
        return any(x.startswith(component) or path_area(x) == component for x in p.changed_paths)

    hits = [
        p
        for p in s.prs
        if p.merged and p.merged_at is not None and start <= p.merged_at <= end and comp_ok(p)
    ]
    # 규칙: 최근 머지 순, 같은 시각이면 번호 큰 것 먼저
    return sorted(hits, key=lambda p: (p.merged_at, p.number), reverse=True)


def expect_related(s: State, q: dict[str, Any]) -> dict[str, Any]:
    # 규칙(docstring): 배포가 증상보다 먼저라 구간 시작 30분 앞부터 본다
    start = datetime.fromisoformat(q["window_from"].replace("Z", "+00:00")) - ROLLBACK_LOOKBACK
    end = datetime.fromisoformat(q["window_to"].replace("Z", "+00:00"))
    rows = merged_between(s, start, end, q.get("component"))
    return {"status": "completed", "candidates": [related_row(s, p) for p in rows]}


def judge_rollback(s: State, deployed_at: datetime, opened_at: datetime) -> dict[str, Any]:
    if deployed_at > opened_at:
        return {"approved": False, "reason_code": "deploy_after_incident", "related_prs": []}
    rows = merged_between(s, deployed_at - ROLLBACK_LOOKBACK, deployed_at)
    if rows:
        return {
            "approved": True,
            "reason_code": "deploy_before_incident",
            "related_prs": [related_row(s, p) for p in rows],
        }
    return {"approved": False, "reason_code": "no_deploy_in_window", "related_prs": []}


def expect_rollback(s: State, q: dict[str, Any], ctx: str) -> dict[str, Any]:
    if "params" not in q:  # 계약 형식
        d = datetime.fromisoformat(q["deployed_at"].replace("Z", "+00:00"))
        o = datetime.fromisoformat(q["incident_opened_at"].replace("Z", "+00:00"))
        return {"status": "completed", **judge_rollback(s, d, o)}
    # safety 검토 형식: 개발 자기 기록이 먼저 (그 버전으로 나간 가장 최근 머지 PR, 요청 사건)
    p = q["params"]
    target = f"{p['service']} {p['from_version']}"
    deploys = [x for x in s.prs if x.merged and x.merged_at and x.deployed_version == target]
    deployed: Any = (
        max(deploys, key=lambda x: (x.merged_at, x.number)).merged_at
        if deploys
        else p.get("deployed_at")
    )
    inc = next((i for i in s.incidents if i.context_id == ctx), None)
    opened: Any = inc.opened_at if inc else p.get("incident_opened_at")
    if deployed is None or opened is None:
        return {"status": "failed", "outcome": "invalid_input"}
    if isinstance(deployed, str):
        deployed = datetime.fromisoformat(deployed.replace("Z", "+00:00"))
    if isinstance(opened, str):
        opened = datetime.fromisoformat(opened.replace("Z", "+00:00"))
    return {"status": "completed", **judge_rollback(s, deployed, opened)}


def _request_of(s: State, task: Task) -> Request | None:
    spec = next((x for x in s.specs if x.id == task.spec), None)
    if spec is None:
        return None
    return next((r for r in s.requests if r.spec == spec.id), None) or next(
        (r for r in s.requests if r.id == spec.request), None
    )


def _task_area(s: State, task: Task) -> str | None:
    # README: PR 바뀐 경로 → 없으면 작업 제목·기획서 문단·기획서 제목 낱말
    paths = [x for p in s.prs if p.task == task.id for x in p.changed_paths]
    for x in paths:
        found = prefix_area(x)
        if found:
            return found
    spec = next((x for x in s.specs if x.id == task.spec), None)
    para = next((p for p in spec.paras if p.id == task.para), None) if spec else None
    return text_area(task.title, para.text if para else "", spec.title if spec else "")


def _pr_area(s: State, pr: PR, task: Task) -> str | None:
    for x in pr.changed_paths:
        found = prefix_area(x)
        if found:
            return found
    return _task_area(s, task)


def open_items(s: State) -> list[dict[str, Any]]:
    """README 3장 hr 연동 항목 표대로: 작업 순서 → PR 번호 순."""
    items: list[dict[str, Any]] = []
    tasks = {t.id: t for t in s.tasks}
    for t in s.tasks:
        owner = OWNER_MEMBER_IDS.get(t.owner)
        if owner is None or t.state not in OPEN_TASK_STATES or t.discarded:
            continue
        items.append(
            {
                "item_id": f"tkt_{t.id}",
                "kind": "ticket",
                "status": t.state,
                "owner_id": owner,
                "area": _task_area(s, t),
            }
        )
    for pr in sorted(s.prs, key=lambda p: p.number):
        task = tasks.get(pr.task)
        owner = OWNER_MEMBER_IDS.get(task.owner) if task else None
        if task is None or owner is None:
            continue
        if not pr.merged:
            if pr.closed:  # 닫은 PR은 머지하지 않았지만 열린 PR도 아니다 (status `open`만 있음)
                continue
            items.append(
                {
                    "item_id": f"pr_{pr.number}",
                    "kind": "pr",
                    "status": "open",
                    "owner_id": owner,
                    "area": _pr_area(s, pr, task),
                }
            )
            continue
        status = None
        for inc in s.incidents:
            if not any(c.pr == pr.id for c in inc.candidates):
                continue
            follow = next((r for r in s.requests if r.id == inc.follow_up), None)
            if follow is not None and follow.state not in CLOSED_REQ:
                status = "incident_follow_up_open"
                break
        if status is None:
            req = _request_of(s, task)
            if req is not None and req.state not in CLOSED_REQ:
                status = "merged_unconfirmed"
        if status:
            items.append(
                {
                    "item_id": f"dep_pr_{pr.number}",
                    "kind": "deployment",
                    "status": status,
                    "owner_id": owner,
                    "area": _pr_area(s, pr, task),
                }
            )
    return items


def expect_in_progress(s: State, q: dict[str, Any]) -> dict[str, Any]:
    area = q.get("area")
    if area is not None and area not in ROSTER_AREAS:
        return {"status": "completed", "items": []}  # README: 명부 값이 아니면 빈 목록
    rows = [
        i
        for i in open_items(s)
        if i["owner_id"] == q["owner_id"] and (area is None or i["area"] == area)
    ]
    return {"status": "completed", "items": rows}


def known_ids(s: State) -> set[str]:
    ids = {f"tkt_{t.id}" for t in s.tasks}
    for p in s.prs:
        ids.add(f"pr_{p.number}")
        if p.merged:
            ids.add(f"dep_pr_{p.number}")
    return ids


def expect_handover(s: State, q: dict[str, Any]) -> dict[str, Any]:
    area = q.get("area")
    if area is not None and area not in ROSTER_AREAS:
        return {"status": "failed", "outcome": "invalid_input"}
    current = expect_in_progress(s, q)["items"]
    listed = list(dict.fromkeys(q["handover_item_ids"]))
    missing = [i for i in current if i["item_id"] not in set(listed)]
    still_open = {i["item_id"] for i in open_items(s)}
    known = known_ids(s)
    stale = [
        x for x in listed if x.startswith(ITEM_PREFIXES) and x in known and x not in still_open
    ]
    approved = not missing
    return {
        "status": "completed",
        "approved": approved,
        "reason_code": "complete" if approved else "missing_items",
        "missing": missing,
        "stale_item_ids": stale,
    }


def expect_link(s: State, q: dict[str, Any]) -> dict[str, Any]:
    paras: list[str] = []
    for n in dict.fromkeys(q["pr_numbers"]):
        pr = next((p for p in s.prs if p.number == n), None)
        if pr is None:
            continue
        spec_id, para_id = spec_para(s, pr.task)
        spec = next((x for x in s.specs if x.id == spec_id), None)
        if spec and any(p.id == para_id for p in spec.paras):
            paras.append(f"{spec_id}:{para_id}")
    title = q.get("follow_up_title")
    if not title:
        return {"status": "completed", "linked_paras": paras, "follow_up": None, "outcome": None}
    by_id = {r.id: r for r in s.requests}
    for r in s.requests:  # README: 같은 제목의 끝나지 않은 요청이 있으면 그 ID (중복 연결은 원본)
        if r.title != title:
            continue
        origin = by_id.get(r.duplicate_of) if r.duplicate_of else r
        if origin is not None and origin.state not in CLOSED_REQ:
            return {
                "status": "completed",
                "linked_paras": paras,
                "follow_up": origin.id,
                "outcome": "linked_duplicate",
            }
    return {"status": "completed", "linked_paras": paras, "follow_up": "new", "outcome": None}


def _pr_of_task(s: State, task_id: str) -> PR | None:
    prs = [p for p in s.prs if p.task == task_id]
    for hit in ([p for p in prs if p.merged], [p for p in prs if not p.closed], prs):
        if hit:
            return hit[-1]  # 규칙(docstring): 머지된 것 → 열린 것 → 닫힌 것, 마지막 것
    return None


def expect_outcomes(s: State, q: dict[str, Any]) -> dict[str, Any]:
    since = q.get("since")
    until = q.get("until")
    since_t = datetime.fromisoformat(since.replace("Z", "+00:00")) if since else None
    until_t = datetime.fromisoformat(until.replace("Z", "+00:00")) if until else None
    coder = q.get("coder")
    rows: list[dict[str, Any]] = []
    for t in s.tasks:
        cr = t.coding
        if cr is None:
            continue
        if since_t and cr.updated_at < since_t or until_t and cr.updated_at > until_t:
            continue
        sub = cr.mode == "subscription"
        who = cr.claimed_by if sub else cr.model
        if coder == "agent" and sub:
            continue
        if coder not in (None, "agent") and (not sub or who != coder):
            continue
        pr = _pr_of_task(s, t.id)
        mism = pr.reviews.count("불일치") if pr else max(0, cr.attempts - 1)
        if pr is None:
            cx = "unknown"
        elif pr.gate.lines <= SIMPLE_LINES and pr.gate.files <= SIMPLE_FILES and not mism:
            cx = "simple"
        else:
            cx = "complex"
        held = bool(cr.hold_key) or (
            pr is not None and (pr.auto_review is not None or (pr.merged and not pr.auto))
        )
        rows.append(
            {
                "task_id": t.id,
                "mode": cr.mode,
                "coder": who,
                "status": cr.status,
                "complexity": cx,
                "changed_lines": pr.gate.lines if pr else None,
                "changed_files": pr.gate.files if pr else None,
                "llm_tokens": 0 if sub else cr.llm_tokens,
                "review_mismatches": mism,
                "held": held,
                "pr_number": (pr.vcs_number or pr.number) if pr else None,
                "merged": bool(pr and pr.merged) or cr.status == "merged",
            }
        )
    merged = [r for r in rows if r["merged"]]
    summary = {
        "total": len(rows),
        "merged": len(merged),
        "failed": sum(r["status"] == "failed" for r in rows),
        "covered": sum(r["status"] == "covered" for r in rows),
        "llm_tokens": sum(r["llm_tokens"] for r in rows),
        "by_complexity": dict(Counter(r["complexity"] for r in rows)),
    }
    return {"status": "completed", "outcomes": rows, "summary": summary}


# ---------------------------------------------------------------- 질의 만들기


def make_queries(rng: random.Random, s: State) -> list[tuple[str, str, dict[str, Any], str]]:
    """(skill, 요청자 토큰, 입력, context_id)."""
    qs: list[tuple[str, str, dict[str, Any], str]] = []
    merged = [p for p in s.prs if p.merged and p.merged_at]
    for _ in range(3):
        a = rng.randint(0, SPAN_MIN)
        b = a + rng.randint(5, 180)
        if merged and rng.random() < 0.5:  # 경계에 머지 시각을 둔다
            m = rng.choice(merged).merged_at
            a_t = m + timedelta(minutes=rng.choice([0, 30, 31]))
            b_t = a_t + timedelta(minutes=rng.randint(0, 60))
        else:
            a_t, b_t = at(a), at(b)
        q = {"window_from": iso(a_t), "window_to": iso(b_t)}
        comp = rng.choice(COMPONENTS)
        if comp:
            q["component"] = comp
        qs.append(("find_related_prs", rng.choice(["orchestrator", "infra", "data"]), q, "ctx_q"))

    for _ in range(2):  # 롤백: 계약 형식
        base_t = rng.choice(merged).merged_at if merged else at(rng.randint(0, SPAN_MIN))
        d = base_t + timedelta(minutes=rng.choice([0, 1, 10, 29, 30, 31, 45]))
        o = d + timedelta(minutes=rng.choice([-20, -1, 0, 1, 15, 60]))
        q = {
            "service": rng.choice(SERVICES),
            "target_version": f"v1.{rng.randint(0, 4)}",
            "deployed_at": iso(d),
            "incident_opened_at": iso(o),
        }
        qs.append(("verify_rollback_target", "safety", q, "ctx_q"))
    # 롤백: safety 검토 형식 (기록이 있으면 기록, 없으면 params)
    p: dict[str, Any] = {"service": rng.choice(SERVICES), "from_version": f"v1.{rng.randint(0, 4)}"}
    if rng.random() < 0.6:
        p["deployed_at"] = iso(at(rng.randint(0, SPAN_MIN)))
    if rng.random() < 0.6:
        p["incident_opened_at"] = iso(at(rng.randint(0, SPAN_MIN)))
    ctx = rng.choice([i.context_id for i in s.incidents] + ["ctx_inc_9999"])
    review = {"execution_id": f"exe_{rng.randint(1, 999)}", "action": "rollback_deployment",
              "requester": "infra", "params": p}  # fmt: skip
    qs.append(("verify_rollback_target", "safety", review, ctx))

    areas = [None, None, *ROSTER_AREAS, "shop-api/payment"]
    for _ in range(2):
        q = {"owner_id": rng.choice(MEMBERS)}
        area = rng.choice(areas)
        if area:
            q["area"] = area
        qs.append(("list_in_progress", rng.choice(["hr", "orchestrator"]), q, "ctx_q"))

    items = open_items(s)
    known = sorted(known_ids(s))
    for k in range(2):
        owner = rng.choice(MEMBERS[:2])
        area = rng.choice(areas)
        mine = [i["item_id"] for i in items if i["owner_id"] == owner]
        listed = [x for x in mine if rng.random() < 0.8]
        extra = rng.sample(known, min(len(known), rng.randint(0, 3)))
        fake = ["tkt_T-99", "pr_999", "card_0001", "doc_7"]
        listed += extra + rng.sample(fake, rng.randint(0, 2))
        rng.shuffle(listed)
        body: dict[str, Any] = {"handover_id": f"ho_{k}", "owner_id": owner,
                                "handover_item_ids": listed}  # fmt: skip
        if area:
            body["area"] = area
        if rng.random() < 0.5:  # safety 교차검증 형식
            body = {"execution_id": f"exe_h{k}", "action": "confirm_handover", "requester": "hr",
                    "params": {**{kk: v for kk, v in body.items() if kk != "handover_item_ids"},
                               "item_ids": listed, "completeness_percent": 90}}  # fmt: skip
        qs.append(("verify_handover", "safety", body, "ctx_q"))

    numbers = [x.number for x in s.prs]
    picks = rng.sample(numbers, min(len(numbers), rng.randint(1, 3))) + (
        [max(numbers) + 50] if rng.random() < 0.3 else []
    )
    if picks and rng.random() < 0.2:
        picks.append(picks[0])
    title = rng.choice([None, "결제 설정 변경은 부하 시험 먼저", *[r.title for r in s.requests]])
    inc_ctx = rng.choice([i.context_id for i in s.incidents] + ["ctx_inc_0500"])
    link = {"incident_context_id": inc_ctx, "pr_numbers": picks}
    if title:
        link["follow_up_title"] = title
    qs.append(("link_incident", rng.choice(["orchestrator", "infra"]), link, inc_ctx))

    oq: dict[str, Any] = {}
    if rng.random() < 0.5:
        oq["since"] = iso(at(rng.randint(0, SPAN_MIN // 2)))
    if rng.random() < 0.4:
        oq["until"] = iso(at(rng.randint(SPAN_MIN // 2, SPAN_MIN)))
    c = rng.choice([None, None, "agent", "예진", "병환", "아무개"])
    if c:
        oq["coder"] = c
    qs.append(("list_dev_outcomes", rng.choice(["hr", "orchestrator"]), oq, "ctx_q"))
    return qs


# ---------------------------------------------------------------- 실제 호출과 비교


async def _no_send(*_: Any, **__: Any) -> str:
    return "보냄 (평가: 버림)"


def call(client: TestClient, skill: str, token: str, payload: dict, ctx: str, key: str | None):
    body = {
        "meta": {
            "context_id": ctx,
            "token": f"mock:{token}",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key,
        },
        "skill": skill,
        "input": payload,
        "untrusted": {},
    }
    return client.post("/a2a", json=body).json()


def _rel(rows: list[dict]) -> list[tuple]:
    return [(r["pr_number"], r["spec_id"], r["para_id"], tuple(r["changed_paths"])) for r in rows]


def _items(rows: list[dict]) -> list[tuple]:
    return [(r["item_id"], r["kind"], r["status"], r["owner_id"], r["area"]) for r in rows]


def compare(skill: str, exp: dict, got: dict, s_before: State, svc: DevService) -> str | None:
    """맞으면 None, 틀리면 어디가 틀렸는지 한 줄."""
    if exp["status"] != got.get("status"):
        return f"status {exp['status']} != {got.get('status')}"
    if exp["status"] == "failed":
        return None if exp.get("outcome") == got.get("outcome") else "outcome"
    out = got.get("output") or {}
    if skill == "find_related_prs":
        return None if _rel(exp["candidates"]) == _rel(out["candidates"]) else "candidates"
    if skill == "verify_rollback_target":
        for k in ("approved", "reason_code"):
            if exp[k] != out[k]:
                return k
        return None if _rel(exp["related_prs"]) == _rel(out["related_prs"]) else "related_prs"
    if skill == "list_in_progress":
        return None if _items(exp["items"]) == _items(out["items"]) else "items"
    if skill == "verify_handover":
        if exp["approved"] != out["approved"]:
            return "approved"
        if _items(exp["missing"]) != _items(out["missing"]):
            return "missing"
        return None if exp["stale_item_ids"] == out["stale_item_ids"] else "stale_item_ids"
    if skill == "link_incident":
        if set(exp["linked_paras"]) != set(out["linked_paras"]):
            return "linked_paras"
        if exp["outcome"] != got.get("outcome"):
            return "outcome"
        fid = out.get("follow_up_request_id")
        if exp["follow_up"] is None:
            return None if fid is None else "follow_up"
        if exp["follow_up"] == "new":
            before = {r.id for r in s_before.requests}
            req = next((r for r in svc.state.requests if r.id == fid), None)
            ok = fid not in before and req is not None and req.state == "접수"
            return None if ok else "follow_up(new)"
        return None if fid == exp["follow_up"] else "follow_up"
    if skill == "list_dev_outcomes":
        keys = list(exp["outcomes"][0].keys()) if exp["outcomes"] else ["task_id"]
        got_rows = [{k: r.get(k) for k in keys} for r in out["outcomes"]]
        if exp["outcomes"] != got_rows:
            return "outcomes"
        sm = out["summary"]
        for k, v in exp["summary"].items():
            if sm.get(k) != v:
                return f"summary.{k}"
        return None
    return "unknown skill"


def run(seed: str, n: int, show: int) -> dict[str, Any]:
    config.set_agent_name("planning")  # 시험과 같다: execute 요청자 이름
    workflow_base.send_to_service = _no_send  # type: ignore[assignment]
    client = TestClient(app)
    total: Counter[str] = Counter()
    wrong: Counter[str] = Counter()
    why: dict[str, Counter[str]] = {}
    samples: list[dict[str, Any]] = []
    for w in range(n):
        rng = random.Random(f"{seed}:{w}")
        world = make_world(rng)
        for qi, (skill, token, payload, ctx) in enumerate(make_queries(rng, world)):
            svc = dev_runtime.set_service(DevService(dev_sender=_no_send, relater=None))
            svc.state = world.model_copy(deep=True)
            before = world.model_copy(deep=True)
            if skill == "find_related_prs":
                exp = expect_related(before, payload)
            elif skill == "verify_rollback_target":
                exp = expect_rollback(before, payload, ctx)
            elif skill == "list_in_progress":
                exp = expect_in_progress(before, payload)
            elif skill == "verify_handover":
                body = payload
                if "params" in payload:
                    pp = payload["params"]
                    body = {"owner_id": pp["owner_id"], "area": pp.get("area"),
                            "handover_item_ids": pp["item_ids"]}  # fmt: skip
                exp = expect_handover(before, body)
            elif skill == "link_incident":
                exp = expect_link(before, payload)
            else:
                exp = expect_outcomes(before, payload)
            key = f"{ctx}:link_incident:{w}:{qi}" if skill == "link_incident" else None
            got = call(client, skill, token, payload, ctx, key)
            diff = compare(skill, exp, got, before, svc)
            if skill == "link_incident" and diff is None:  # 같은 키 두 번째는 이전 결과
                again = call(client, skill, token, payload, ctx, key)
                if again.get("output") != got.get("output"):
                    diff = "idempotency"
            total[skill] += 1
            if diff:
                wrong[skill] += 1
                why.setdefault(skill, Counter())[diff] += 1
                if len(samples) < show:
                    samples.append({"world": w, "skill": skill, "diff": diff, "input": payload})
    by_skill = {
        k: {
            "cases": total[k],
            "correct": total[k] - wrong[k],
            "accuracy": round((total[k] - wrong[k]) / total[k], 4),
            "wrong_fields": dict(why.get(k, {})),
        }
        for k in sorted(total)
    }
    all_n = sum(total.values())
    return {
        "seed": seed,
        "worlds": n,
        "cases": all_n,
        "accuracy": round((all_n - sum(wrong.values())) / all_n, 4),
        "by_skill": by_skill,
        "samples": samples,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100, help="세계 수 (세계마다 질의 12개)")
    ap.add_argument("--holdout", action="store_true", help=f"holdout seed {HOLDOUT_SEED}")
    ap.add_argument("--seed", default=None)
    ap.add_argument("--show", type=int, default=0, help="틀린 질의 몇 개 보이기 (dev seed만)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    seed = args.seed or (HOLDOUT_SEED if args.holdout else DEV_SEED)
    show = 0 if seed == HOLDOUT_SEED else args.show  # holdout은 숫자만 본다
    result = run(seed, args.n, show)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
