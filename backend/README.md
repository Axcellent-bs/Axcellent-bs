# backend

AXcellent AI 백엔드. ax-company-hq/ax-backend(dev `04f0e28`, 10/28)를 복사해 와서 **서비스 13개 단위**로 나눴다.
부서가 아니라 서비스 단위로 팔기 때문에(고객사가 필요한 서비스만 고름) 에이전트도 서비스마다 하나씩 띄운다.

## 폴더

| 폴더 | 내용 |
| --- | --- |
| `shared/` | 공통: `contracts`(계약), `agent-template`(에이전트 틀·서비스 대응표), `safety`(안전 관문), `memory`(회사 기억), `dashboard-api`(화면 중계), `shop-api`(고객사 쇼핑몰 시뮬·총괄 읽기 중계) |
| `domains/` | 업무 로직. 원래 부서 패키지 그대로(support·hr·marketing·finance·data·planning·infra-dejafix). 직접 띄우지 않고 서비스가 가져다 쓴다 |
| `services/` | 서비스 에이전트 13개. 각자 카드(`agent-card.json`)·포트·실행 설정을 가진다 |
| `infra/` | docker compose (`infra/compose.yml`이 `shared/*`·`services/*` compose를 모은다) |
| `scripts/` | `split_services.py`(서비스 폴더 생성), `run_all.sh`(로컬 전체 실행) |
| `docs/` | ax-backend 설계 문서 복사본 |

## 서비스 13개

| 서비스 | 화면 | 포트 | 업무 로직 | skill |
| --- | --- | --- | --- | --- |
| `orchestrator` | 총괄·사건 | 8000 | services/orchestrator | 1 |
| `inquiry` | 고객 문의 | 8102 | domains/support | 17 |
| `claims` | 클레임·보상 | 8112 | domains/support | 14 |
| `reviews` | 리뷰·지식 | 8122 | domains/support | 2 |
| `coupon` | 공지·쿠폰 | 8103 | domains/marketing | 5 |
| `finance` | 재무 관리 | 8105 | domains/finance | 4 |
| `data` | 데이터 분석 | 8106 | domains/data | 4 |
| `handover` | 인수인계 | 8104 | domains/hr | 6 |
| `decisions` | 결정 기록 | 8114 | domains/hr | 5 |
| `planning` | 요청·기획서 | 8101 | domains/planning | 2 |
| `dev` | 개발·배포 | 8111 | domains/planning | 6 |
| `incident` | 장애 대응 | 8107 | domains/infra-dejafix | 3 |
| `hosts` | PC·서버 연결 | 8117 | 없음 | 0 (`services/hosts/README.md`) |

대응표는 `shared/agent-template/src/ax_agent_template/services.py` 한 곳에 있다. 서비스 id는 화면 `frontend/src/demo/services.ts`와 같다.

## 나눈 방식

- 서비스는 부서 패키지의 앱을 그대로 띄우고, 카드에는 자기 skill만 올린다. 카드에 없는 skill은 템플릿 ACL이 막는다.
- 다른 에이전트는 예전처럼 부서 이름(`support`)으로 불러도 된다. `call_agent`가 skill을 보고 맡은 서비스(`claims`)로 보낸다.
- 받는 쪽 허용 목록에는 부서 이름이 적혀 있어서, 서비스가 보낸 요청은 부서 이름으로도 인정한다(`claims` → `support`).
- 총괄은 부서 단위로 일을 나눈다. 카드를 모을 때 같은 부서의 서비스 카드를 합쳐 부서 카드 하나로 본다.

## 로컬 실행 (실제 모드, 기본)

목 모드 없이 실제 LLM·실제 에이전트 토큰·이벤트 버스·파일 DB로 돈다. 고객사는 캣북스(쇼핑몰 `shop-api`)이고,
AXcellent 에이전트들이 캣북스의 실제 주문·결제 데이터로 A2A 작업을 한다.

