# 캣북스 마케팅 에이전트 — Instagram 게시(SNS) 설계 초안

> 작성: 2026-10-27, 이 레포로 옮김: 2026-11-03 · 상태: **초안(팀 검토 전)** · 대상: `backend/domains/marketing/`
> 전제 문서: `backend/docs/notes/marketing/instagram-policy-checklist.md`, `ai-influencer-pipeline.md`, `backend/shared/safety/src/ax_safety/rules/registry.toml`·`thresholds.toml`, 원본 레포(ax-backend)의 `docs/requirements.md`(SCN-A, SAF-01~11, DEP-MKT-01~04, 참고용)
> 표기: 레포에서 확인한 사실은 그대로 적고, 확인하지 못한 것은 `[확인 필요]`, 팀이 정할 값은 `(TODO: 추후결정)`으로 둔다. 이 문서는 값을 확정하지 않는다.

---

## 0. 이 레포 기준으로 읽는 법 (2026-11-03 정리)

초안은 원본 레포(ax-company-hq/ax-backend)에서 썼다. 이 레포(Axcellent-bs)로 옮기면서 달라진 점만 적는다. 본문의 옛 경로는 아래로 읽는다.

| 초안의 표기 | 이 레포 |
| --- | --- |
| `agents/marketing/` | `backend/domains/marketing/` (서비스는 `backend/services/coupon`) |
| `contracts/` | `backend/shared/contracts/` (추가만 한다) |
| `safety/src/ax_safety/rules/registry.toml`·`thresholds.toml` | `backend/shared/safety/src/ax_safety/rules/` (사람만 PR로 바꾼다) |
| `docs/notes/marketing/` | `backend/docs/notes/marketing/` |
| 공통 로그 `contracts/data-access/common_log.py` | `backend/shared/agent-template/src/ax_agent_template/common_log.py` |

- **이미 있는 것**: `ChannelCode`에 `"sns"`가 있다(`marketing_agent/schemas.py`). 배너 공지 연결은 PR #483, 쿠폰 쇼핑몰 연결은 PR #481(둘 다 머지 전)이라, 3절의 "`publish_notice`는 메모리에만 기록" 설명은 #483이 머지되면 배너 채널에 한해 바뀐다. SNS 채널(`sns`)은 그대로 메모리다.
- **초안과 달라진 사실**: 3절의 "`generate_pr`가 `verifier = "safety"` 코드 규칙을 쓰는 선례"는 지금 레지스트리에 `generate_pr` 항목이 없다(검증자는 `planning`·`data` 등 다른 부서 skill을 가리킨다). 검증자를 safety 코드 규칙으로 두는 방식이 지금 관문에서 지원되는지 `[확인 필요]`. 요청 이슈 초안(`sns-safety-request.md`)의 질문 3과 이어진다.
- **새 사실**: 2026-11-03 재헌이 #2-마케팅-고객지원에 `instagram.com/catbooks.lab`을 공유하며 "마케팅 AI 에이전트의 관여는 앞으로 이 계정에서 실시될 예정"이라고 알렸다. 10절 "게시 계정" 결정의 후보이며, 운영 주체 표기·테스트 계정 여부는 아직 정하지 않았다 `(TODO: 추후결정)`.
- **하지 않은 것**: 코드는 아직 한 줄도 없다. 이 문서와 요청 이슈 초안만 올린다. 8절 구현 순서의 S0에 해당한다.

---

## 1. 목적과 범위

### 하는 것
- 마케팅 에이전트가 **사이트 공지(배너)로 이미 나간 공지**와 **공개 쿠폰 이벤트**를 Instagram 피드·릴스에도 올릴 수 있게 한다.
- 게시물 = 텍스트(캡션) + 이미지 또는 짧은 세로 영상(9:16). 영상은 외부 생성 모델로 만들고 **AI 라벨을 후처리 오버레이**로 넣는다.
- 게시는 **실행 관문 `execute()`** 를 거치고, 등급은 안전 관문(safety, 태윤)이 레지스트리·기준표로 정한다. 마케팅은 **등록 요청만** 한다(요청 이슈 초안: `sns-safety-request.md`).
- 결과(execution_id, 게시 ID, AI 라벨 전달·응답값)를 공통 로그와 공지 저장소에 남긴다.

