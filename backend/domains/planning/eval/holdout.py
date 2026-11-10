"""기획 A2A holdout 평가 (처음 보는 seed로 한 번 잰다).

실행: `uv run python domains/planning/eval/holdout.py [--seed 20261110_02]`

무엇을 재나
- 기획 서비스가 받는 A2A skill 6개와 요청자 권한을 **실제 A2A 경로**로 보낸다:
  기획 서버 앱(`planning_agent.main.app`)에 `POST /a2a`(요청 부서 토큰)를 보내고, 서버의
  토큰 검증·`allowed_requesters`·입력 계약 검증·핸들러·서비스를 그대로 거친다.
- 정답은 기획 코드가 아니라 **계약·에이전트 카드에 적힌 규칙**으로 따로 계산한다
  (`agent-card.json` skill 설명, `ax_contracts.schemas.planning` 주석).
  기획 판정 함수를 부르지 않는다.
- LLM은 부르지 않는다. 판정이 코드 규칙인 skill만 잰다. LLM을 쓰는 곳(요청 내용 묶기,
  제안 할 일·이유 초안, 대화방)은 꺼서 규칙 경로(같은 제목·규칙 초안)로만 잰다.

영역·정답 규칙 표와 결과: 같은 폴더 `README.md`

규칙: 하네스는 dev seed(`DEV_SEED`)로만 고치고,
holdout seed(`HOLDOUT_SEED`)는 한 번 돌려 숫자만 본다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CARD = json.loads((HERE.parent / "agent-card.json").read_text(encoding="utf-8"))
SKILLS = {s["id"]: s for s in CARD["skills"]}
# 요청자 후보: 카드에 나오는 이름 + 기획 skill을 부를 일이 없는 부서·서비스
NAMES = sorted(
    {n for s in CARD["skills"] for n in s["allowed_requesters"]} | {"infra", "safety", "planning"}
)
# 이 프로세스 안에서만 쓰는 평가용 토큰 (비밀값 아님)
TOKENS = {n: f"planning-holdout-{n}" for n in NAMES}
os.environ["AX_AGENT_TOKENS"] = ",".join(f"{n}={t}" for n, t in TOKENS.items())
os.environ.pop("PLANNING_STATE_DIR", None)  # 상태 파일에 쓰지 않는다 (메모리에서만)
for key in list(os.environ):  # 목 모드를 꺼서 실서버처럼 토큰 명부로만 검증한다
    if key == "AGENT_TEMPLATE_MOCK" or key.endswith("_MOCK"):
        os.environ.pop(key)

import httpx  # noqa: E402

from ax_contracts.schemas import ExecuteDecision  # noqa: E402
from ax_workflow.models import Para, Request, Spec, Task  # noqa: E402
from planning_agent.domain.service import PlanningService  # noqa: E402
from planning_agent.main import app  # noqa: E402
from planning_agent.runtime import set_service  # noqa: E402

DEV_SEED = "20261110_01"
HOLDOUT_SEED = "20261110_02"
CLOSED = {"완료", "기각"}
LLM_SKILLS = {"planning_chat_send"}  # 허용된 요청자로 보내면 LLM을 부를 수 있어 거부 쪽만 잰다


# ---------------------------------------------------------------- 보내기


class Sent:
    """개발에 보내는 배포 지시를 받아 둔다 (실제로 보내지 않음)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, skill, ctx, tenant, payload, key) -> str:
        self.calls.append((skill, payload.model_dump(mode="json")))
        return "보냄"


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_holdout", grade="A", status="executed")


def fresh() -> tuple[PlanningService, Sent]:
    sent = Sent()
    svc = PlanningService(
        gate=_gate,
        dev_sender=sent,
        relater=None,
        hr_client=None,
        asker=None,
        spec_writer=None,
        insights=None,
        marketing=None,
        proposal_writer=None,
        spec_filler=None,
    )
    set_service(svc)
    return svc, sent


class A2A:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://planning"
        )
        self.n = 0

    async def send(
        self, skill: str, requester: str, payload: dict, key: str | None = "auto"
    ) -> dict:
        self.n += 1
        if key == "auto":
            key = f"holdout:{skill}:{self.n}"
        meta = {
            "context_id": f"ctx_holdout_{self.n:05d}",
            "tenant_id": "shop-demo",
            "token": TOKENS[requester],
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "idempotency_key": key,
        }
        r = await self.client.post("/a2a", json={"skill": skill, "meta": meta, "input": payload})
        return r.json()