```bash
cd backend
uv sync --all-packages
cp .env.example .env            # LLM_MODEL·LLM_BASE_URL·LLM_API_KEY(·EMBED_MODEL)를 채운다. 커밋하지 않는다
bash scripts/run_all.sh         # 16개 서버 + 캣북스 고객 주문 트래픽. 로그 backend/.logs/, 데이터 backend/.data/
bash scripts/run_web.sh         # 다른 터미널: 화면(frontend 3090)을 실제 모드 토큰으로 켠다
bash scripts/run_all.sh stop
```

- Docker가 필요하다. 이벤트 버스(Redis)를 컨테이너 `ax-redis`로 띄운다 (`REDIS_URL`을 주면 그걸 쓴다).
- 에이전트 토큰(`AX_AGENT_TOKENS`)·사람 토큰은 켤 때마다 새로 만들어 프로세스에만 넘긴다. 화면에 넘길 값만
  `backend/.data/web.env`에 쓴다 (커밋 안 함).
- 캣북스 데이터(`.data/shop.db`)·공통 로그(`.data/common_log.db`)·인사(`.data/hr.db`)는 파일이라 다시 켜도 남는다.
- `scripts/shop_traffic.py`가 3초마다 캣북스 고객 주문·결제를 넣는다. 결제 성공·실패는 쇼핑몰이 정한다.
  결제가 실패한 고객 일부(30%)는 쇼핑몰 문의 창구로 그 주문에 대해 문의를 남긴다 → 고객지원 AI가 받아 답변 초안을 쓴다.
- 장애 넣기·빼기는 운영자 명령이다: `bash scripts/shop_fault.sh failure|latency|clear|status`. 테스트 기록을 지우려면 `bash scripts/run_all.sh reset`.
- 캣북스 쇼핑몰 화면(고객이 보는 곳)은 ax-frontend의 shop-web이다. 같은 shop-api(8010)에 붙여 띄우면 장애 때 결제 화면에
  "결제 서버에 문제가 있어 결제하지 못했습니다"가 뜬다: `cd ax-frontend && npm run dev:shop` (3000, CORS 허용됨).
  마케팅 AI가 게시한 공지 배너는 shop-web에 아직 그리는 곳이 없다(ax-frontend 쪽 작업).
- 장애 대응 흐름: AI 대화방의 "캣북스에 장애 넣기" → 캣북스 결제가 실제로 실패 → 쇼핑몰 감시가 `shop.degraded`
  (20초 지속, `SHOP_DEGRADED_SUSTAIN_S`) → Redis → 총괄이 사건을 열고 7개 부서를 실제로 부른다.
- 예전 목 모드는 `bash scripts/run_all.sh mock` (시드 데이터·가짜 LLM, 네트워크 없이 화면 확인용).

## 업무 요청 라우터

고객사는 서비스를 골라서 산다. 서비스 하나만 사도 다른 부서(사람)와 협업이 되고, 여러 개 사면 그 서비스 에이전트끼리
A2A로 협업한다. 같은 요청(`call_agent(target, req)`)이 받는 쪽만 바뀐다.

```
call_agent("finance", req)
  → route_target(): skill을 맡은 서비스 (finance)
  → 켠 서비스?  예 → 지금처럼 A2A (POST <finance>/a2a)
               아니오 → 조직도에서 맡은 부서를 찾아 사람 할 일함(dashboard-api)에 HandoffTask로 넣는다
                        → AgentResult(status=input-required, outcome="handed_to_human", output.task_id)
                        조직도에도 없으면 → failed + outcome "no_assignee" (지어내지 않는다)
사람이 콘솔에서 결과를 적고 끝내면 → dashboard-api가 handoff.completed 이벤트 발행 (context_id·task_id, 결과는 untrusted)
```

- **켠 서비스**: `backend/.env`의 `AX_ENABLED_SERVICES` (쉼표 목록, 예: `inquiry,claims`). 비어 있으면 전부 켠다(예전 동작).
  `run_all.sh`가 읽어서 모든 서버에 넘긴다. 서비스 13개만 이 목록을 따르고, 공통 서버(safety·memory·shop-api·dashboard-api)와
  주소로 부른 대상은 늘 켜져 있다. 서버는 목록과 관계없이 다 띄운다(꺼진 서비스로 가는 요청만 사람에게 간다).