### 안 하는 것
- 자동 DM·댓글 수집·스토리 게시(체크리스트 4절 그대로).
- 참여 조건부 이벤트(좋아요·팔로우·공유 조건) — 공개 쿠폰만.
- 사람 승인 없는 외부 공개(게시 등급은 아래 3절 제안 → 최종은 safety).
- 계정 생성·로그인·토큰 발급·결제수단 등록·지출 한도 설정 — **사람이 한다**. 에이전트는 `.env`에 들어 있는 값만 읽는다.
- 장애 원인·영향 숫자를 캡션에 적는 일(README "하지 않는 것" 그대로. 공지는 사실 필드만).
- 제3자 책 표지·상표·실존 인물 이미지 사용.
- `shared/contracts/`·`shared/safety/` 직접 수정(요청 이슈·계약 PR로만).

### 시연에서 보여줄 장면 (제안)
1. 시나리오 A에서 **정상화 공지가 배너에 나간 뒤**, 총괄이 마케팅에 "이 공지를 SNS에도"를 선택 단계(6+)로 요청한다. 영상은 쓰지 않고 **이미지 1장 + 캡션**(시연 안정성).
2. 안전 관문이 `publish_sns`를 **C로 보류** → 대시보드(DSH-03)에서 사람이 승인 → 게시 → `execution_id`·게시 ID가 사건 타임라인에 붙는다.
3. (여유가 있을 때) 평소 운영 장면: 공개 쿠폰 릴스를 `generate_video`로 만들고 AI 라벨 오버레이가 들어간 결과를 보여 준다. **실제 게시는 테스트 계정 1건**만.

---

## 2. 전체 흐름

| 단계 | 누가 | 무엇 | 입력 → 출력 | 등급 | 기록 |
| --- | --- | --- | --- | --- | --- |
| 0 | orchestrator(사건) 또는 사람(캠페인) | 요청 | 배너 공지 `notice_id` 또는 캠페인 `campaign_id` + `context_id` | — | `request_received` |
| 1 | marketing `draft_sns_post` | 게시 초안 | 공지 사실 필드(`get_notice`와 같은 저장소)·쿠폰 제약 사항 → 캡션·해시태그·필수 문구(AI 라벨·시연용·약관) 포함한 `SnsPostDraft` | read | `result_sent` |
| 2 | marketing `generate_video` *(선택)* | 영상 생성 + 라벨 오버레이 | `SnsPostDraft` + 프롬프트(확인된 사실만) → `asset_ref`, 비용, `label_overlay=true` | **B 제안**(비용 발생) | `execute_decided`, `result_sent` |
| 3 | marketing `check_sns_post`(검증자 후보, read) | 코드 규칙 점검 | 초안+자산 → 필수 문구 유무, 금칙어, 길이·규격, 라벨 유무 | read | `result_sent` |
| 4 | marketing `publish_sns` → `execute()` | 등급 판정 요청 | `action=publish_sns`, params(아래 4절), `idempotency_key` | safety 판정 | safety가 `execute_decided` |
| 5 | safety / 사람 | C면 보류 큐 → 대시보드 승인·거절 | `review_id` | C | safety 기록 |
| 6 | marketing(`executed` 뒤에만) | Instagram Graph API 게시 | 컨테이너 생성 → 상태 확인 → 게시 | — | `result_sent` + 게시 ID |
| 7 | marketing | 결과 기록·이벤트 | `notice.published`(channel_code=`sns`) 재사용 또는 새 이벤트 `(TODO)` | — | `event_published` |

기록 방식(레포 관례): 상태 변경은 `ax_agent_template.execute.execute()` 한 곳만, 로그는 `ax_agent_template.logging.log_event` → 공통 로그 저장소(`common_log.py`, `LogKind`: `request_received`·`result_sent`·`execute_decided`·`event_published`…). 결과 `evidence.ref_ids`에 `execution_id`(접두사 `exe_`)·게시 ID·`asset_ref`를 넣는다(`handlers.py`의 `publish_notice`가 `[notice_id, decision.execution_id]`를 넣는 방식과 동일). 보류·거절·관문 무응답 처리는 `_from_decision()` 그대로(fail-closed: `exe_unavailable` → `rejected`).

사건 번호: 장애 공지 재게시는 **같은 `ctx_inc_`** 아래에서(총괄이 발급), 캠페인 게시는 marketing이 발급하는 **`ctx_cmp_`**(`ContextPrefix.CAMPAIGN`, 초안 시점 확인) 아래에서 처리한다 `[확인 필요: 이 레포 ids.py 기준]`.

---

## 3. skill 후보와 등급 제안