# ---------------------------------------------------------------- 영역별 표본 · 정답


class Score:
    def __init__(self) -> None:
        self.n: Counter[str] = Counter()
        self.ok: Counter[str] = Counter()
        self.fails: dict[str, list[dict]] = defaultdict(list)
        self.mix: dict[str, Counter[str]] = defaultdict(Counter)  # 정답 종류별 표본 수

    def add(self, area: str, ok: bool, detail: dict, kind: str = "") -> None:
        self.n[area] += 1
        self.mix[area][kind] += 1
        self.ok[area] += ok
        if not ok and len(self.fails[area]) < 5:
            self.fails[area].append(detail)


async def area_access(a: A2A, rng: random.Random, sc: Score, n: int) -> None:
    fresh()
    for _ in range(n):
        skill = rng.choice(sorted(SKILLS))
        who = rng.choice(NAMES)
        allowed = who in SKILLS[skill]["allowed_requesters"]
        if allowed and skill in LLM_SKILLS:
            who = rng.choice([x for x in NAMES if x not in SKILLS[skill]["allowed_requesters"]])
            allowed = False
        res = await a.send(skill, who, {})
        forbidden = res.get("status") == "rejected" and res.get("outcome") == "FORBIDDEN_SKILL"
        sc.add(
            "access",
            forbidden == (not allowed),
            {"skill": skill, "requester": who, "allowed": allowed, "got": res.get("outcome")},
            "허용" if allowed else "거부",
        )


TITLES = [
    "결제 실패 안내 문구 바꾸기",
    "배송 지연 공지 띄우기",
    "쿠폰 적용 순서 바꾸기",
    "주문 내역 화면에 환불 시각 표시",
    "회원 탈퇴 사유 받기",
    "상품 상세 리뷰 정렬 바꾸기",
]


async def area_ticket(a: A2A, rng: random.Random, sc: Score, n: int) -> None:
    done = 0
    while done < n:
        svc, _ = fresh()
        # 정답 쪽 세계: 시드 요청을 그대로 읽어 시작한다 (입력 세계, 판정은 따로 한다)
        world = {
            r.id: {"title": r.title, "state": r.state, "dup": r.duplicate_of, "ctx": r.context_id}
            for r in svc.state.requests
        }
        sent: list[tuple[str, dict]] = []  # (키, 결과)
        for _ in range(min(10, n - done)):
            done += 1
            # 사람이 요청 하나를 끝내거나 기각한다 (세계 변화)
            if rng.random() < 0.25:
                rid = rng.choice(sorted(world))
                st = rng.choice(sorted(CLOSED))
                world[rid]["state"] = st
                next(r for r in svc.state.requests if r.id == rid).state = st
            roll = rng.random()
            if roll < 0.08 and sent:  # 같은 멱등성 키로 다시 보냄 → 같은 결과
                key, before = rng.choice(sent)
                res = await a.send(
                    "create_ticket",
                    rng.choice(SKILLS["create_ticket"]["allowed_requesters"]),
                    {"title": "아무거나", "evidence": "재전송"},
                    key,
                )
                sc.add(
                    "create_ticket",
                    res.get("output") == before,
                    {"case": "resend", "want": before, "got": res.get("output")},
                    "재전송",
                )
                continue
            title = rng.choice(TITLES)
            who = rng.choice(
                [x for x in SKILLS["create_ticket"]["allowed_requesters"] if x != "dev"]
            )
            body = {"title": title, "evidence": f"반복 문의 {rng.randint(3, 90)}건"}
            if roll < 0.13:  # 멱등성 키 없음
                res = await a.send("create_ticket", who, body, key=None)
                sc.add(
                    "create_ticket",
                    res.get("outcome") == "missing_idempotency_key",
                    {"case": "no_key", "got": res.get("outcome")},
                    "키 없음",
                )
                continue
            # 정답: 끝나지 않은 같은 제목 요청 (묶인 요청이면 그 원본) — 먼저 들어온 것부터
            want_dup = None
            for rid, r in world.items():
                if r["title"] != title:
                    continue
                root = r["dup"] or rid
                if root in world and world[root]["state"] not in CLOSED:
                    want_dup = root
                    break
            key = f"holdout:ticket:{a.n + 1}"
            res = await a.send("create_ticket", who, body, key)
            out = res.get("output") or {}
            used_ctx = {r["ctx"] for r in world.values()}
            if want_dup:
                ok = (
                    res.get("outcome") == "linked_duplicate"
                    and out.get("duplicate_of") == want_dup
                    and out.get("context_id") == world[want_dup]["ctx"]
                )
            else:
                ok = (
                    res.get("outcome") == "created"
                    and out.get("duplicate_of") is None
                    and str(out.get("context_id", "")).startswith("ctx_req_")
                    and out.get("context_id") not in used_ctx
                )
            ok = ok and out.get("request_id") not in world
            sc.add(
                "create_ticket",
                ok,
                {"title": title, "want_dup": want_dup, "got": res.get("outcome"), "out": out},
                "묶음" if want_dup else "새 요청",
            )
            if out.get("request_id"):
                got = next((r for r in svc.state.requests if r.id == out["request_id"]), None)
                world[out["request_id"]] = {
                    "title": title,
                    "state": got.state if got else "접수",
                    "dup": want_dup,
                    "ctx": out.get("context_id"),
                }
                sent.append((key, out))


