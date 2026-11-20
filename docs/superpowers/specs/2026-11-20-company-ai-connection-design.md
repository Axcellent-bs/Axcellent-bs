# 회사 AI 연결(공통 AI 도구 연결) — 설계

- 날짜: 2026-11-20 (예진 결정·팀 의견 반영)
- 범위: 설계만. 구현은 회사 공간 나누기(`2026-11-20-multi-company-design.md`) **1단계 뒤**
- 화면 미리보기: https://claude.ai/artifact/VpXPjhGTQxMSeo2DKsKEAR

## 1. 목표

| 화면 | 누가 | 하는 일 |
|---|---|---|
| 시스템 관리 › 회사 연결 › "공통 AI 도구 연결" | 관리자만 | 회사 AI 모델(모델·API 키·월 예산 토큰), 개발 AI 코딩 에이전트(회사 모델 / 따로 설정), 마케팅 영상 생성(키 있어야 켜짐). 그 밖의 AI 직원은 모두 회사 모델(직원별 모델 선택은 원하는 고객사가 생기면 추가) |
| 도구 연결 › AI 연결 | 모두(보기만) | 회사 AI 상태, 이번 달 사용량(%), AI 직원별 사용량 |
| 도구 연결 › 내 연결 | 본인 | 개인 AI 도구(Claude Code 등)로 AXcellent를 쓰는 개인 토큰. 요금은 개인 도구 계정 |