기존 skill(README·`agent-card.json` 확인): `draft_notice`(read), `publish_notice`(A), `get_notice`(read), `list_active_notices`(read), `issue_coupon`(A, 기준 초과·금액 불일치 C). `ChannelCode`에 이미 `"sns"`가 있다.

**충돌 1건(중요)**: 현재 `publish_notice`는 `channel_code="sns"`여도 **A로 바로 실행**된다(registry `publish_notice = A`, 채널 구분 없음). SNS는 외부 공개·되돌리기 어려움이므로 **별도 액션 `publish_sns`로 분리**하고, `publish_notice`는 `channel_code="sns"` 요청을 코드로 거부(`invalid_input`)하거나 `publish_sns`로 안내하도록 바꾼다(마케팅 폴더 안 변경, 계약 변경 없음). `[팀 확인]` — 또는 safety가 registry에서 `publish_notice`에 `when = { channel_code = "sns" }` 규칙으로 C를 거는 방식도 가능(값은 safety 몫).

**이름 정리**: `ai-influencer-pipeline.md`의 `produce_short`(B)·`publish_short`(C)와 겹친다. 제안: `produce_short` → `generate_video`(공지·쿠폰·쇼츠 공통), `publish_short` → `publish_sns`(플랫폼 파라미터로 구분)로 **통합**해 액션을 2개만 등록한다. 기존 문서는 머지 뒤 각주로 맞춘다 `[팀 확인]`.

| skill | 상태 변경 | 등급 제안 | 이유 | 대안 |
| --- | --- | --- | --- | --- |
| `draft_sns_post` | 없음 | read | 초안만 만든다. 문안은 v0 고정 템플릿(공지 방식), LLM은 이후 | — |
| `generate_video` | 비용 발생(외부 유료 API) | **B**, 검증자 = `safety`(코드 규칙: 건당 비용 상한·길이·해상도·일일 누적) | 되돌릴 수 있지만 돈이 나간다. 초안 시점에는 `generate_pr`가 `verifier = "safety"` 코드 규칙을 쓰는 선례가 있었으나 **지금 레지스트리에는 없다** `[확인 필요]` | A + 비용 기준표(issue_coupon 방식: 한도 안 A, 초과 C). 교차검증할 다른 부서가 없어 B의 검증자는 safety 코드 규칙뿐 |
| `check_sns_post` | 없음 | read (safety가 B 검증자로 부를 수 있는 skill 후보) | 코드 규칙 점검: 필수 문구·금칙어·규격 | 검증자를 **마케팅 자신**으로 두면 registry가 거부(owner=verifier 금지, registry 주석 확인). 따라서 safety 코드 규칙 또는 다른 부서가 검증해야 함 |
| `publish_sns` | **외부 공개, 되돌리기 어려움** | **C(항상 사람 승인)** — 1차 | 요구사항 "되돌릴 수 없는 영역"에 "외부 고객 대상 발송"이 들어 있다(원본 레포 `issue-166-planning-grades.md`). 데모·초기엔 모든 게시를 사람이 본다 | **B**(검증자 safety 코드 규칙: AI 라벨·문구 일치·일일 건수) — 테스트 릴스로 규칙이 검증된 뒤 하향은 **사람 결정**(safety 메모: 자동 하향은 팀 결정 필요) |

미등록 액션은 safety가 C로 보류한다(`HoldReason.UNREGISTERED_ACTION`). 등록 전에도 **동작은 막히지 않고 사람 승인으로 간다**는 점은 안전하나, 라이브에서는 등록이 있어야 한다.

---

## 4. `generate_video` 인터페이스 (제안)

### 입력 `GenerateVideoInput`
| 필드 | 설명 |
| --- | --- |
| `post_draft_id` | `draft_sns_post` 결과 ID. 프롬프트 재료는 이 초안의 **확인된 사실 필드만**(상품 ID·카테고리·쿠폰 제약 사항·공지 종류). `untrusted` 원문·고객 문의·원인 추측은 넣지 않는다 |
| `scene_template_id` | 마케팅 설정의 장면 템플릿 ID(캣북스 고양이 캐릭터·서점 배경 등). 자유 프롬프트는 받지 않는다 |
| `duration_s` | 기본 8, 상한은 설정(`(TODO)`) |
| `aspect_ratio` | `"9:16"` 고정 |
| `resolution` | `"720p"` 기본. 1080p는 공급자 단가가 공식 페이지에 없어 `[확인 필요]` |
| `korean_overlay` | `{title, price_text}` — **모델에 한글을 생성시키지 않고** 후처리 오버레이로 넣는 문구(코드가 사실 필드에서 만든다) |
| `claimed_cost_krw` *(선택)* | 요청 쪽 추정값. 코드 계산값과 다르면 safety가 C(`amount_mismatch`) — `issue_coupon`의 `claimed_total` 패턴 |