def _release_world(rng: random.Random, svc: PlanningService) -> dict:
    """기획서 하나와 작업을 무작위로 만든다 (세계). 정답 계산에 쓸 값도 같이 돌려준다."""
    sid, rid = "SPEC-H1", "REQ-H1"
    n_req = rng.randint(1, 4)
    paras = []
    for i in range(n_req):
        paras.append(
            {
                "id": f"R{i + 1}",
                "kind": "req",
                "text": f"요구 {i + 1}",
                "incident": "inc_h" if rng.random() < 0.04 else None,
            }
        )
    if rng.random() < 0.6:
        paras.append({"id": "C1", "kind": "done", "text": "완료 기준", "incident": None})
    status = "확정" if rng.random() < 0.92 else "초안"
    question = "범위가 어디까지인가요?" if rng.random() < 0.06 else None
    paused = rng.random() < 0.05
    tasks: list[dict] = []
    t = 0
    for p in paras:
        k = 0 if rng.random() < 0.04 else rng.randint(1, 2)
        for _ in range(k):
            t += 1
            tasks.append({"id": f"T-H{t}", "para": p["id"]})
    svc.state.requests.append(
        Request.model_validate(
            {
                "id": rid,
                "context_id": "ctx_req_9001",
                "from": "support",
                "via": "create_ticket",
                "title": "holdout 요청",
                "evidence": "-",
                "state": "작업 중",
                "spec": sid,
                "paused": paused,
            }
        )
    )
    svc.state.specs.append(
        Spec(
            id=sid,
            title="holdout 기획서",
            request=rid,
            status=status,
            problem="-",
            goal="-",
            paras=[Para.model_validate(p) for p in paras],
            question=question,
        )
    )
    for x in tasks:
        svc.state.tasks.append(
            Task(id=x["id"], spec=sid, para=x["para"], title=x["id"], owner="dev", state="진행 중")
        )
    prs: list[dict] = []
    num = 100
    for x in tasks:
        for _ in range(0 if rng.random() < 0.04 else rng.choice([1, 1, 2])):
            num += 1
            r = rng.random()
            review = "match" if r < 0.85 else ("mismatch" if r < 0.93 else "none")
            prs.append(
                {"number": num, "task_id": x["id"], "merged": rng.random() < 0.95, "review": review}
            )
    if rng.random() < 0.05 or not prs:
        num += 1
        prs.append(
            {
                "number": num,
                "task_id": rng.choice([None, "T-ELSE"]),
                "merged": True,
                "review": "match",
            }
        )
    unknown = rng.random() < 0.04
    return {
        "spec_id": "SPEC-NOPE" if unknown else sid,
        "request_id": rid,
        "paras": paras,
        "status": status,
        "question": question,
        "paused": paused,
        "tasks": tasks,
        "prs": prs,
        "unknown": unknown,
    }