원칙
- AI 키·모델은 **회사당 하나**. 키 넣는 곳도 **이 화면 한 곳**(마케팅 #1450 영상 공급자 카드와 겹치지 않게, 재헌 님 의견)
- 예산은 **토큰 수**로 저장하고 화면에는 **사용 %**만 계산해 보인다. "공급자 월 한도 %"는 우리가 알 수 없으므로 쓰지 않는다. 마케팅 `MARKETING_QUOTA_*`(#1490, 사용량 예산 + 남은 %)와 같은 개념
- 키 값은 저장 뒤 끝 4자리만 돌려준다. 화면·로그·A2A·이력에 원문이 가지 않는다
- 모델 이름을 코드에 적지 않는다(규칙). 판별용 목록은 설정 파일

## 2. 지금 상태 (조사)

### 2.1 LLM 호출 경로
- `shared/agent-template/src/ax_agent_template/llm.py`의 `LLMSettings.from_env(dept)`가 `<부서>_LLM_MODEL/BASE_URL/API_KEY` → 공통 `LLM_*` 순으로 **프로세스 환경변수**에서 읽는다. `LLMClient`가 OpenAI 호환 `/chat/completions`를 부른다
- 테스트를 뺀 소스 42곳이 `LLMClient(...)`를 직접 만든다(준하 님 11/20 확인: support 4, planning 4, finance·data·archive 각 2, safety·memory·workflow-core·orchestrator·hr·dev·marketing·infra 각 1 등)(finance·planning·infra `chat/turn.py`, data `data_chat.py`, marketing `chat.py`, hr·archive `services.py` 등). 즉 설정이 **서버 띄울 때 고정**이고 회사별로 바뀌지 않는다
- dev는 `dev_agent/llm_env.py`·`llm_key.py`로 따로 덮어쓴다

### 2.2 dev BYOK (`dev_agent/llm_key.py`)
- 고객사 키를 프로세스 메모리 + **평문 JSON 파일**(`DEV_LLM_KEY_STORE`, `backend/.data/`)에 둔다. 화면엔 넣었는지와 끝 4자리만
- 키가 있으면 `apply(base)`로 `LLMSettings`를 덮어쓰고, 월 예산 `State.monthly_budget_tokens`만 건다. 키가 없으면 서버 기본 키 + **서버 AI 체험 한도**(`TRIAL_EXHAUSTED`), 예산 초과는 `BUDGET_EXHAUSTED`(`service.py`)
- 회사 구분이 없다(설치당 하나)

### 2.3 마케팅 영상·이미지 키 (#1450·#1468)
- 영상·이미지 공급자(gemini·seedance·custom 등) 키는 `ax-secret-store`(`shared/secret-store`, `get_secret`)에서 읽는다(`media/config.py` 등)
- 키 넣기·교체는 `secret_requests.py`: 화면이 값을 암호화해 대기 요청으로 두고, 스킬은 요청 번호만 받아 안전 관문을 지난다. `add_secret`은 바로, `change_secret`(교체·삭제)은 **다른 사람 승인**. 이번 설계의 승인 흐름은 이 틀을 그대로 쓴다
- 비용 한도: `media/quota_store.py`·`cost_limits.py`, 월 수량 `MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY` / `IMAGE_COUNT` / `AUDIO_COUNT`(#1490 진행 중)

### 2.4 부서별 한도
- data: `data_agent/llm_budget.py`(일·월 토큰 상한, 예비 비율 `reserve_ratio`), `llm_halt.py`
- dev: 위 월 예산·체험 한도
- 나머지 부서는 한도 없음

### 2.5 사용량 기록
- `ax_agent_template/llm_usage.py`: LLM 호출마다 `LlmCallRecord`(계약 `ax_contracts.data_access.llm_usage`)를 SQLite(`AX_LLM_USAGE_DB`)에 쓴다. 칸: `tenant_id`, `source`(에이전트), `department`, `model`, `input_tokens`, `output_tokens`, `success`, `occurred_at`
- **회사별·AI 직원별 집계에 필요한 칸은 이미 있다.** 다만 `tenant_id`는 지금 기본값만 들어가고(1단계에서 채워짐), dashboard-api에 사용량 화면 API는 없다
- 경로가 비어 있으면 파일에 안 쓴다 → 실제 모드 설치에서 반드시 켜야 한다

### 2.6 개인 연결 토큰
- 사람 세션 토큰: `human_auth.py`(2026-11-19 사람별 토큰 설계). 화면 로그인용
- MCP: dev 작업 대기열 전용 토큰 `DEV_WORK_TOKENS="이름:토큰"`(`work_auth.py`). **환경변수에 운영자가 적는 방식**이라 각자 화면에서 만들 수 없다. 작업 대기열만 쓸 수 있다(사람 결정 권한 없음 — 이 성질은 유지)

## 3. 저장 (키 저장소 + 회사 ID)

| 무엇 | 어디 | 비밀? |
|---|---|---|
| 회사 AI 키 | `ax-secret-store`, `tenant=<회사 ID>`, 이름 `LLM_API_KEY` | 예(암호화) |
| 코딩 에이전트 따로 키 | 같은 곳, 이름 `DEV_CODER_LLM_API_KEY` | 예 |
| 영상 공급자 키 | 같은 곳, 지금 이름 그대로(`MARKETING_*` 영상 키) | 예 |
| 모델·주소·공급자·월 예산·코딩 방식 | 회사 설정 `company_ai.json`(회사 폴더, `tenant.py`의 회사 폴더 아래) | 아니오 |

`company_ai.json` 모양(예)
```json
{
  "model": "<모델 이름>",
  "provider": "auto | openai_compatible | ...",
  "base_url": "",
  "monthly_budget_tokens": 20000000,
  "coder": {"mode": "company | separate", "model": "", "base_url": ""},
  "video": {"provider": "", "enabled": false},
  "updated_at": "...", "updated_by": "..."
}
```
- 키 저장소 db 모드(`AX_SECRET_STORE=db`)가 전제. 아니면 화면에 "키 저장소 꺼짐"으로 저장 버튼을 막는다
- 바꿀 때마다 `company_ai.history.jsonl`(누가·언제·무엇을, 키는 끝 4자리)
- 직원별 모델 선택(`overrides`)은 **이번 범위 밖**(11/20 결정, 태윤 님 안). 지금은 회사당 키 하나·모델 하나이고, 원하는 고객사가 생기면 그때 `overrides`(모델만)를 추가한다. 예외는 코딩 에이전트·영상 생성뿐

마스터 키 백업
- 키 저장소 마스터 키(.env)는 키 담당자 두 명(재헌·예진, 11/20 합의)이 **각자 전체 사본**을 서버 밖에 보관한다. 한 명이 없어도 복구된다(반씩 나누는 방식은 쓰지 않는다). 각자 `.env` 밖 암호화된 곳에 보관(재헌 님 11/20)

마스터 키 교체 절차 (키 저장소 `ax-secret-store`가 이미 지원: 키 버전 `AX_SECRET_MASTER_KEY_ID`, 옛 키 `AX_SECRET_MASTER_KEYS_OLD`, `migrate rewrap`)
1. 담당자 한 명이 새 마스터 키를 만들고(`generate_master_key`), 두 담당자가 **같은 자리에서** 각자 보관소에 전체 사본을 넣는다. 둘 다 넣은 것을 서로 확인하기 전에는 다음 단계로 가지 않는다
2. 서버 `.env`: `AX_SECRET_MASTER_KEY`=새 키, `AX_SECRET_MASTER_KEY_ID`=새 버전(예: k2), `AX_SECRET_MASTER_KEYS_OLD`=`k1=<옛 키>` → 재시작(옛 키로 저장된 값도 읽힘)
3. `uv run python -m ax_secret_store.migrate rewrap` → 모든 값을 새 키로 다시 암호화(기록에 `rewrap` 남음)
4. 화면에서 키 하나를 열람 없이 "연결 확인"으로 읽혀지는지 확인한 뒤 `AX_SECRET_MASTER_KEYS_OLD`를 지우고 재시작
5. 두 담당자가 옛 키 사본을 보관소에서 지운다. 교체 이유·시각·담당자는 슬랙 서버 스레드에 남긴다
- 언제: 담당자가 바뀔 때, 사본 유출이 의심될 때, 그 밖에는 정기 교체를 따로 정한다(열린 질문)

## 4. 요청마다 회사 모델·키 꺼내 쓰기 (공통 LLM 호출부)

새 함수 `ax_agent_template.llm.settings_for(dept, tenant_id) -> LLMSettings`
1. `tenant_id`는 1단계에서 A2A 메타에 실리는 회사 ID(`meta.tenant_id`)
2. 회사 설정을 읽는다(바뀐 시각 확인, 재시작 없이 반영 — `company_connection.py`와 같은 방식)
3. 키: `get_secret("LLM_API_KEY", tenant=tenant_id)`
4. 모델: 회사 `model` 하나(코딩 에이전트가 "따로 설정"이면 그 모델). 직원별 `overrides`는 나중에 추가할 자리만 남긴다
5. 주소: 공급자 판별(5장) 결과
6. 회사 키가 없으면 → 체험 키 폴백(아래)

`LLMClient`는 생성 때 고정 설정 대신 **부를 때** `settings_for`를 쓰도록 바꾼다. 도메인 코드는 `LLMClient(dept)` 그대로 두고, 요청 문맥(contextvar)의 회사 ID를 읽는다. 기존 `from_env`는 회사 설정이 없을 때(로컬 개발·테스트 목) 그대로 쓴다.

체험 키 폴백·체험 한도
- 회사 키가 없을 때만, 설치 운영자가 체험 키(`LLM_TRIAL_API_KEY`, `.env`)를 넣어 둔 경우에만 쓴다
- 회사별 체험 한도 `LLM_TRIAL_TOKENS`(토큰, 회사마다 한 번). 사용량은 그 회사 `tenant_id`로 기록 → 다른 회사로 비용이 섞이지 않는다
- 다 쓰면 `TRIAL_EXHAUSTED`, 화면 "회사 AI 키를 넣어 주세요". 다른 회사 키·설치 기본 키로 넘어가지 않는다(회사 공간 설계 7장과 일치. 체험 키는 "설치 기본 키"가 아니라 한도가 있는 별도 키로 본다)
- dev의 지금 체험 한도는 이 공통 체험 한도로 합친다

임베딩
- `LLMSettings`의 `embed_model`·`embed_url`·`embed_key`(아카이브 과거 사례 검색 등 `embed()` 경로)도 **회사 키·모델을 따르고, 회사 설정이 없으면 `from_env`로 폴백**한다(11/20 예진 결정, 준하 님 제안)

회사 문맥이 없는 호출
- 서버 시작 때 만드는 이벤트 소비 작업(`events.py`)·스케줄 작업은 contextvar 회사 ID가 비어 있다. 이 경로는 이벤트·작업에 실린 회사 ID를 넘기고, 없으면 `from_env` 폴백 또는 멈춤을 경로별로 명시한다(2번 PR에서 42곳 기준 목록)

월 예산 검사
- 부르기 전에 이번 달 회사 사용 토큰(`llm_usage` 합)과 `monthly_budget_tokens`를 비교. 넘으면 `BUDGET_EXHAUSTED`로 멈춘다
- 80%·100%에 관리자 알림 한 번씩
- data의 `llm_budget.py`(일 상한·예비 비율)는 부서 안쪽 한도로 남기고 회사 예산 아래에서 돈다

## 5. 공급자 판별

- 모델 이름 앞부분으로 판별: 설정 파일 `shared/agent-template/.../llm_providers.toml`(접두어 → 공급자·기본 주소). 코드에 모델 이름 없음
- 목록에 없으면 화면이 "OpenAI 호환 주소"를 받는다(`provider=openai_compatible`, `base_url` 필수)
- 주소 검사는 `company_connection.check_host`와 같은 정책(링크 로컬·메타데이터 거절, 사내 밖은 https + 허용 목록)과 `pinned_http`
- 저장 전 "연결 시험": 1토큰짜리 호출 한 번, 결과(성공/거절 코드)만 저장

## 6. 코딩·영상 분리

- 코딩 에이전트: `coder.mode=company`면 회사 키·모델, `separate`면 `DEV_CODER_LLM_API_KEY`와 `coder.model/base_url`. 사용량은 둘 다 회사 예산에 센다(`department=dev`, `source=coder`)
- 영상: 키가 있어야 켜짐. 키 넣는 곳은 이 화면만 → 마케팅 화면의 영상 공급자 카드는 "회사 연결에서 설정" 링크와 상태만 보인다. 영상·이미지 예산은 토큰이 아니라 `MARKETING_QUOTA_*`(초·장·곡) 그대로, 화면 표시는 같은 "사용량 / 예산, 남은 %"

## 7. 사용량 API (dashboard-api)

| API | 권한 | 내용 |
|---|---|---|
| `GET /company-ai` | 관리자 | 설정 전체(키는 `has_key`·끝 4자리), 대기 중 승인 요청 |
| `PUT /company-ai/settings` | 관리자 | 모델·주소·예산·코딩 방식 |
| `POST /company-ai/keys/{name}` | 관리자 | 키 등록/교체 요청(8장) |
| `DELETE /company-ai/keys/{name}` | 관리자 | 삭제 요청(8장) |
| `POST /company-ai/test` | 관리자 | 연결 시험 |
| `GET /ai-connection/status` | 모두 | 회사 AI 상태(연결됨/키 없음/체험 중/예산 다 씀), 이번 달 사용 %, 영상 켜짐 여부 |
| `GET /ai-connection/usage?month=YYYY-MM` | 모두 | AI 직원(부서)별 토큰·%, 영상·이미지 수량 |
| `GET/POST/DELETE /me/connections` | 본인 | 개인 연결 토큰(9장) |

- 전부 회사 ID는 세션에서만 꺼낸다(요청 값으로 받지 않음)
- 사용량은 `llm_usage`의 읽기 조회(`LlmUsageQuery`)에 `tenant_id`·월·부서 묶음 조회를 **추가**(계약은 추가만)
- 비용(원)은 보이지 않는다. 토큰과 %만

## 8. 교체·삭제 승인 흐름

`secret_requests.py`(add_secret/change_secret) 틀을 공통으로 올려 쓴다.
- **처음 등록**: 관리자만 하므로 바로 적용(`add_secret`)
- **교체·삭제**: 대기 요청을 만들고 다른 관리자 승인 뒤 적용(`change_secret`, 안전 관문). 요청자 본인 승인은 막는다
- **관리자가 한 명뿐**: 바로 적용 + 회원 모두에게 알림("누가 회사 AI 키를 바꿨어요, 끝 4자리 ****")
- 모델·예산 변경도 같은 규칙인지 → 열린 질문 2
- 상태 변경은 agent-template `execute()`로만(규칙). 이력에는 지문·끝 4자리만

## 9. 개인 연결 토큰 (내 연결)

- 각자 화면에서 만든다. 만들 때 한 번만 원문을 보여 주고 이후엔 이름·끝 4자리·만든 날·마지막 사용
- 저장은 해시만(dashboard-api 계정 저장소, 회사 ID·사람 ID 붙임). 만료 기본 90일, 언제든 끊기
- 권한은 지금 `DEV_WORK_TOKENS`와 같은 **좁은 권한**(작업 대기열·읽기). 관문 승인·머지·재무 같은 사람 결정은 못 한다
- `work_auth.py`는 환경변수 토큰과 함께 이 토큰도 받게 하고, 옮긴 뒤 환경변수 방식은 끈다
- 개인 도구의 LLM 요금은 개인 계정 → 회사 예산·사용량에 세지 않는다

## 10. 이전 계획

1. dev 평문 BYOK → 키 저장소: 시작 때 `llm_key.json`이 있으면 `tenant=<설치 회사>`의 `LLM_API_KEY`(코딩 전용이었으면 `DEV_CODER_LLM_API_KEY` + `coder.mode=separate`)로 옮기고 파일을 지운다. 한 버전 동안 읽기만 남긴 뒤 코드 삭제. `monthly_budget_tokens`는 `company_ai.json`으로
2. 마케팅 영상 키: 이름은 그대로(옮길 값 없음), 넣는 화면만 회사 연결로 옮긴다. 마케팅 화면 카드는 상태 표시로
3. 환경변수 `LLM_*`: 회사 설정이 없을 때만 쓰는 값으로 남긴다(로컬·테스트). 실제 설치는 회사 설정이 먼저

## 11. PR 나누기

| # | 내용 | 영향 | 담당 |
|---|---|---|---|
| 1 | 계약 추가: 사용량 묶음 조회, `CompanyAiSettings` 모양 | contracts | 예진 |
| 2 | `settings_for`·contextvar·공급자 판별·체험 폴백·월 예산 검사 | agent-template | **준하 님** |
| 3 | dashboard-api `/company-ai`·`/ai-connection` + 승인 흐름 공통화 | dashboard-api, safety | 예진 |
| 4 | 화면: 공통 AI 도구 연결 탭, AI 연결 탭 | frontend | 예진 |
| 5 | dev BYOK 이전·체험 한도 합치기·코딩 분리 | dev | 예진 |
| 6 | 마케팅 영상 키 화면 이전·예산 표시 맞추기(#1490과 맞춤) | marketing | **재헌 님** |
| 7 | 개인 연결 토큰 + `work_auth` | dashboard-api, dev | 예진 |

순서: 1 → 2 → (3, 5, 6 나란히) → 4 → 7. 1·3은 예진이 한 PR로 묶어 먼저 진행(계약 `CompanyAiSettings` 포함, 회사 ID는 지금 있는 `tenant.current()`). 2(준하 님)는 1과 회사 공간 1단계(`meta.tenant_id` 채우기) 머지 뒤 시작. 번호는 이 표를 기준으로 부른다.

## 12. 위험

- 42곳 `LLMClient` 중 회사 문맥 없이 부르는 곳(배치·스케줄 작업)은 회사 ID가 비어 체험/키 없음으로 멈출 수 있다 → 2번 PR에서 문맥 없는 호출을 목록으로 뽑고 회사 ID를 넘긴다
- `AX_LLM_USAGE_DB`가 꺼져 있으면 예산 검사가 늘 0 → 실제 모드에서 비어 있으면 시작 때 경고, 예산이 설정된 회사는 호출을 막는다
- 예산 검사는 사후 기록 기반이라 동시 호출에서 조금 넘을 수 있다(허용, 문서화)

## 13. 열린 질문

1. ~~직원별 다른 공급자·키 허용~~ → 11/20 결정: 직원별 모델 선택 자체를 이번엔 하지 않음(회사당 키·모델 하나, 코딩·영상만 예외). 원하는 고객사가 생기면 모델만 추가
2. 모델·예산 변경도 다른 관리자 승인을 받을지(추천: 예산을 **올리는** 것만 승인, 모델 변경·예산 내리기는 바로 + 알림)
3. 체험 키를 둘지, 회사 공간 설계대로 "키 없으면 멈춤"만 할지(추천: 체험 키는 회사별 토큰 한도로 두고 사용량을 그 회사로 센다)
4. 월 예산의 "월" 기준(추천: 회사 시간대 1일 0시)
5. 개인 연결 토큰 만료 기본값(추천: 90일)