### 출력 `GeneratedVideo`
`video_id`(접두사는 마케팅 폴더 안에서만 쓰는 `vid_` 제안; 계약 `IdPrefix`에 넣을 필요가 생기면 계약 PR), `asset_ref`(저장 경로), `provider`, `model_id`(응답에서 받은 값), `duration_s`, `resolution`, `cost_krw`(정수 원, 코드 계산), `label_overlay: true`, `generated_at`(UTC), `provider_job_id`, `fallback`(공급자 실패 시 이미지 대체면 true).

### 공급자 교체 (코드에 모델 하드코딩 금지 — 루트 CLAUDE.md 규칙과 동일)
환경변수는 부서 접두사 규칙(`MARKETING_*`)에 맞춰 **이름만 제안**한다. 값은 모두 `.env`(사람 입력).

| 변수(제안) | 뜻 | 기본 |
| --- | --- | --- |
| `MARKETING_VIDEO_PROVIDER` | `mock` / `gemini` / `minimax` / `wan` / `kling` | `mock` |
| `MARKETING_VIDEO_MODEL` | 공급자 모델 ID. 기본 후보 `gemini-omni-1.1-flash`(GA). **`gemini-omni-flash-preview`는 쓰지 않는다**(2026-10-22 가장 이른 종료일) | 비움 → `(TODO: 추후결정)` |
| `MARKETING_VIDEO_API_KEY` | 공급자 키(사람 입력) | 비움 |
| `MARKETING_VIDEO_BASE_URL` | 엔드포인트(OpenAI 호환 래퍼를 쓸 수 있으면 `LLM_BASE_URL` 패턴 재사용 `[확인 필요]`) | 비움 |
| `MARKETING_VIDEO_MAX_COST_KRW_PER_CLIP` | 건당 상한(코드가 생성 **전에** 추정 비용과 비교, 초과면 호출하지 않음) | `(TODO)` |
| `MARKETING_VIDEO_MONTHLY_BUDGET_KRW` | 월 누적 상한(마케팅 쪽 1차 차단; safety 기준표의 일일 누적과 별도) | `(TODO)` |
| `MARKETING_VIDEO_TIMEOUT_S` | 생성 대기 상한 | `(TODO)` |
| `MARKETING_MEDIA_DIR` | 생성물 저장 경로(컨테이너 볼륨) | `(TODO)` |

공급자 어댑터는 `marketing_agent/video/<provider>.py`처럼 **인터페이스 1개 + 공급자별 모듈**로 두고, 공급자·모델 ID는 환경변수에서만 읽는다. 목 모드(`MARKETING_MOCK=1`)는 가짜 `asset_ref`와 고정 `cost_krw`를 돌려준다.

### 비용 (공급자 공식 확인값, 2026-10-27 기준 · 변동 가능)
- Gemini Omni Flash: 입력 $1.50/100만 토큰, 영상 출력 $17.50/100만 토큰, 720p는 초당 5,792 출력 토큰.
  → 1초 = 5,792 × $17.50 / 1,000,000 ≈ **$0.101/초**, 8초 ≈ **$0.81/건**(입력 토큰 제외, 1080p 단가 공식 페이지에 없음 `[확인 필요]`).
- MiniMax H3-Lite(저비용), Wan 3.0·Kling 3.0(긴 영상): 단가 `[확인 필요]`.
- Seedance 2.5: BytePlus 유료 API의 **SNS 공개 허용 조항 미확인** → 보류. Sora 2 API: 종료됨 → 제외.
- 원화 환산 환율·월 예산: `(TODO: 추후결정)`.

### 실패·재시도·타임아웃
- 공급자 호출은 `execute()`가 `executed`를 돌려준 **뒤에만**(관문 통과 전 호출 금지).
- 멱등 키 `"{context_id}:generate_video:{post_draft_id}:{prompt_sha256[:10]}"`. 같은 키 재요청은 이전 결과(`completed`만 저장, `coupons.py`와 같은 원칙).
- 타임아웃·5xx: 지수 백오프 재시도 최대 N회(`(TODO)`), 같은 `provider_job_id`로 상태 조회 우선(중복 과금 방지). 재시도마다 비용은 다시 계산해 상한과 비교.
- 끝내 실패: `TaskStatus.FAILED`, `outcome="provider_failed"`, 비용은 공급자 응답에 과금 표시가 있을 때만 기록.