def _release_answer(w: dict) -> dict:
    """계약 `ReleaseHoldReason`·카드 설명대로 정답을 계산한다."""
    if w["unknown"]:
        return {"verdict": "hold", "reasons": {"spec_unknown"}, "items": None, "order": False}
    reasons: set[str] = set()
    if w["paused"]:
        reasons.add("paused_by_human")
    if w["status"] != "확정":
        reasons.add("spec_not_confirmed")
    if w["question"]:
        reasons.add("open_question")
    task_para = {x["id"]: x["para"] for x in w["tasks"]}
    if any(p["task_id"] not in task_para for p in w["prs"]):
        reasons.add("pr_outside_spec")
    items = {}
    for para in [p for p in w["paras"] if p["kind"] == "req"]:
        mine = [tid for tid, pid in task_para.items() if pid == para["id"]]
        prs = [p for p in w["prs"] if p["task_id"] in mine]
        merged_tasks = {p["task_id"] for p in prs if p["merged"]}
        if para["incident"]:
            st = "incident_open"
            reasons.add("linked_incident_open")
        elif not mine:
            st = "missing_task"
            reasons.add("para_without_task")
        elif set(mine) - merged_tasks:
            st = "not_merged"
            reasons.add("task_not_merged")
        else:
            st = "covered"
        reviews = {p["review"] for p in prs if p["merged"]}
        content = (
            "mismatch"
            if "mismatch" in reviews
            else "match"
            if reviews and reviews == {"match"}
            else "none"
        )
        items[para["id"]] = (st, content)
    if reasons:
        verdict = "hold"
    elif items and all(c == "match" for _, c in items.values()):
        verdict = "ready"
    else:
        verdict = "needs_human"
    order = verdict == "ready" and any(p["merged"] for p in w["prs"])
    return {"verdict": verdict, "reasons": reasons, "items": items, "order": order}


async def area_release(a: A2A, rng: random.Random, sc: Score, n: int) -> None:
    for _ in range(n):
        svc, sent = fresh()
        w = _release_world(rng, svc)
        want = _release_answer(w)
        res = await a.send(
            "request_release_check",
            "dev",
            {
                "spec_id": w["spec_id"],
                "request_id": w["request_id"],
                "env": "dev",
                "prs": w["prs"],
            },
        )
        out = res.get("output") or {}
        got_items = {i["para_id"]: (i["status"], i["content"]) for i in out.get("items", [])}
        orders = [c for c in sent.calls if c[0] == "receive_release_order"]
        ok = (
            out.get("verdict") == want["verdict"]
            and set(out.get("hold_reasons", [])) == want["reasons"]
            and (want["items"] is None or got_items == want["items"])
            and bool(out.get("order_sent")) == want["order"]
            and len(orders) == int(want["order"])
        )
        sc.add(
            "request_release_check",
            ok,
            {
                "want": {**want, "reasons": sorted(want["reasons"])},
                "got": {
                    "verdict": out.get("verdict"),
                    "reasons": out.get("hold_reasons"),
                    "items": got_items,
                    "order": out.get("order_sent"),
                },
            },
            want["verdict"],
        )


_NUM = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


def _nums(text: str) -> set[str]:
    return {m.replace(",", "") for m in _NUM.findall(text or "")}


def _fact(rng: random.Random) -> dict:
    kind = rng.random()
    if kind < 0.35:
        value = f"평균의 {rng.randint(11, 39) / 10}배"
    elif kind < 0.7:
        value = f"{rng.randint(1, 98)}%"
    else:
        value = f"{rng.randint(1_000, 250_000):,}회"
    label = rng.choice(["'독서' 검색 작년 11월", "숏츠 조회수 7일", "20대 비중", "같은 달 판매"])
    return {
        "label": label,
        "value": value,
        "source": rng.choice(["trend_insight_brief", "promo_concepts"]),
    }


def _insight(rng: random.Random, iid: str) -> tuple[dict, set[str]]:
    facts = [_fact(rng) for _ in range(rng.randint(1, 4))]
    title = rng.choice(
        ["가을 독서 루틴 숏츠", "수험생 필기 노트 소개", f"{rng.randint(2, 9)}분 책 추천"]
    )
    allowed = _nums(title)
    for f in facts:
        allowed |= _nums(f"{f['label']} {f['value']}")
    body_nums = set()
    parts = [f"- {f['label']}: {f['value']}" for f in facts[: rng.randint(0, len(facts))]]
    if rng.random() < 0.4:  # 근거에 없는 숫자를 설명에 섞는다
        x = str(rng.randint(10_000, 99_999))
        parts.append(f"- 목표 조회 {x}회")
        body_nums.add(x)
    body = "\n".join(parts) or None
    extra = (_nums(body or "") - allowed) if body else set()
    inp = {
        "insight_id": iid,
        "title": title,
        "summary": "근거 수치 요약",
        "skill": facts[0]["source"],
        "facts": facts,
        "products": rng.sample(["무지 노트", "만년필", "독서대"], rng.randint(0, 2)),
        "body_md": body,
    }
    return inp, extra


