# backend

AXcellent AI 백엔드. ax-company-hq/ax-backend(dev `04f0e28`, 10/28)를 복사해 와서 **서비스 14개 단위**로 나눴다.
부서가 아니라 서비스 단위로 팔기 때문에(고객사가 필요한 서비스만 고름) 에이전트도 서비스마다 하나씩 띄운다.

## 폴더

| 폴더 | 내용 |
| --- | --- |
| `shared/` | 공통: `contracts`(계약), `agent-template`(에이전트 틀·서비스 대응표), `safety`(안전 관문), `memory`(회사 기억), `dashboard-api`(화면 중계), `secret-store`(비밀값 암호화 저장소, 스위치 `AX_SECRET_STORE=env|db`, [README](shared/secret-store/README.md)). 고객사 판매 서버(shop-api)는 캣북스 레포 [`Catbooks-demo/catbooks-demo`](https://github.com/Catbooks-demo/catbooks-demo)로 옮겼다 |
| `domains/` | 업무 로직. 원래 부서 패키지 그대로(support·hr·marketing·finance·data·planning·infra-dejafix). 직접 띄우지 않고 서비스가 가져다 쓴다 |
| `services/` | 서비스 에이전트 14개. 각자 카드(`agent-card.json`)·포트·실행 설정을 가진다 |
| `infra/` | docker compose (`infra/compose.yml`이 `shared/*`·`services/*` compose를 모은다) |
| `config/` | 회사 설정 폴더 `tenants/<회사>/`(카탈로그·커넥터·조직도, "새 회사를 붙일 때") |
| `scripts/` | `split_services.py`(서비스 폴더 생성), `run_all.sh`(로컬 전체 실행), `tenant_env.py`(회사 설정 읽기) |
| `docs/` | ax-backend 설계 문서 복사본 |

## 서비스 14개

| 서비스 | 화면 | 포트 | 업무 로직 | skill |
| --- | --- | --- | --- | --- |
| `orchestrator` | 총괄·사건 | 8000 | services/orchestrator | 1 |
| `inquiry` | 고객 문의 | 8102 | domains/support | 17 |
| `claims` | 클레임·보상 | 8112 | domains/support | 14 |
| `reviews` | 리뷰·지식 | 8122 | domains/support | 2 |
| `coupon` | 공지·쿠폰 | 8103 | domains/marketing | 5 |
| `finance` | 재무 관리 | 8105 | domains/finance | 4 |
| `data` | 데이터 분석 | 8106 | domains/data | 4 |
| `handover` | 인수인계 (화면에서 접음: 띄우지 않고 켠 서비스에서도 뺌) | 8104 | domains/hr | 6 |
| `decisions` | 결정 기록 | 8114 | domains/hr | 5 |
| `org` | 조직 변동 | 8124 | domains/hr | 5 |
| `planning` | 요청·기획서 | 8101 | domains/planning | 2 |
| `dev` | 개발·배포 | 8111 | domains/planning | 6 |
| `incident` | 장애 대응 | 8107 | domains/infra-dejafix | 3 |
| `hosts` | PC·서버 연결 | 8117 | 없음 | 0 (`services/hosts/README.md`) |

대응표는 `shared/agent-template/src/ax_agent_template/services.py` 한 곳에 있다. 서비스 id는 화면 `frontend/src/demo/services.ts`와 같다(인수인계 `handover`는 화면에서 접어 `services.ts`에 없다).

## 나눈 방식

- 서비스는 부서 패키지의 앱을 그대로 띄우고, 카드에는 자기 skill만 올린다. 카드에 없는 skill은 템플릿 ACL이 막는다.
- 다른 에이전트는 예전처럼 부서 이름(`support`)으로 불러도 된다. `call_agent`가 skill을 보고 맡은 서비스(`claims`)로 보낸다.
- 받는 쪽 허용 목록에는 부서 이름이 적혀 있어서, 서비스가 보낸 요청은 부서 이름으로도 인정한다(`claims` → `support`).
- 총괄은 부서 단위로 일을 나눈다. 카드를 모을 때 같은 부서의 서비스 카드를 합쳐 부서 카드 하나로 본다.

## 로컬 실행 (실제 모드, 기본)

실제 LLM·실제 에이전트 토큰·이벤트 버스·파일 DB로 돈다. 로컬 실행은 이 실제 모드 하나다. 고객사는 캣북스(쇼핑몰 `shop-api`)이고,
AXcellent 에이전트들이 캣북스의 실제 주문·결제 데이터로 A2A 작업을 한다.

```bash
cd backend
uv sync --all-packages
cp .env.example .env            # LLM_MODEL·LLM_BASE_URL·LLM_API_KEY(·EMBED_MODEL)를 채운다. 커밋하지 않는다. 이미지를 읽는 모델이면 영수증 사진 대조도 켜진다
bash scripts/run_all.sh         # 17개 서버(dejafix-proxy 포함). 판매 서버(shop-api)는 띄우지 않는다(아래 "캣북스 판매 서버"). 로그 backend/.logs/, 데이터 backend/.data/
bash scripts/run_web.sh         # 다른 터미널: 화면(frontend 3090)을 실제 모드 토큰으로 켠다
bash scripts/run_all.sh stop
```

- **임베딩 설정** (memory 카드 검색, data 소재↔상품 연결): 모델은 `<부서>_EMBED_MODEL` → `EMBED_MODEL`.
  주소·키는 `<부서>_EMBED_BASE_URL`·`<부서>_EMBED_API_KEY` → `EMBED_BASE_URL`·`EMBED_API_KEY` → 없으면 LLM 주소·키
  (`<부서>_LLM_*` → `LLM_*`, 예전 동작). 문장 생성은 늘 LLM 주소로 가고, 임베딩 주소를 넣으면 임베딩 요청만 그리로 간다.
  임베딩 주소가 LLM 주소와 다른데 임베딩 키가 없으면 LLM 키를 그 주소로 보내지 않고 임베딩이 설정 오류로
  실패한다(경고 로그에 빠진 이름만 남긴다). 주소가 같으면 키를 비워도 LLM 키를 쓴다. 레포 맨 위 `.env`에서도
  `EMBED_MODEL`·`EMBED_BASE_URL`·`EMBED_API_KEY`를 읽는다(`backend/.env`에 없을 때만)

- **일부 서버만 띄우기** (`--only`, 없으면 전부): 메모리가 모자라거나 한 부서만 볼 때 쓴다.
  ```bash
  bash scripts/run_all.sh --only data,finance            # 이름은 run_all.sh의 APPS 목록 이름
  bash scripts/run_all.sh --only core                    # safety, memory, dashboard-api, orchestrator
  bash scripts/run_all.sh --only data-demo               # core + data, finance, inquiry, coupon, dev (data 업무 리포트가 부르는 곳)
  bash scripts/run_all.sh --only data-demo,incident      # 묶음과 이름을 섞어도 된다
  ```
  - 시작할 때 `띄운 서버 N개: …, 안 띄움: …`을 출력한다. 모르는 이름이면 목록을 보여 주고 멈춘다.
  - 판매 서버(shop-api, 8010)는 `--only`와 상관없이 띄우지 않는다. `stop`·`reset`도 8010을 건드리지 않고, 다른 서비스는 `AX_SHOP_API_URL`(기본 `http://127.0.0.1:8010`)로 찾아간다. 예전 `SHOP_API_EXTERNAL=on`은 이제 늘 켜진 것과 같아 주지 않아도 된다(남아 있어도 상관없다).
  - 안 띄운 서비스는 `AX_ENABLED_SERVICES`에서도 자동으로 빠진다. 그래서 라우터가 그쪽으로 A2A를 보내지 않고 사람 할 일함으로 넘긴다. 이미 `AX_ENABLED_SERVICES`를 줬으면 둘의 교집합이고, 남는 서비스가 없으면 `none`(모두 끔)이다.
  - DejaFix 읽기 전용 프록시(`dejafix-proxy`, 8030)는 계정이 없어도 run_all이 같이 띄운다(연결 전에는 503 `DEJAFIX_NOT_CONNECTED`). 연결은 화면의 도구 연결(보관함 `dejafix`)에서 정하면 재시작 없이 반영되고, 인프라 AI·개발 AI는 보관함에 `dejafix`가 있으면 이 프록시 주소를 쓴다(`INFRA_DEJAFIX_URL`·`PLANNING_DEJAFIX_API_URL`이 있으면 그 값이 먼저).
  - 메모리(전용 메모리, Windows 로컬 측정): 전체 서버(예전 판매 서버·가상 주문 포함 측정)는 프로세스 36개·약 940MB, `data-demo`는 26개·약 680MB다.
- Docker가 필요하다. 이벤트 버스(Redis)를 컨테이너 `ax-redis`로 띄운다 (`REDIS_URL`을 주면 그걸 쓴다).
- 에이전트 토큰(`AX_AGENT_TOKENS`)·사람 토큰은 켤 때마다 새로 만들어 프로세스에만 넘긴다. 화면 개발 서버에 넘길 값만
  `backend/.data/web.env`에 쓴다 (커밋 안 함, 이름은 `AX_WEB_*_TOKEN`). `run_web.sh`는 이 값을 개발 서버 프록시에만 넘기고,
  화면 번들(`VITE_*_TOKEN`)에는 자리표시자만 넣는다. 프록시가 자리표시자를 진짜 토큰으로 바꿔 붙인다(`frontend/token-proxy.ts`, 서버 nginx와 같은 구조).
- 화면은 판매 서버(shop-api) 관리 읽기(총괄 사건·부서, 공지 배너, 장애 상태)를
  직접 부르지 않고 dashboard-api의 `/api/dashboard/shop/*`(GET만)를 부른다. dashboard-api가 회사 연결 주소
  (`agent_url("shop-api")`)로 **읽기 전용 열람 토큰** `SHOP_VIEWER_TOKEN`(24자 이상)을 붙여 부른다.
  캣북스 shop-api와 dashboard-api에 같은 값을 넣고, 화면 빌드에는 넣지 않는다. 열람 토큰은 위 읽기만 열리고
  쓰기·그 밖의 관리 API는 모두 403이다. 에이전트 토큰 명부와 따로 두어 다른 서버(총괄 등)로는 통하지 않는다.
  받은 기획안·장애 SNS 초안은 판매 서버를 거치지 않고 `/api/dashboard/marketing/{briefs,briefs/{id},outage-sns-drafts}`가 마케팅 skill을 A2A로 바로 읽는다.
  `.env`에 없으면 run_all.sh가 `.data/tokens.env`에 만들어 두고 다시 켤 때 그대로 쓴다.
- 판매 서버(shop-api)가 마케팅 등을 A2A로 부를 때 쓰는 에이전트 토큰 `shop-api`도 같은 명부(`AX_AGENT_TOKENS`)에 들어간다.
  이 값은 판매 서버(`shop.env`)가 쓰는 값과 같아야 한다. 운영 서버는 `.data/tokens.env`의 `AX_AGENT_TOKENS=` 줄에
  `shop-api=<판매 서버가 쓰는 토큰>`을 넣고 백엔드를 다시 켠다(run_all.sh가 저장된 값을 그대로 쓴다).
  저장된 값이 없으면 새로 만들어 판매 서버와 달라지고, 그러면 마케팅이 UNAUTHORIZED로 거부한다.
- 공통 로그(`.data/common_log.db`)·인사(`.data/hr.db`)·아카이브(`.data/archive.db`, 검색 벡터는 `archive_vectors.db`)는 파일이라 다시 켜도 남는다.
  아카이브 파일이 없으면 예전처럼 `hr.db`를 같이 읽는다. 옛 기록은 서버를 끄고 `uv run python scripts/migrate_archive_db.py`로 옮긴다(백업 후 복사, `--dry-run`으로 건수 확인, 원본은 지우지 않음).
  예전 판매 서버 파일(`.data/shop.db`)은 이제 쓰지 않지만 `reset`도 지우지 않고 그대로 둔다(되돌릴 때를 위해).
- 장애 넣기·빼기는 운영자 명령이다: `bash scripts/shop_fault.sh failure|latency|clear|status` (캣북스 레포 스크립트를 부르는 래퍼).
  `SHOP_API_URL`(판매 서버 주소, 로컬이면 `http://127.0.0.1:8010`)과 `SHOP_ADMIN_TOKEN`(캣북스 관리자 토큰)을 둘 다 주고 부른다. 하나라도 없으면 멈춘다. 테스트 기록을 지우려면 `bash scripts/run_all.sh reset`.
- 캣북스 쇼핑몰 화면(고객이 보는 곳)은 캣북스 레포의 `frontend/shop-web`이다. 같은 shop-api(8010)에 붙여 띄우면 장애 때 결제 화면에
  "결제 서버에 문제가 있어 결제하지 못했습니다"가 뜬다(띄우는 법은 캣북스 레포 README).
- 장애 대응 흐름: AI 대화방의 "캣북스에 장애 넣기" → 캣북스 결제가 실제로 실패 → 쇼핑몰 감시가 `shop.degraded`
  (캣북스 쪽 `SHOP_DEGRADED_SUSTAIN_S`, 시연 값 20초) → Redis → 총괄이 사건을 열고 7개 부서를 실제로 부른다.
- **목 스위치는 테스트 전용**: `AGENT_TEMPLATE_MOCK=1`·`<서비스>_MOCK=1`(예: `DATA_MOCK`, `ORCHESTRATOR_MOCK`, `DASHBOARD_MOCK`)과 테스트용 가짜 부서(`services/orchestrator/.../mock/`)는 CI 단위 테스트가 쓴다. 로컬 실행·데모에는 쓰지 않는다. `run_all.sh`는 `.env`에 남아 있어도 끄고, `run_all.sh mock`은 지웠다.
- dashboard-api의 목 데이터 응답(`routes.py` `mock_only`)도 테스트 전용이다. 실제 모드에서 출처가 없는 응답(지식 조각·후기)은 지어내지 않고 빈 목록이고, 화면은 "준비 중"으로 표시한다.

### 캣북스 판매 서버 (따로 띄운다)

캣북스 판매 서버(shop-api)·쇼핑몰 화면(shop-web)·가상 주문·장애 스크립트는 캣북스 레포
[`Catbooks-demo/catbooks-demo`](https://github.com/Catbooks-demo/catbooks-demo)에 있고 따로 배포한다.
AXcellent는 판매 서버를 띄우지 않고 주소(`AX_SHOP_API_URL`, 또는 시스템 관리 > 회사 연결)·연결 키·웹훅으로만 붙는다.
로컬 개발에서 판매 서버가 필요하면 캣북스 레포를 따로 클론해 띄운다(자세한 것은 그 레포 README).

```bash
git clone https://github.com/Catbooks-demo/catbooks-demo
cd catbooks-demo/backend
uv sync
uv run uvicorn ax_shop_api.main:app --port 8010   # 다른 터미널. 가상 주문은 그 레포의 scripts/shop_traffic.py
```

- AXcellent `run_all.sh`는 `AX_SHOP_API_URL`을 기본 `http://127.0.0.1:8010`으로 내보내므로 위처럼 8010에 띄우면 그대로 붙는다.
  다른 주소면 `backend/.env`에 `AX_SHOP_API_URL=<주소>`를 두거나 화면의 시스템 관리 > 회사 연결에서 정한다(회사 연결이 먼저, 재시작 없이 반영).
- 판매 서버 관리 읽기 중계(dashboard-api)를 쓰려면 `SHOP_VIEWER_TOKEN`을 양쪽에 같은 값으로 넣는다.
- 결제 장애 감지(`shop.degraded`)·가상 주문 스위치(`SHOP_TRAFFIC`)는 캣북스 쪽 설정이다.

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
  `run_all.sh`가 읽어서 모든 서버에 넘긴다. 서비스 14개만 이 목록을 따르고, 공통 서버(safety·memory·shop-api·dashboard-api)와
  주소로 부른 대상은 늘 켜져 있다. 서버는 목록과 관계없이 다 띄운다(꺼진 서비스로 가는 요청만 사람에게 간다).
- **켠 서비스 파일**: `AX_ENABLED_SERVICES_FILE`(run_all.sh 기본값 `backend/.data/enabled_services.json`)이 가리키는 파일이 있으면
  환경변수보다 먼저 쓴다. 형식은 `{"enabled": ["data", "finance"], "updated_at": "...", "updated_by": "..."}`이고, 빈 목록은 "모두 끔"이다.
  라우터가 부를 때마다 바뀐 시각을 확인하므로 파일을 바꾸면 **재시작 없이** 반영된다. data `activity_report`도 같은 함수로 읽는다.
  파일이 없거나 깨졌으면(경고 로그) 환경변수 `AX_ENABLED_SERVICES`를 쓴다(전부 켬으로 바꾸지 않는다).
  `--only`로 띄울 때 이 파일이 있으면 파일이 먼저 쓰이므로, 안 띄운 서비스가 파일에 있으면 고치거나 지운다(run_all.sh가 경고한다).
- **조직도**: 회사 설정 폴더의 `config/tenants/<회사>/org.yaml`(캣북스는 커밋함, "새 회사를 붙일 때")을 `run_all.sh`가
  `AX_ORG_CONFIG`로 넘긴다. 내 PC에서만 바꾸려면 `config/org.example.yaml`을 `config/org.yaml`로 복사해 고친다(커밋 안 함, 그쪽이 먼저). 키는 서비스 id 또는 skill id이고 skill 줄이 먼저다. 부서 이름은 이 파일에만 있다.
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

result = await call_agent("finance", req)               # skill: closing_report
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

## 새 회사를 붙일 때

회사 한 곳 = **카탈로그 + 커넥터 + 조직도**다. 셋을 `config/tenants/<회사>/` 폴더 하나로 묶고,
`tenant.toml` 한 장이 어느 카탈로그·커넥터·조직도를 쓰는지 가리킨다. 비밀값(토큰·주소)은 이 폴더에 두지 않고 `backend/.env`에만 둔다.

```
config/tenants/
  catbooks/            # 캣북스 (기본, 커밋함)
    tenant.toml
    org.yaml
tests/tenants/
  coldchain/           # 테스트 전용 가짜 회사(냉장 물류), 아래 예시
    tenant.toml
    org.yaml
```

| 무엇 | 어디 | 정하는 것 |
| --- | --- | --- |
| `tenant.toml` | 회사 폴더 | `name`(한국어 이름), `catalog`, `connector`, `org` |
| 카탈로그 | `catalog` 값. 이름이면 `domains/data/src/data_agent/catalogs/<이름>.toml`, `.toml`이면 회사 폴더 기준 경로 | data가 무엇을 볼지: 지표·단위·실패 코드 (`domains/data/README.md` "지표 카탈로그") |
| 커넥터 | `connector` 값. 카탈로그의 `connector`와 같아야 한다 | data가 어디서 읽을지: 이름 → 구현은 data `clients/shop_client.py`의 `CONNECTORS` (`domains/data/README.md` "커넥터") |
| 조직도 | `org` 값(회사 폴더 기준, 기본 `org.yaml`) | 꺼진 서비스로 가는 일을 맡을 부서·담당자 ("업무 요청 라우터"). **서비스 14개를 모두 적는다**. 빠진 서비스로 가는 일은 `no_assignee`로 실패한다 |
| 회사 소개 | `tenant.toml` `[profile] about` (덮어쓰기는 `[profile.override]`) | data 도메인 프로필(업종·상품군·씨앗 키워드)의 재료. **회사 소개는 AI가 사실로 읽는다. 분위기 문장·별명은 쓰지 말고, 카탈로그와 맞게** (소개에 없는 상품군이 카탈로그에 있으면 프로필에 경고) |
| 브랜드 | `tenant.toml` `[brand]` (모두 선택) | 영상 주인공·배경·화풍(`character_en/ko`·`setting_en/ko`·`style_en`, 걸리면 기본값), 홍보 방향(`promo_style`·`brand_exposure`). 11/24 더함: SNS 브랜드 태그 `hashtags`(없으면 `#` + 회사 이름, 공백 뺌), 캡션 시연 안내 `demo_notice`(가상 회사만, 없으면 넣지 않음), 마케팅 미디어 추천 분류 `categories`(없으면 분류는 늘 미정), data 홍보 영상 아이디어 소재 `promo_words`·고정 틀 장면 `[brand.promo_scenes]`(형식 → 장면)·`promo_scene`(없으면 `[brand]` 배경·주인공과 카탈로그 품목 이름으로 만든 중립 장면). 코드에 회사 이름·업종 낱말을 두지 않는다 (캣북스 값은 `config/tenants/catbooks/tenant.toml`) |

### 예시: 냉장 물류 (테스트 전용 가짜 회사)

`tests/tenants/coldchain/tenant.toml`

```toml
name = "냉장물류 테스트"
catalog = "../../../domains/data/tests/catalogs/coldchain.toml"   # 회사 폴더 기준 경로
connector = "memory"                                             # 카탈로그 connector와 같다 (테스트 메모리 커넥터)
org = "org.yaml"
```

`tests/tenants/coldchain/org.yaml` (일부)

```yaml
assignments:
  incident: {unit: 관제실, assignee: 야간 당직}
  inquiry: {unit: 거래처지원}
  finance: {unit: 경영지원}
  # … 서비스 14개 모두
```

카탈로그 `coldchain.toml`은 지표 2개(냉장 유통 이탈 건수·출고 지연 건수), 단위 "출고", 고객 "거래처"다. 이 회사로 돌리면 data 문장에 캣북스 단어(결제·주문·판매자)가 나오지 않는다(`domains/data/tests/test_catalog_wording.py`).

### 순서

1. `config/tenants/<회사>/` 폴더를 만들고 `tenant.toml`·`org.yaml`을 둔다 (위 예시를 복사해 고친다)
2. 카탈로그를 만든다. 실제 회사는 `domains/data/src/data_agent/catalogs/<회사>.toml`에 두고 `catalog = "<회사>"`
3. 그 회사 시스템을 읽는 커넥터가 없으면 data에 만들고 `CONNECTORS`에 이름을 올린다 (메서드 3개, 못 읽으면 `ConnectorUnavailable`, 0으로 채우지 않음)
4. `uv run pytest tests/test_tenant_config.py`: 회사 폴더 확인 (카탈로그 있음, 커넥터 일치, 조직도에 서비스 14개). 새 회사 폴더는 이 테스트의 목록에 더한다
5. `AX_TENANT=<회사> bash scripts/run_all.sh`

### run_all.sh가 정하는 것 (`scripts/tenant_env.py`)

`AX_TENANT`의 폴더를 읽어 아래 값을 정한다. **이미 준 값이 먼저다**(지금 쓰는 사람이 깨지지 않게).
캣북스 기본값은 없다. `AX_TENANT`가 없으면 아무 회사 설정도 내보내지 않고, 서비스들이 시스템 관리 › 회사 연결에서 고른 회사(연결 파일 `tenant`)를 재시작 없이 따른다(`ax_agent_template.tenant`). 연결 전에는 어느 회사도 아니고 판매 서버도 "연결 안 됨"(사이트 변환기 503 `NOT_CONFIGURED`)이다.

| 환경변수 | 순서 |
| --- | --- |
| `AX_ORG_CONFIG` | 환경변수(`.env` 포함) → `config/org.yaml`(각자 덮어쓰기, 커밋 안 함) → 회사 폴더 `org` |
| `DATA_TENANT_CATALOG` | 환경변수(`.env` 포함) → 회사 폴더 `catalog` |

- 회사 폴더가 없거나 틀리면(카탈로그 없음, 커넥터가 카탈로그와 다름) 서버를 켜지 않고 이유를 알려 준다.
- 조직도를 찾지 못하면 켜면서 분명히 알린다: `조직도 없음: 꺼진 서비스로 가는 일은 no_assignee로 실패합니다`

## 회사 폴더 (여러 회사 1-4·1-6)

회사마다 다른 공통층 파일은 `.data/companies/<회사 ID>/` 아래에 둔다. 지금은 캣북스 하나(`catbooks`).

| 무엇 | 예전 자리 | 회사 폴더 | 경로 환경변수(정했으면 그 경로 그대로) |
| --- | --- | --- | --- |
| 회사 연결 + 기록 | `.data/company_connection.json` | `companies/catbooks/company_connection.json` | 없음. `AX_COMPANY_CONNECTION_FILE`은 설치 기준 자리(계정·연결 키 옆)로 남는다 |
| 도구 연결 + 기록 | `.data/tool_connections.json` | `companies/catbooks/tool_connections.json` | `AX_TOOL_CONNECTIONS_FILE` |
| 켠 서비스 | `.data/enabled_services.json` | `companies/catbooks/enabled_services.json` | `AX_ENABLED_SERVICES_FILE` |
| 변환기 폴더 | `.data/site_adapter/` | `companies/catbooks/site_adapter/` | `AX_SITE_ADAPTER_DIR` |
| 비용 정책 + 기록 | `SAFETY_STATE_DB` 옆 `cost_policy.json` | `companies/catbooks/cost_policy.json` | `SAFETY_COST_POLICY_FILE` |
| 형상관리 연결 (dev, 1-6) | `.data/vcs_connections.json` | `companies/catbooks/vcs_connections.json` | `PLANNING_VCS_STORE` |
| 기획 대화·노션 연결 (planning, 1-6) | `PLANNING_STATE_DIR/planning_chat.db`·`notion_token.json` | `companies/catbooks/` 같은 이름 | 없음. `PLANNING_STATE_DIR`가 없으면 메모리만. 업무 흐름 상태 파일은 `PLANNING_STATE_DIR` 그대로 |
| 재무 기록·내보내기·대화 (finance, 1-6) | `.data/finance_state.json`·`finance_exports/`·`finance_chat.db` | `companies/catbooks/` 같은 이름 | `FINANCE_STATE_FILE`·`FINANCE_EXPORT_DIR`·`FINANCE_CHAT_DB`(대화는 예전처럼 `FINANCE_STATE_FILE` 옆도) |
| 인프라 배포 기록 (infra, 1-6) | 패키지 안 `data/deployments.json` | `companies/catbooks/infra_deployments.json`(있을 때만) | `INFRA_DEPLOYMENTS_FILE` |
| 마케팅 미디어 폴더 (1-6) | 임시 폴더 `marketing_media`(또는 `<회사 폴더의 부모>/marketing_media`) | `companies/catbooks/marketing_media/` | `MARKETING_MEDIA_DIR`(정했으면 그 경로. SNS 자산 해석은 이 값을 정했을 때만, 예전 그대로) |
| 마케팅 비용 한도·영상 공급자 설정 (1-6) | `<MARKETING_MEDIA_DIR>/cost_limits.json`·`video_providers.json`(+ `.history.jsonl`) | `companies/catbooks/` 같은 이름 | `MARKETING_COST_LIMITS_FILE`·`MARKETING_VIDEO_PROVIDERS_FILE` |

회사 칸(1-6): `infra_chat.db`는 줄마다 `tenant_id`(옛 줄은 켤 때 `catbooks`로 채움). 재무 메모리 주문 모음은
회사 ID마다 따로(`finance_agent.books.order_store`).

마케팅 DB 6개(`marketing_briefs`·`media_ledger`·`sns_pending`·`sns_drafts`·`marketing_chat`·`marketing_creative`)는 파일은 그대로
두고 표마다 `tenant_id` 칸이 붙는다(1-6). 옛 줄은 `catbooks`로 채워지고(멱등), 저장소는 자기 회사 줄만 읽는다.
run_all.sh가 칸을 붙이기 전 한 번 `.data/backup-before-marketing-company-<시각>.tgz`(0600)를 만든다.

- 켜는 값은 `AX_COMPANIES_DIR`(run_all.sh 기본 `.data/companies`). 안 정하면 모두 예전 자리다
- 옮기기(`ax_agent_template.company_files`): 예전 자리에 있고 회사 폴더에 없으면 처음 읽을 때 한 번 옮긴다.
  잠금(`.migrate.lock`) → 임시 이름 복사 → `os.replace`. 예전 파일은 지우지 않고 `<이름>.moved`로 둔다.
  실패하면 예전 자리를 계속 읽는다. 비용 정책은 안전 서버가 켤 때 옮긴다(판정 중에는 잠금을 기다리지 않는다)
- 백업: run_all.sh가 옮기기 전에 `.data/backup-before-companies-<시각>-<확인값>.tgz`(0600)를 만든다.
  옮길 파일이 없거나 같은 내용이면 건너뛰고 최근 5개만 남긴다
- **되돌리기**: `.env`에 `AX_COMPANY_FOLDERS=off`를 넣고 다시 켠다. 예전 자리가 비어 있으면 각 서버가
  회사 폴더 값으로 예전 자리를 되살린다(옮긴 뒤 화면에서 바꾼 값까지 살아 있다). 회사 폴더 파일은 지우지 않는다.
  비용 정책을 되살리지 못하면 경고 로그 한 번 + 현황 API(`GET /api/safety/cost-policy`)의 `moved_unrestored: true`

## 알아 둘 것

- 실제 모드에서 아직 출처가 없는 화면 데이터(지식 조각·후기)는 지어내지 않고 빈 목록이다.
- 캣북스에는 배포 기록이 없어서, 인프라 진단은 원인 배포를 특정하지 못하면 롤백하지 않고 사람에게 넘긴다(이관 큐).
- 같은 부서에서 나뉜 서비스는 메모리를 따로 가진다. 예: `inquiry`의 문의가 `claims`에 안 보인다. 공유 저장소(DB)를 붙여야 이어진다.
- `planning`·`dev`는 A2A로 잇는다(`PLANNING_ROLE`, `planning_agent/dev_handoff.py`). `planning`이 확정한 기획서·작업을 `start_dev`로 넘기면 `dev`가 PR·검수·자동 머지를 하고 `receive_dev_result`로 작업·PR·요청 상태를 돌려준다. 역할 없이(합친 모드) 띄우면 예전처럼 한 프로세스에서 다 한다.
- `infra/compose.server.yml`(서버 PC·게이트웨이)은 아직 옛 이름이다. Docker 빌드는 아직 확인하지 않았다.
- 원본 ax-backend는 계속 바뀐다. 이 복사본과 어느 쪽을 기준으로 할지 정해야 한다.