### 생성물 저장
`MARKETING_MEDIA_DIR/<tenant_id>/<context_id>/<video_id>/{raw.mp4, labeled.mp4, meta.json}`. `meta.json`에 프롬프트(사실 필드만)·모델 ID·응답 ID·비용·오버레이 문구·해시. 보관 기간은 개인정보 방침(3개월)과 **별개 자산**이지만 기준을 맞출지 `(TODO)` — 10절.

### 공급자 실패 시 fallback
1. 설정 `MARKETING_VIDEO_FALLBACK=image|hold` `(TODO)`.
2. `image`: 자체 제작 가상 표지·캐릭터 이미지 1장 + 같은 캡션으로 `publish_sns`에 넘기고 `fallback=true` 표시.
3. `hold`: 게시 보류(`input-required`가 아니라 **마케팅 쪽에서 `failed`** 로 끝내고 사람이 다시 요청).
시나리오 A 시연은 영상이 필수가 아니므로 기본은 `image` 제안.

---

## 5. AI 라벨 후처리

- 방침(결정됨): 계정 라벨 + 게시 API `is_ai_generated=true` + 캡션 안내문 + **영상 안 안내문(후처리 오버레이)**. 글만 AI여도 예외 없음.
- 오버레이 도구: **ffmpeg `drawtext`**(또는 `overlay`로 PNG 자막 띄우기) `[확인 필요: 컨테이너 이미지에 ffmpeg·한글 글꼴 포함 여부]`. 생성 모델이 그린 글자에 의존하지 않는다(모델이 한글을 틀릴 수 있고, 검사도 불가).
- 표시 기준(제안값, 체크리스트 3절 그대로): **영상 전 구간**, 읽을 수 있는 크기·배경 대비. 구체 크기·위치·시간은 **설정 파일**(`marketing_agent/.../sns_label.toml` 같은 한 곳)에 두고 코드에 박지 않는다.
- 문구: `[확인 필요 — 팀·변호사 승인]`. 예시 초안만: "이 영상과 글은 AI로 만들었습니다 · 캣북스는 시연용 가상 서비스입니다". 인공지능기본법 제31조·표시광고법 심사지침·과기정통부 가이드라인 원문 확인 전에는 확정하지 않는다.
- 한국어 제목·가격도 같은 오버레이 단계에서 넣는다(사실 필드 → 코드가 문자열 생성).
- `publish_sns`는 `is_ai_generated=true`를 **항상** 보내고, API 응답(컨테이너 ID·게시 ID·응답 본문에 라벨 관련 필드가 있으면 그 값)을 `meta.json`과 결과 `output`에 저장한다. 앱 화면 표시 여부는 **별도로 사람이 확인해 기록**(9절).
- 검증자(`check_sns_post`)는 ① 캡션 안내문 포함 ② `labeled.mp4` 존재·해시 일치 ③ `is_ai_generated` 파라미터 true, 셋 중 하나라도 빠지면 reject.

---

## 6. Instagram Graph API 게시 절차

> 아래 절차는 일반적으로 알려진 Content Publishing API 흐름이다. **게시 어댑터를 켜기 전에 공식 문서로 전부 다시 확인한다**(목록은 이 절 끝). 체크리스트 5절의 "환불 시 쿠폰 처리가 구매자 약관에서 확정되기 전에는 게시하지 않는다"도 그대로 적용.

| 단계 | 호출(개략) | 비고 |
| --- | --- | --- |
| 1 컨테이너 생성 | `POST /{ig-user-id}/media` — 릴스: `media_type=REELS`, `video_url=<공개 URL>`, `caption`, `share_to_feed`, `is_ai_generated=true` / 이미지: `image_url`, `caption`, `is_ai_generated=true` | 캐러셀은 컨테이너에만 라벨(체크리스트 3절) |
| 2 상태 확인 | `GET /{container-id}?fields=status_code` → `IN_PROGRESS`/`FINISHED`/`ERROR`/`EXPIRED` | 폴링 간격·최대 대기 `(TODO)`; `ERROR`·`EXPIRED`면 게시하지 않고 `failed` |
| 3 게시 | `POST /{ig-user-id}/media_publish?creation_id=<container-id>` → `{id}` | 이 호출 **1회만**. 응답 `id`를 멱등 결과로 저장 |
| 4 (선택) 확인 | `GET /{media-id}?fields=permalink,media_type,timestamp` | `permalink`를 결과·공지 저장소에 기록 |