async def area_insight(a: A2A, rng: random.Random, sc: Score, n: int) -> None:
    done = 0
    while done < n:
        svc, _ = fresh()
        seen: dict[tuple[str, str], tuple[str, dict]] = {}  # (보낸 쪽, insight_id) → (PROP, 입력)
        for _ in range(min(8, n - done)):
            done += 1
            who = rng.choice(["data", "marketing"])
            r = rng.random()
            if r < 0.06:  # 근거 없음 → 계약 위반
                inp, _ = _insight(rng, f"ins_h{a.n}")
                inp["facts"] = []
                res = await a.send("receive_insight", who, inp)
                sc.add(
                    "receive_insight",
                    res.get("outcome") == "invalid_input",
                    {"case": "no_facts", "got": res.get("outcome")},
                    "근거 없음",
                )
                continue
            mine = [k for k in seen if k[0] == who]
            if r < 0.2 and mine:  # 같은 insight_id 재전송 → duplicate, 같은 PROP
                k = rng.choice(mine)
                res = await a.send("receive_insight", who, seen[k][1])
                out = res.get("output") or {}
                sc.add(
                    "receive_insight",
                    res.get("outcome") == "duplicate" and out.get("proposal_id") == seen[k][0],
                    {"case": "resend", "want": seen[k][0], "got": out},
                    "재전송",
                )
                continue
            iid = f"ins_h{a.n + 1}"
            inp, extra = _insight(rng, iid)
            res = await a.send("receive_insight", who, inp)
            out = res.get("output") or {}
            p = next((x for x in svc.state.proposals if x.id == out.get("proposal_id")), None)
            want_why = " · ".join(f"{f['label']} {f['value']}" for f in inp["facts"][:2])
            warned = {w for w in (p.warnings if p else []) if w.startswith("설명")}
            got_extra = set()
            for w in warned:
                got_extra |= set(w.split(": ", 1)[1].split(" (")[0].split(", "))
            ok = (
                res.get("outcome") == "created"
                and p is not None
                and p.status == "확인 대기"
                and p.from_ == who
                and [(f.label, f.value, f.source) for f in p.facts]
                == [(f["label"], f["value"], f["source"]) for f in inp["facts"]]
                and p.products == inp["products"]
                and p.idea == inp["title"]
                and p.why == want_why
                and p.target is None
                and got_extra == extra
                and not [w for w in p.warnings if not w.startswith("설명")]
            )
            sc.add(
                "receive_insight",
                ok,
                {
                    "case": "new",
                    "want_extra": sorted(extra),
                    "got": res.get("outcome"),
                    "warnings": p.warnings if p else None,
                },
                "새 제안·경고" if extra else "새 제안",
            )
            if p is not None:
                seen[(who, iid)] = (p.id, inp)


async def area_campaign(a: A2A, rng: random.Random, sc: Score, n: int) -> None:
    done = 0
    while done < n:
        svc, _ = fresh()
        props: dict[str, dict] = {}
        for i in range(3):
            inp, _ = _insight(rng, f"ins_c{a.n}_{i}")
            res = await a.send("receive_insight", "data", inp)
            pid = (res.get("output") or {}).get("proposal_id")
            props[pid] = {"status": "확인 대기", "result": None}
        for _ in range(min(10, n - done)):
            done += 1
            unknown = rng.random() < 0.12
            pid = "PROP-999" if unknown else rng.choice(sorted(props))
            stage = rng.choice(["published", "result"])
            body: dict[str, Any] = {"proposal_id": pid, "campaign_id": f"cmp_{pid}", "stage": stage}
            if stage == "result":
                direction = rng.choice(["늘었음", "같음", "줄었음", "확인 불가"])
                summary = rng.choice(["주문 증분 +4%", "변화 없음"])
                facts = [
                    {
                        "label": "캠페인 기간 주문 증분",
                        "value": summary.split()[-1] if "+" in summary else "0%",
                        "source": "campaign_effect",
                    }
                ]
                body.update({"direction": direction, "summary": summary, "facts": facts})
            else:
                body.update({"channel": "sns", "post_ref": f"post_{pid}"})
            who = rng.choice(SKILLS["receive_campaign_update"]["allowed_requesters"])
            # 정답
            if unknown:
                want = "unknown_proposal"
            elif stage == "published":
                st = props[pid]["status"]
                want = "duplicate" if st in ("진행 중", "성과 확인") else "applied"
                if want == "applied":
                    props[pid]["status"] = "진행 중"
            else:
                key = (body["summary"], json.dumps(body["facts"], ensure_ascii=False))
                want = "duplicate" if props[pid]["result"] == key else "applied"
                if want == "applied":
                    props[pid].update(status="성과 확인", result=key)
            res = await a.send("receive_campaign_update", who, body)
            p = next((x for x in svc.state.proposals if x.id == pid), None)
            ok = res.get("outcome") == want and (unknown or p.status == props[pid]["status"])
            sc.add(
                "receive_campaign_update",
                ok,
                {
                    "pid": pid,
                    "stage": stage,
                    "want": want,
                    "got": res.get("outcome"),
                    "status": p.status if p else None,
                },
                f"{stage} {want}",
            )