- **조직도**: `config/org.example.yaml`을 `config/org.yaml`로 복사해 고객사 부서 이름으로 고친다(커밋 안 함). `run_all.sh`가 있으면
  `AX_ORG_CONFIG`로 넘긴다. 키는 서비스 id 또는 skill id이고 skill 줄이 먼저다. 부서 이름은 이 파일에만 있다.
- **할 일함 API** (dashboard-api 8020, 저장 `AX_HANDOFF_DB` = `.data/handoffs.db`)
  - `POST /api/dashboard/handoffs`: 에이전트가 넣는다(에이전트 토큰). 에이전트는 dashboard-api를 import하지 않고 HTTP로만 넣는다
  - `GET /api/dashboard/handoffs?unit=<부서>&status=open|done|rejected`
  - `POST /api/dashboard/handoffs/{task_id}/complete` `{"result": "...", "status": "done"|"rejected"}`: 사람 토큰 필요
- **기록**: 모든 넘김(`handed_to_human`·`no_assignee`·`inbox_unreachable`)과 사람의 완료를 공통 로그 `task_status`에 남긴다.
- 계약: `shared/contracts/src/ax_contracts/schemas/handoff.py` (`HandoffTask`, `HandoffCreate`, `HandedToHumanOutput`),
  이벤트 `handoff.completed`(`HandoffCompletedData`).

넘김을 받은 쪽(맡긴 에이전트)은 `handed_to_human`을 실패로 보지 않고 "사람이 하는 중"으로 둔다. 예: 고객지원이 재무에 정산 보류를 맡겼는데
재무 서비스를 사지 않은 고객사라면

```python
from ax_agent_template import call_agent, subscribe
from ax_contracts.schemas import HANDED_TO_HUMAN, EventType

result = await call_agent("finance", req)               # skill: settlement_hold
if result.outcome == HANDED_TO_HUMAN:
    task_id = result.output["task_id"]                  # 재무/정산 부서 할 일함에 들어감
    save_waiting(req.meta.context_id, task_id)          # 이 서비스의 저장소에 "사람 처리 대기"로 남긴다
    return reply("재무 담당 부서에 확인을 요청했습니다")   # 고객·화면에는 기다리는 중으로 보인다
elif result.status == "completed":
    ...                                                 # 재무 에이전트가 바로 처리한 경우 (서비스를 산 고객사)

async def on_handoff_completed(event):                  # 사람이 할 일함에서 끝내면
    waiting = find_waiting(event.data["task_id"])       # 같은 event_id는 두 번 처리하지 않는다
    if waiting:
        resume(waiting, status=event.data["status"], note=event.untrusted.get("result"))  # 결과는 데이터로만

subscribe([EventType.HANDOFF_COMPLETED], on_handoff_completed)
```

## 알아 둘 것

- 실제 모드에서 아직 출처가 없는 화면 데이터(지식 조각·후기)는 지어내지 않고 빈 목록이다.
- 캣북스에는 배포 기록이 없어서, 인프라 진단은 원인 배포를 특정하지 못하면 롤백하지 않고 사람에게 넘긴다(이관 큐).
- 같은 부서에서 나뉜 서비스는 메모리를 따로 가진다. 예: `inquiry`의 문의가 `claims`에 안 보인다. 공유 저장소(DB)를 붙여야 이어진다.
- `planning`·`dev`는 A2A로 잇는다(`PLANNING_ROLE`, `planning_agent/dev_handoff.py`). `planning`이 확정한 기획서·작업을 `start_dev`로 넘기면 `dev`가 PR·검수·자동 머지를 하고 `receive_dev_result`로 작업·PR·요청 상태를 돌려준다. 역할 없이(합친 모드) 띄우면 예전처럼 한 프로세스에서 다 한다.
- `infra/compose.server.yml`(서버 PC·게이트웨이)은 아직 옛 이름이다. Docker 빌드는 아직 확인하지 않았다.
- 원본 ax-backend는 계속 바뀐다. 이 복사본과 어느 쪽을 기준으로 할지 정해야 한다.
