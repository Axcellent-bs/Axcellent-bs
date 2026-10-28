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
| `planning` | 요청·기획서 | 8101 | domains/planning | 1 |
| `dev` | 개발·배포 | 8111 | domains/planning | 5 |
| `incident` | 장애 대응 | 8107 | domains/infra-dejafix | 3 |
| `hosts` | PC·서버 연결 | 8117 | 없음 | 0 (`services/hosts/README.md`) |

대응표는 `shared/agent-template/src/ax_agent_template/services.py` 한 곳에 있다. 서비스 id는 화면 `frontend/src/demo/services.ts`와 같다.

## 나눈 방식

- 서비스는 부서 패키지의 앱을 그대로 띄우고, 카드에는 자기 skill만 올린다. 카드에 없는 skill은 템플릿 ACL이 막는다.
- 다른 에이전트는 예전처럼 부서 이름(`support`)으로 불러도 된다. `call_agent`가 skill을 보고 맡은 서비스(`claims`)로 보낸다.
- 받는 쪽 허용 목록에는 부서 이름이 적혀 있어서, 서비스가 보낸 요청은 부서 이름으로도 인정한다(`claims` → `support`).
- 총괄은 부서 단위로 일을 나눈다. 카드를 모을 때 같은 부서의 서비스 카드를 합쳐 부서 카드 하나로 본다.

## 로컬 실행 (목 모드)

```bash
cd backend
uv sync --all-packages
bash scripts/run_all.sh        # 16개(공통 4 + 총괄 + 서비스 11) 띄우고 /health 표를 찍는다. 로그 backend/.logs/
bash scripts/run_all.sh stop
```

화면(`frontend/`)은 `.env`에 `VITE_MOCK=off`로 두면 위 서버들을 부른다(`.env.example`).

## 알아 둘 것

- 목 모드(`AGENT_TEMPLATE_MOCK=1`)만 된다. 데이터는 시드값이고 프로세스 메모리에만 있어서 재시작하면 처음으로 돌아간다.
- 같은 부서에서 나뉜 서비스는 메모리를 따로 가진다. 예: `planning`에서 받은 요청이 `dev`에 안 보이고, `inquiry`의 문의가 `claims`에 안 보인다. 공유 저장소(DB)를 붙여야 이어진다.
- `infra/compose.server.yml`(서버 PC·게이트웨이)은 아직 옛 이름이다. Docker 빌드는 아직 확인하지 않았다.
- 원본 ax-backend는 계속 바뀐다. 이 복사본과 어느 쪽을 기준으로 할지 정해야 한다.