- **Meta 서버가 접근할 수 있는 영상 URL**이 필요하다 → 로컬 볼륨만으로는 안 되고, 공개 HTTPS 호스팅(shop-web 정적 경로·오브젝트 스토리지 등) `[확인 필요 — 어디에 둘지, 누가 소유하는지]`. 서명 URL·만료 시간을 두고, 게시 뒤 원본 URL은 닫을 수 있는지 확인.
- Reel 규격 `[확인 필요]`: 9:16, MP4(H.264/AAC), 길이 하한·상한, 파일 크기 상한, 프레임레이트. 공식 표를 설정으로 옮기고 `check_sns_post`가 ffprobe로 검사.
- 실패 처리: 컨테이너 `ERROR` → 사유 코드 기록 후 `failed`(재시도는 사람 판단). 토큰 만료(190 계열) → `failed`, 사람에게 토큰 재발급 안내(에이전트는 발급 안 함). 전송 뒤 응답 유실 → 같은 `creation_id`로 재게시하지 않고 `GET /{ig-user-id}/media`로 최근 게시물에서 `creation_id`/캡션 해시 대조 `[확인 필요: 대조 가능한 필드]`.
- 게시 한도: 체크리스트가 "24시간 100건 / 50건 어긋남"을 적어 두었다 `[확인 필요]`. 마케팅 설정의 일일 상한은 그보다 훨씬 낮게(`(TODO)`), safety 기준표에도 같은 취지의 규칙 제안(요청 이슈 초안).

### 공식 문서로 확인할 항목
1. `is_ai_generated` 파라미터의 정확한 이름·적용 미디어 종류(릴스·이미지·캐러셀)·응답 필드.
2. 릴스 규격(해상도·길이·크기·코덱)과 `share_to_feed` 기본값.
3. 컨테이너 상태 코드 목록과 만료 시간.
4. 게시 한도(24h)와 개발 모드·앱 검수(권한 `instagram_business_content_publish` 등 `[확인 필요]`) 요건, 테스트 사용자.
5. 개발자 정책 §6.2("게시만을 위한 용도") 해당 여부, 브랜디드 콘텐츠 정책(체크리스트 5절).
6. 계정 단위 AI 라벨("AI-generated profile") 설정 방법.
7. 미디어 URL 접근 요건(공개 HTTPS, 리다이렉트 허용 여부, 서명 URL 가능 여부).

---

## 7. 안전·위험 표

| 위험 | 통제(코드/규칙) | 담당 |
| --- | --- | --- |
| 저작권 — 제3자 책 표지·상표·음원 | 프롬프트는 장면 템플릿 + 사실 필드만. 자산은 **자체 제작 가상 표지·캐릭터**만. `check_sns_post`가 템플릿 외 이미지 참조·외부 URL 거부. 배경음은 무음 또는 라이선스 확인된 것만 `(TODO)` | 마케팅 |
| 초상 — 실존 인물 | 템플릿에 인물 없음(고양이 캐릭터). 프롬프트 금칙어(실명·"닮은"·유명인) 코드 검사 | 마케팅 |
| 개인정보 | 프롬프트·캡션에 `buy_`·주문·문의 원문·연락처를 넣지 않는다(허용 필드 화이트리스트). 로그 `payload`에 원문 금지(레포 규칙) | 마케팅 |
| 허위·과장 | 캡션은 `get_notice` 저장소의 사실 필드·쿠폰 제약 사항에서만 생성. **장애 원인·영향 숫자 금지**(README). 쿠폰은 제약 사항 없으면 reject(체크리스트) | 마케팅 |
| 중복 게시 | 멱등 키 `"{context_id}:publish_sns:{post_draft_id}"`; `media_publish` 1회; 응답 유실 시 재게시 금지 | 마케팅 + safety(동일 키 재요청은 이전 판정) |
| 외부 공개 되돌리기 어려움 | `publish_sns` C(사람 승인) 제안, fail-closed(관문 무응답 → 실행 안 함) | safety |
| 비용 폭주 | 생성 전 추정 비용 vs 건당 상한, 월 누적 상한(마케팅), 일일 누적 기준(safety 기준표 제안) | 마케팅 + safety |
| 계정 보안 | 토큰·키는 `.env`만, 로그·이슈·커밋 금지(플랫폼 약관 6.a.iv). 토큰 입력·재발급은 사람. 장기 토큰 만료 전 알림만 `(TODO)` | 사람 + 마케팅 |
| 프롬프트 주입 | 외부 입력은 `untrusted`로만, 게시 문안 생성에 쓰지 않음(SCN-S 필수 항목과 같은 원칙) | 마케팅 |
| 정책 위반(스팸·조건부) | 참여 조건 문구 금칙어("팔로우하면"·"좋아요 누르면") 코드 검사, 일일 건수 상한, 문구 변주 | 마케팅 |

