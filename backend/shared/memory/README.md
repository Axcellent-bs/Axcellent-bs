# memory
담당: 재현 · 포트: 8002
## 1. 역할
**회사 기억**. 부서들이 만든 카드(사건·결정·사례·공지·정책·사기·요청)를 저장하고, 의미가 가까운 카드를 찾아 주고, 옛 카드를 새 카드로 대체·폐기 연결한다. 카드 문장은 쓰는 부서가 만들고 memory는 `title`·`body`를 바꾸지 않는다.

- skill 5개 (`src/ax_memory/handlers.py`, `agent-card.json`)
  - `card_write` (등급 A): 카드 저장. 저장 전에 `execute("memory.card_write")`가 `executed`를 돌려줘야 한다. `meta.idempotency_key`가 없으면 `invalid_input`. `card.tenant_id`가 요청 `meta.tenant_id`와 다르면 `invalid_input`
  - `card_search` (read): 의미 검색. 상태를 바꾸지 않는다. 부를 때마다 공통 로그 `memory_referenced`(requester·card_ids·hit_count)를 남긴다
  - `card_get` (read): `card_id`로 한 장. 없거나 다른 회사 카드면 `failed` + `card_not_found`
  - `card_link` (등급 A): 옛 카드를 `superseded`(새 카드 id를 `superseded_by`에) 또는 `retired`로 바꾼다. `execute("memory.card_link")`를 거친다. 두 카드가 모두 같은 회사에 있어야 하고, 같은 카드끼리는 거부
  - `card_review` (등급 A, #304): 후보 카드(`candidate: true`)를 사람이 승인·폐기한 결과를 반영한다. 승인이면 `candidate`가 false(`status`는 `active` 그대로), 폐기면 `status`가 `retired`. `execute("memory.card_review")`를 거친다. 이미 반영된 결정이면 바꾸지 않고 `outcome: duplicate`·`changed: false`, 후보가 아닌 카드는 `failed` + `not_candidate`
- 후보 카드 (#304): `card_write`의 `card.candidate: true`로 저장한다(기본 false). 저장소 두 곳(`store.py` 메모리, `pg_store.py` `cards.candidate` 칸) 모두 저장한다. 예전 Postgres 테이블은 서버를 켤 때 칸을 더한다(기존 카드는 false). `card_search`·`card_get` 결과에 그대로 나오고, 판정 근거에서 빼는 것은 읽는 쪽이 정한다(data `past_incidents`는 뺀다)
- 멱등 (`store.py`): 키 = `context_id + kind + sha256(title)`, 회사(`tenant_id`)마다 따로 본다. 같은 키로 다시 쓰면 새로 만들지 않고 기존 카드를 `created: false`로 돌려준다. 그래서 같은 제목은 다시 쓸 수 없고, 내용을 바꾸려면 새 제목으로 쓰고 `card_link`로 옛 카드를 대체한다
- 외부 입력(`request.untrusted`)은 본문에 섞지 않고 `extra["untrusted"]`에만 둔다

### 카드 종류(kind)와 쓰는 쪽·읽는 쪽
종류는 계약 `Card.kind`(`incident`·`decision`·`case`·`notice`·`policy`·`fraud`·`request`)다. 아래는 이 레포 코드에서 memory **A2A skill을 실제로 부르는 곳**만 적었다.

| kind | 쓰는 쪽 (`card_write`) | 읽는 쪽 (`card_search`) |
| --- | --- | --- |
| `policy` | memory 자신 (서버 시작 시드, `seed/loader.py`) | support `policy_memory.py` (답변 근거) |
| `request` | planning·개발 서버 (`shared/workflow-core` `request_index.py`, 요청 id마다 1장) | 같은 곳 (`related.py`, 관련 요청 찾기) |
| `incident` | 확인 필요 (A2A로 쓰는 코드를 찾지 못함) | data `fact_sources.py` `past_incidents` (원인 추정의 "지난 비슷한 사건", top_k 3) |
| `decision`·`case`·`notice` | 확인 필요 (아래 "인사 AI와의 관계") | 확인 필요 |
| `fraud` | 확인 필요 | 확인 필요 |

- `card_link`·`card_review`를 A2A로 부르는 코드는 아직 없다 (Agent Card상 요청 가능한 쪽은 hr뿐). hr 전환 때 쓴다(#304)
- `card_get`의 요청 가능 목록에는 dashboard-api가 있지만, 부르는 코드는 확인 필요

### 인사 AI와의 관계 (#304)
**결정 (2026-11-02, #304):** 사건·결정·사례 카드의 원본 저장소는 **이 memory 서버 하나**다. memory에 REST를 두지 않고, hr는 A2A `card_write`·`card_search`·`card_get`·`card_link`·`card_review`로 맞춘다(전환은 hr 담당). 그 전까지 hr는 자기 SQLite를 그대로 쓰고, 기존 hr SQLite 카드를 옮길지는 전환 때 정한다.

지금 상태:
인사 AI(hr)는 사건·결정·사례 카드를 만들고 관문 `memory.card_write`·`memory.card_link`를 거치지만, 저장은 memory A2A가 아니라 hr의 `MemoryClient`로 한다.
- 기본 `HR_MEMORY_MODE=mock`: hr 자기 저장소(`HR_DB_PATH`의 SQLite)에 둔다. `scripts/run_all.sh`도 이 값을 바꾸지 않는다
- `HR_MEMORY_MODE=http`: `clients/real.py`가 REST `POST /api/memory/cards`, `GET /api/memory/cards/search` 등을 부른다. 이 서버(`src/ax_memory/`)에는 그 REST 경로가 없다(`/a2a`·`/health`·Agent Card만 있음). 맞추는 일은 확인 필요
- 그래서 지금 실제 모드에서 이 서버의 `incident`·`decision`·`case` 카드가 누가 채우는지는 확인 필요

### 검색 흐름 (임베딩 → 코사인 유사도)
1. 저장: `card_write`가 새 카드를 만들면(`created: true`) 카드 글(`title`, `body`, 태그를 줄바꿈으로 이음, `embed.card_text`)을 임베딩해 벡터 인덱스에 올린다. 같은 키로 기존 카드를 돌려줄 때는 다시 임베딩하지 않는다
2. 검색 `card_search(query, kind, status="active", since, top_k=5)`:
   - 먼저 저장소에서 후보를 고른다: 요청 회사(`meta.tenant_id`) + `status` + `kind`(비우면 전부) + `since`(그 뒤에 만든 카드)
   - 후보가 없으면 임베딩을 부르지 않고 빈 결과
   - 질의를 임베딩하고, 후보 id 안에서만 코사인 유사도 내림차순(같으면 `card_id` 순)으로 `top_k`(1~20)장
   - 점수는 0~1로 자른다. 카드 `confidence`가 `low`면 `low_confidence: true`로 표시만 하고 빼지는 않는다 (빼는 것은 읽는 쪽이 정한다)
   - 기본 `status="active"`라 `superseded`·`retired` 카드는 기본 검색에 나오지 않는다
3. 점수 하한은 memory에 없다. 하한은 읽는 쪽마다 정한다 (아래 "부서별 기준값")
- 임베딩 (`embed.py`): `LLMClient("memory").embed`. 모델은 `MEMORY_EMBED_MODEL`(없으면 `EMBED_MODEL`), 주소·키는 `MEMORY_LLM_*`(없으면 `LLM_*`). 코드에 모델 이름을 두지 않는다. `MEMORY_MOCK=1`이면 네트워크 없는 해시 bag-of-words 목 임베더(64차원)
- 임베딩이 실패한 `card_write`: 카드는 저장된 뒤 인덱싱에서 예외가 난다. 그 카드가 이후 검색에 나오는지는 확인 필요 (메모리 인덱스에는 들어가지 않고, Postgres에서는 `embedding`이 비어 검색에서 빠지는 것으로 보인다)

### 저장소
`MEMORY_PG_DSN`이 있느냐로 정한다 (`handlers.State`).

| | 저장소 | 벡터 인덱스 | 켜면 |
| --- | --- | --- | --- |
| `MEMORY_PG_DSN` 없음 | `InMemoryCardStore` (프로세스 메모리) | `InMemoryVectorIndex` (파이썬 코사인) | 서버를 끄면 카드가 사라진다. 시드 카드만 다시 들어온다 |
| `MEMORY_PG_DSN` 있음 | `PgCardStore` (PostgreSQL `cards` 테이블) | `PgVectorIndex` (pgvector, `1 - (embedding <=> 질의)`) | 시작할 때 `vector` 확장·`cards` 테이블·`(tenant_id, kind, status)` 인덱스를 만든다(여러 번 불러도 안전). 멱등은 `UNIQUE (tenant_id, idempotency_key)` |

- 벡터 차원 `MEMORY_EMBED_DIM` (기본 768). 임베딩 모델을 바꿔 차원이 달라지면 `cards` 테이블을 다시 만들어야 한다. 차원이 다른 벡터는 거부한다
- memory 자체에는 SQLite 구현이 없다. SQLite는 hr 쪽(`HR_DB_PATH`, hr Dense 색인)이다
- `scripts/run_all.sh`는 `MEMORY_PG_DSN`을 넣지 않는다. `.env`에 있으면 Postgres, 없으면 메모리 저장이다 (로컬에서 어느 쪽인지는 각자 `.env`에 따라 다름)

### 서버 시작할 때 시드 카드
`main.py`의 `seed_on_start` → `seed/startup.py`.
- 정책 문서 24섹션(`seed/loader.py` `POLICY_SECTIONS`)을 `kind=policy` 카드로 저장한다: 환불 3, 보상 4, 입점 3, 배송 3, 결제 3, 취소 2, 계정 2, 개인정보 삭제 2, 상품 1, 후기 1
- 카드 모양: `context_id = ctx_ntc_policy_seed`(공지 접두사), `author = memory-seed`, 태그 `["policy", <doc_id>]`, `extra`에 `doc_id`·`section_id`·`version`(·`condition_code`). 분류별 정책의 `doc_id`는 고객지원 분류 코드와 같다 (support가 태그를 분류로 읽음)
- 미확정 섹션 5장: 제목 끝 "(미확정)", `confidence = low`, `extra.confirmed = false` → 검색에서 `low_confidence`
- 기본 회사(`TENANT_ID_DEFAULT` = `shop-demo`)에만 넣는다. 다른 `tenant_id`로 검색하면 시드 정책이 나오지 않는다
- 멱등이라 다시 켜도 늘지 않는다. 저장 뒤 정책 카드를 한 번에 임베딩해 인덱스에 올린다
- 임베딩이 안 되면(모델 미설정·네트워크) 서버는 그대로 켜지고 경고 로그만 남는다. 카드는 저장돼 있지만 검색에는 나오지 않는다
- 끄기: `MEMORY_SEED_POLICIES=off`

### 부서별 기준값 (점수 하한)
memory는 하한 없이 점수만 준다. 하한은 각 부서 코드에 있다.

| 쓰는 곳 | 값 | 어디서 정함 | 바꾸는 법 |
| --- | --- | --- | --- |
| 고객지원 정책 근거 | 0.65 | `domains/support/src/support_agent/policy_memory.py` `MIN_SCORE`. memory에서 6장 받아 `low_confidence`·`active` 아님을 빼고, 0.65 미만은 근거로 쓰지 않음. 근거 없는 분류에 엉뚱한 분류 카드(0.59~0.62)가 들어오는 것을 막으려는 시드값 | `SUPPORT_POLICY_MIN_SCORE` (0~1) |
| planning·개발 관련 요청 | 0.6 | `shared/workflow-core/src/ax_workflow/related.py` `MIN_SCORE["memory"]` | 코드 |
| data 지난 사건 | 하한 없음 | `fact_sources.py`: top_k 3, `low_confidence`·`candidate`·같은 사건 카드만 뺌 | — |
| 인사 Dense 검색 | 0.71 (같은 방향 판정 0.72) | `domains/hr/src/hr_agent/config.py` `dense_min_cosine`·`dense_match_cosine`. 골든 셋 개발용 질의에서 정한 0.7063을 올림 | `HR_DENSE_MIN_COSINE`, `HR_DENSE_MATCH_COSINE` |

- 인사 0.71은 memory `card_search` 점수가 아니라 hr 자기 Dense 색인(SQLite, 임베딩 설정은 `MEMORY_*`/`EMBED_MODEL`을 같이 씀)의 코사인이다
- 모든 값은 임베딩 모델에 묶여 있다. 모델이 바뀌면 다시 정해야 한다

## 2. 실행 방법
- 로컬 전체: `bash scripts/run_all.sh` (core 묶음, 8002 `ax_memory.main:app`). `EMBED_MODEL`이 없으면 시작할 때 "회사 기억 검색이 실패한다"는 경고가 나온다
- 컨테이너(pgvector 포함): `docker compose -f shared/memory/compose.yml up` → `memory`(8002) + `memory-db`(`pgvector/pgvector:pg16`, 호스트 포트 안 엶). compose 안에서는 `MEMORY_PG_DSN`을 `memory-db`로 덮어쓴다
- 목 모드: `MEMORY_MOCK=1` (목 임베더). 저장소는 `MEMORY_PG_DSN`이 없으면 메모리
- 테스트: `uv run pytest shared/memory`
- 환경값 예시: `.env.example` (`MEMORY_TOKEN`, `MEMORY_PG_DSN`, `MEMORY_DB_PASSWORD`, `MEMORY_EMBED_DIM`, `MEMORY_EMBED_MODEL`). Agent Card 경로는 `MEMORY_AGENT_CARD`로 바꿀 수 있다

## 3. 입출력 계약
엔드포인트는 템플릿(`create_agent_app`)이 만든다: `POST /a2a`, `GET /health`, `GET /.well-known/agent-card.json`. REST API는 없다.

| skill | 입력 → 출력 (`shared/contracts/.../schemas/record.py`) | 등급 | 요청 가능 |
| --- | --- | --- | --- |
| `card_write` | `CardWriteInput{card: CardDraft}` → `CardWriteOutput{card_id, created}` | A | hr, support, marketing, planning, finance, orchestrator, data |
| `card_search` | `CardSearchInput{query, kind[], status, since, top_k}` → `CardSearchOutput{hits: CardHit[]}` | read | orchestrator, planning, support, marketing, hr, finance, data, infra, safety |
| `card_get` | `CardGetInput{card_id}` → `CardGetOutput{card}` | read | 위 + dashboard-api |
| `card_link` | `CardLinkInput{old_card_id, new_card_id, relation}` → `CardLinkOutput{old_card}` | A | hr |
| `card_review` | `CardReviewInput{card_id, decision: approve\|reject}` → `CardReviewOutput{card, changed}` | A | hr |

- 관문 결과에 따라: `held` → `input-required`(사람 승인 대기), `verifying` → `working`, 검증 거절 → `completed` + `rejected_by_review`, 검증 시간 초과 → `canceled` + `TIMEOUT`, 그 밖의 거부·실패 → `failed`
- `Card.candidate`(사람 확인 전 후보)는 `card_write`(`CardDraft.candidate`)로 저장하고 `card_review`로 바꾼다(#304)
- MCP 도구 이름: `memory_write_card`·`memory_search_card`·`memory_get_card`·`memory_link_card` (Agent Card 설명)

## 4. 의존
ax-contracts, ax-agent-template (`create_agent_app`, `execute`, `LLMClient.embed`, `log_event`), safety(관문 `memory.card_write`·`memory.card_link`·`memory.card_review`), 임베딩 모델(LLM 엔드포인트), PostgreSQL + pgvector(선택, `psycopg`)

## 5. 하지 않는 것
- 카드 문장을 쓰거나 고치지 않는다 (쓰는 부서 책임). 요약·생성에 LLM을 쓰지 않는다 (임베딩만)
- 점수 하한·"관련 있음" 판정을 하지 않는다 (읽는 쪽이 정함). `low_confidence` 카드도 빼지 않고 표시만 한다
- 정책 판정을 하지 않는다: 시드 정책은 조건과 사실만 담고, 보상 금액·등급은 고객지원·마케팅·안전 레이어, 사기 점수는 data, 심사는 finance
- 카드를 지우지 않는다. 옛 카드는 `card_link`로 `superseded`·`retired`, 폐기한 후보 카드는 `card_review`로 `retired`가 될 뿐이다
- 다른 회사 카드를 보여 주지 않는다 (`tenant_id`로 거름)
- 관문(`execute()`) 없이 카드를 쓰거나 상태를 바꾸지 않는다