async def area_answer(a: A2A, rng: random.Random, sc: Score, n: int) -> None:
    done = 0
    while done < n:
        svc, _ = fresh()
        open_q: dict[str, str] = {}  # question_id → request_id
        answered: set[str] = set()
        for i in range(4):
            sid, rid, qid = f"SPEC-Q{i}", f"REQ-Q{i}", f"q_h{a.n}_{i}"
            svc.state.requests.append(
                Request.model_validate(
                    {
                        "id": rid,
                        "context_id": f"ctx_req_{8000 + i}",
                        "from": "support",
                        "via": "create_ticket",
                        "title": f"되묻기 요청 {i}",
                        "evidence": "-",
                        "state": "기획 중",
                        "spec": sid,
                    }
                )
            )
            svc.state.specs.append(
                Spec(
                    id=sid,
                    title=f"되묻기 기획서 {i}",
                    request=rid,
                    status="초안",
                    problem="-",
                    goal="-",
                    paras=[Para(id="R1", kind="req", text="요구")],
                    question="대상 범위는?",
                    question_id=qid,
                    question_to="inquiry",
                )
            )
            open_q[qid] = rid
        for _ in range(min(8, n - done)):
            done += 1
            r = rng.random()
            if r < 0.15:
                qid, rid, want = "q_nope", "REQ-Q0", "unknown_question"
            elif r < 0.3 and open_q:
                qid = rng.choice(sorted(open_q))
                rid = rng.choice(
                    [x for x in ["REQ-Q0", "REQ-Q1", "REQ-Q2", "REQ-Q3"] if x != open_q[qid]]
                )
                want = "unknown_question"
            elif r < 0.55 and answered:
                qid = rng.choice(sorted(answered))
                rid, want = "REQ-Q" + qid.rsplit("_", 1)[1], "duplicate"
            elif open_q:
                qid = rng.choice(sorted(open_q))
                rid, want = open_q.pop(qid), "applied"
                answered.add(qid)
            else:
                qid, rid, want = "q_nope", "REQ-Q0", "unknown_question"
            who = rng.choice(SKILLS["receive_planning_answer"]["allowed_requesters"])
            res = await a.send(
                "receive_planning_answer",
                who,
                {"question_id": qid, "request_id": rid, "answer": "주문 화면만요"},
            )
            ok = res.get("outcome") == want
            if want == "applied":
                sp = (
                    next(x for x in svc.state.specs if x.question_id is None and x.request == rid)
                    if any(x.request == rid and x.question_id is None for x in svc.state.specs)
                    else None
                )
                ok = ok and sp is not None and not sp.question
            sc.add(
                "receive_planning_answer",
                ok,
                {"qid": qid, "rid": rid, "want": want, "got": res.get("outcome")},
                want,
            )


REQ_STATES = ["접수", "기획 중", "작업 중", "완료", "기각"]
TASK_STATES = ["진행 중", "PR 검수", "머지 대기", "완료", "C 보류", "중단"]