---

## 8. 구현 순서와 담당

| 단계 | 내용 | 담당 | 완료 기준 |
| --- | --- | --- | --- |
| S0 | 이 문서 검토·열린 결정 1차 합의 | 총괄 준하, 마케팅 재헌, 안전 태윤 | 10절 중 "게시 등급·공급자·라벨 문구 담당" 결정 |
| S1 | 안전 쪽 요청 이슈 등록(`sns-safety-request.md`) | 재헌 | 이슈 번호 발급, 태윤 답변 |
| S2 | `draft_sns_post`(read, 고정 템플릿) + `check_sns_post`(read) + 스키마·테스트. `publish_notice`의 `sns` 채널 거부 | 재헌 (`domains/marketing/` 안) | `uv run pytest domains/marketing` 통과, 필수 문구 누락 시 reject 테스트 |
| S3 | `generate_video` 목 모드 + 공급자 인터페이스 + ffmpeg 오버레이(목에서는 자리표시 영상에 오버레이) | 재헌 | 목에서 `labeled.mp4` 생성·해시 기록, 비용 상한 초과 시 호출 안 함 테스트 |
| S4 | safety 등록(registry·thresholds) — **태윤 폴더** | 태윤 (사람 승인 파일) | `uv run pytest shared/safety` 통과, 미등록 → C 유지 테스트 |
| S5 | `publish_sns` 목 어댑터(`MARKETING_SNS_PROVIDER=mock`), `execute()` 연동, C 보류 → 승인 → 게시 로컬 확인 | 재헌 + 태윤 | 로컬 safety로 held → approve → executed 1회 |
| S6 | 실제 공급자 1개로 `generate_video` 테스트 3건(9절) | 재헌 (사람이 키 입력) | 비용·시간·오버레이 가독성 기록 |
| S7 | Instagram 테스트 계정에 **릴스 1건** 게시(사람 승인) | 재헌 + 사람 | API 응답·앱 화면 라벨 기록 분리 저장 |
| S8 | 이벤트·계약 필요 시 계약 PR(새 이벤트 `sns.published` 등, 추가만) | 계약 소유자 | 계약 호환 검사 통과 |
| S9 | 시연 리허설(시나리오 A 6+ 단계, 이미지 게시) | 준하·재헌 | 연속 3회 성공(SCN-A 기준과 동일) |

### 사람이 해야 하는 준비물 체크리스트
- [ ] Instagram **프로페셔널(비즈니스) 계정** + 연결된 Facebook 페이지 `[확인 필요: 연결 요건]`, 프로필에 "AI 생성·시연용 가상 서비스" 명시, 계정 AI 라벨 설정. 2026-11-03 공유된 `instagram.com/catbooks.lab`이 후보다(운영·테스트 구분은 `(TODO)`)
- [ ] Meta 개발자 앱 생성, 권한 신청, **장기 액세스 토큰**·`ig-user-id` → 서버 `.env`(`MARKETING_IG_ACCESS_TOKEN`·`MARKETING_IG_USER_ID` 이름 제안)
- [ ] Google AI Studio(또는 Vertex) 프로젝트, 결제수단 등록, **지출 한도·예산 알림** 설정, API 키 → `.env`
- [ ] (비교용) MiniMax·Wan 계정·키 — 비교 테스트 때만, 끝나면 폐기 `(TODO)`
- [ ] 영상 공개 호스팅 위치와 소유자 결정, HTTPS 확인
- [ ] 개인정보처리방침 공개 URL·삭제 요청 창구(체크리스트 3절 `[확인 필요]`)
- [ ] 구매자 이용약관 "환불 시 쿠폰 처리" 확정 — 확정 전 게시 금지

---

## 9. 검증 계획

### 9-1. 공식 문서 확인(게시 어댑터 켜기 전, 결과는 체크리스트 5절에 추가)
- Google: `gemini-omni-1.1-flash` GA 상태·가격(720p/1080p)·출력 영상 상업 이용·SNS 공개 허용 조항·워터마크(SynthID 등) 유무 `[확인 필요]`.
- Meta: 6절 "공식 문서로 확인할 항목" 1~7.
- 국내: 인공지능기본법 제31조, 표시광고법 심사지침, 과기정통부 투명성 가이드라인의 표시 방식(문구·위치·크기).

### 9-2. 테스트 릴스 1건 (테스트 계정, 사람 승인 후)
기록은 **두 줄로 따로**:
1. **API 기록**: 컨테이너 요청 파라미터(`is_ai_generated=true` 포함), 컨테이너 응답, `media_publish` 응답 `id`, `GET /{media-id}` 결과 전체 → `meta.json`·공통 로그 `ref_ids`.
2. **앱 화면 기록**: 사람이 Instagram 앱·웹에서 게시물을 열어 AI 라벨이 **보이는지**, 어디에 어떤 문구로 보이는지 스크린샷·시각(KST)·확인자 이름을 기록. API가 true를 받았어도 화면 표시는 별개로 본다.
합격 기준: 1·2 모두 기록되고, 오버레이 안내문이 전 구간에서 읽힌다. 라벨이 화면에 보이지 않으면 원인(권한·미디어 종류)을 확인하기 전까지 추가 게시 금지.

### 9-3. 모델 비교 (동일 프롬프트 3개 × 모델 3개, 8초 · 9:16 · 720p)
- 모델: Gemini Omni Flash(`gemini-omni-1.1-flash`) vs MiniMax H3-Lite vs Wan 3.0. 프롬프트 3개는 장면 템플릿에서 뽑고 사실 필드만 포함(실존 인물·제3자 표지 없음).
- 평가 항목(코드+사람): 생성 시간, 실패율, 비용(실측), 9:16·8초 준수, 오버레이 뒤 가독성, 캐릭터 일관성, 한글 오버레이와의 충돌(모델이 임의 글자를 그렸는지).
- **비용 추정(변동 가능)**: Gemini 8초 720p = 5,792 tok/s × 8 s = 46,336 출력 토큰 × $17.50/1M ≈ **$0.81/건**, 3건 ≈ **$2.43**(입력 토큰 미포함). MiniMax·Wan 단가는 미확인이므로 **9건 전체 ≈ $5.8**은 "건당 평균 약 $0.65"를 가정한 상한 추정이다(Gemini 3건 $2.43 + 나머지 6건 ≈ $3.4 가정). 실제 단가 확인 뒤 다시 계산한다. 재시도 분은 별도.
- 결과는 `backend/docs/notes/marketing/video-provider-comparison.md`에 표로 남기고, 공급자 최종 결정은 팀(10절).

---

## 10. 열린 결정 목록 — 전부 `(TODO: 추후결정)`, 임의 기본값을 채우지 않는다

| # | 결정 | 보기 | 정하는 사람 |
| --- | --- | --- | --- |
| 1 | 영상 생성 **예산**(건당·월) | 원화 상한, 알림 기준 | 준하·태윤(기준표) |
| 2 | **공급자 최종** | Gemini Omni Flash 기본 유지 / MiniMax / Wan / Kling, Seedance는 허용 조항 확인 뒤 | 팀(9-3 결과 뒤) |
| 3 | **게시 등급** | `publish_sns` C 유지 / 조건부 B(검증자 safety 코드 규칙), `generate_video` B / A+기준표 | 태윤(safety) |
| 4 | **AI 라벨 문구·크기·위치** | 법령·가이드라인 확인 뒤 설정 파일 값 | 재헌 초안 → 팀·변호사 |
| 5 | **게시 계정** | 테스트 계정만 / 시연용 공식 계정(`catbooks.lab` 후보), 운영 주체 표기 | 준하·재헌 |
| 6 | **생성물·게시 기록 보관 기간** | 개인정보 방침 3개월에 맞춤 / 자산은 별도(개인정보 아님) / 게시 기록은 감사용으로 더 길게 | 재헌 → 팀 |
| 7 | 영상 공개 호스팅 위치·소유자 | shop-web 정적 / 별도 스토리지 | 준하·재헌 |
| 8 | 새 이벤트(`sns.published`) 추가 여부 | `notice.published(channel_code=sns)` 재사용 / 계약 추가 | 계약 소유자 |
| 9 | `publish_notice`의 `sns` 채널 처리 | 코드 거부 / registry `when` 규칙 | 재헌·태윤 |