async def area_dev_result(a: A2A, rng: random.Random, sc: Score, n: int) -> None:
    for _ in range(n):
        svc, _ = fresh()
        sp = next(x for x in svc.state.specs if x.request)
        rid = sp.request
        tasks = [
            {
                "id": f"T-D{i}",
                "spec": sp.id,
                "para": "R1",
                "title": f"작업 {i}",
                "owner": "dev",
                "state": rng.choice(TASK_STATES),
            }
            for i in range(rng.randint(1, 3))
        ]
        prs = [
            {
                "id": f"PR #{700 + i}",
                "number": 700 + i,
                "task": t["id"],
                "title": "pr",
                "branch": "b",
                "reviews": [],
                "gate": {"result": "통과", "lines": 3, "files": 1, "paths": "src"},
                "merged": rng.random() < 0.6,
            }
            for i, t in enumerate(tasks)
            if rng.random() < 0.8
        ]
        state = rng.choice(REQ_STATES)
        body = {
            "spec_id": sp.id,
            "request_id": rid,
            "request_state": state,
            "tasks": tasks,
            "prs": prs,
        }
        key = f"holdout:dev_result:{a.n + 1}"
        res = await a.send("receive_dev_result", "dev", body, key)
        req = next(r for r in svc.state.requests if r.id == rid)
        got_t = {t.id: t.state for t in svc.state.tasks if t.id.startswith("T-D")}
        got_p = {p.number: p.merged for p in svc.state.prs if p.number >= 700}
        ok = (
            res.get("status") == "completed"
            and (res.get("output") or {}).get("applied") == len(tasks) + len(prs)
            and req.state == state
            and got_t == {t["id"]: t["state"] for t in tasks}
            and got_p == {p["number"]: p["merged"] for p in prs}
        )
        # 같은 키로 다른 값을 다시 보내도 한 번만 적용된다
        resent = rng.random() < 0.5
        if resent:
            other = rng.choice([s for s in REQ_STATES if s != state])
            res2 = await a.send("receive_dev_result", "dev", {**body, "request_state": other}, key)
            ok = ok and req.state == state and res2.get("output") == res.get("output")
        sc.add(
            "receive_dev_result",
            ok,
            {"want_state": state, "got_state": req.state, "res": res.get("outcome")},
            "재전송 포함" if resent else "한 번",
        )


AREAS = [
    ("access", "요청자 권한 (allowed_requesters)", area_access),
    ("create_ticket", "변경 요청 접수 · 중복 묶기", area_ticket),
    ("request_release_check", "배포 전 기획서 대조", area_release),
    ("receive_insight", "인사이트 → 제안 · 숫자 경고", area_insight),
    ("receive_campaign_update", "캠페인 진행 · 성과 받기", area_campaign),
    ("receive_planning_answer", "되물은 질문 답 받기", area_answer),
    ("receive_dev_result", "개발 결과 받기 · 멱등성", area_dev_result),
]


async def evaluate(n: int, seed: str) -> dict:
    rng = random.Random(seed)
    a = A2A()
    sc = Score()
    for key, _, fn in AREAS:
        await fn(a, random.Random(f"{seed}:{key}:{rng.random()}"), sc, n)
    await a.client.aclose()
    areas = {
        key: {
            "label": label,
            "n": sc.n[key],
            "correct": sc.ok[key],
            "accuracy": sc.ok[key] / sc.n[key] if sc.n[key] else 0.0,
            "fails": sc.fails[key],
            "mix": dict(sorted(sc.mix[key].items())),
        }
        for key, label, _ in AREAS
    }
    total, ok = sum(sc.n.values()), sum(sc.ok.values())
    return {"seed": seed, "n": total, "correct": ok, "areas": areas}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200, help="영역마다 표본 수")
    ap.add_argument("--seed", default=DEV_SEED, help=f"dev seed 기본, holdout은 {HOLDOUT_SEED}")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--fails", action="store_true", help="틀린 표본 일부를 같이 출력")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    summary = asyncio.run(evaluate(args.n, args.seed))
    print(f"기획 A2A holdout  seed={args.seed}  표본 {summary['n']}개")
    print(f"{'영역':<36}{'표본':>6}{'정답':>6}{'정확도':>9}")
    for r in summary["areas"].values():
        print(f"{r['label']:<36}{r['n']:>6}{r['correct']:>6}{r['accuracy']:>9.1%}")
        print("   " + " · ".join(f"{k} {v}" for k, v in r["mix"].items()))
        if args.fails:
            for f in r["fails"]:
                print("   ✗", json.dumps(f, ensure_ascii=False, default=str)[:400])
    n, ok = summary["n"], summary["correct"]
    print(f"{'전체':<36}{n:>6}{ok:>6}{ok / n:>9.1%}")
    if args.out:
        args.out.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
