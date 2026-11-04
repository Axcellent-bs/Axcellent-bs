# [요청 마케팅→safety] SNS 게시 등급 요청 초안

> 상태: **이슈 등록 전 초안**(2026-10-27 작성, 2026-11-03 이 레포 기준으로 정리). 설계는 `backend/docs/notes/marketing/sns-design.md`.
> 형식: 이 레포의 `[요청 X→Y]` 이슈 본문 형식(상황·요청·묻는 것·완료 기준)을 따른다. 이슈로 올릴 때 아래 "이슈 본문"을 그대로 쓴다.
> 등록 전 확인: 열린 `[요청 …→safety]` 이슈 중 같은 내용이 있으면 새로 만들지 않고 댓글로 단다.

**제목**: `[요청 마케팅→safety] SNS 게시(publish_sns) 등급 등록과 판정 기준`
**요청자**: 재헌 (marketing)

---

## 이슈 본문

### 상황
- 마케팅이 Instagram 게시(공지 재게시·공개 쿠폰 릴스)를 준비하고 있어요. 상태를 바꾸는 액션 2개를 `backend/shared/safety/src/ax_safety/rules/registry.toml`에 등록하고, 아래 C 판정 조건을 `thresholds.toml`에 둘 수 있는지 봐 주세요. `shared/safety/`와 기준표는 마케팅 폴더가 아니고 사람 승인 파일이라 이슈로 남겨요. **값은 전부 제안이고 최종 등급·기준은 safety가 정해요.**
- 지금 `publish_notice`는 `channel_code="sns"`여도 **A로 바로 실행**돼요(레지스트리에 채널 구분이 없음). SNS는 외부 공개이고 되돌리기 어려워 같은 등급으로 두면 안 돼요. 그래서 SNS 게시를 **별도 액션 `publish_sns`** 로 분리하고, 마케팅 쪽에서 `publish_notice`의 `sns` 채널은 거부하도록 바꿀 예정이에요.
- 영상 생성은 외부 유료 API를 부르므로 비용이 나가요. 미등록이면 C로 보류되는 건 알지만, 라이브에서 돌리려면 등록이 필요해요.

### 요청: 등록 제안 (2개)

| action | owner | base_grade(제안) | verifier(제안) | 이유 |
|---|---|---|---|---|
| `publish_sns` | marketing | **C** | — | 외부 고객 대상 공개·되돌리기 어려움. "되돌릴 수 없는 영역"에 "외부 고객 대상 발송"이 있어 1차는 항상 사람 승인. 규칙이 검증되면 B(검증자 safety 코드 규칙)로 내리는 건 사람 결정 |
| `generate_video` | marketing | **B** | `safety`(코드 규칙) | 돈이 나가지만 되돌릴 수 있음. 교차검증할 다른 부서 skill이 없어 코드 규칙이 적합. 대안: A + 비용 기준표(`issue_coupon` 방식) |

`ai-influencer-pipeline.md`의 `produce_short`(B)·`publish_short`(C)는 이 둘로 **통합**해요(별도 등록하지 않음).

### 마케팅이 보낼 params (제안, 코드가 계산해 보냄)

`publish_sns`
| 필드 | 뜻 |
|---|---|
| `platform` | `"instagram"` (이후 확장 대비) |
| `media_kind` | `"image"` / `"reel"` |
| `post_draft_id` | 초안 ID(승인 대상 문안의 기준) |
| `caption_sha256` | 승인된 초안 캡션의 해시. 게시 직전 캡션과 다르면 아래 `text_mismatch_count` ≥ 1 |
| `is_ai_generated` | 항상 `true`로 보냄. 코드가 false/누락이면 `ai_label_missing_count` ≥ 1 |
| `ai_label_missing_count` | 캡션 안내문·영상 오버레이·API 파라미터 3곳 중 빠진 수(0이 정상) |
| `text_mismatch_count` | 승인 문안과 다른 항목 수(0이 정상) |
| `constraint_missing_count` | 쿠폰 게시물의 필수 제약 사항 누락 수(공지 게시물은 0) |
| `context_id` / `idempotency_key` | `ctx_inc_…`(장애 공지) 또는 `ctx_cmp_…`(캠페인) / `"{context_id}:publish_sns:{post_draft_id}"` |

`generate_video`
| 필드 | 뜻 |
|---|---|
| `post_draft_id`, `provider`, `model_id` | 환경변수에서 읽은 값(기록용) |
| `duration_s`, `resolution`, `aspect_ratio` | 8 / `"720p"` / `"9:16"` |
| `cost_krw` | 코드가 공급자 단가표로 계산한 추정 비용(정수 원) |
| `claimed_cost_krw` *(선택)* | 요청 쪽 값. 다르면 `amount_mismatch` C — `issue_coupon`의 `claimed_fields` 패턴 |
| `idempotency_key` | `"{context_id}:generate_video:{post_draft_id}:{prompt_hash}"` |

### C 판정 조건 제안 (`thresholds.toml`, 값은 제안 — 사람 승인 파일이라 기준만 적어요)

| key(제안) | action | metric | field | op | value | 뜻 |
|---|---|---|---|---|---|---|
| `sns_ai_label_missing` | `publish_sns` | param | `ai_label_missing_count` | `>` | 0 | AI 라벨 하나라도 빠지면 C(B로 내려도 유지) |
| `sns_text_mismatch` | `publish_sns` | param | `text_mismatch_count` | `>` | 0 | 승인된 문구와 다르면 C |
| `sns_constraint_missing` | `publish_sns` | param | `constraint_missing_count` | `>` | 0 | 쿠폰 제약 사항 누락이면 C |
| `sns_daily_posts` | `publish_sns` | **일일 건수** | — | `>=` | `(TODO)` | 일일 게시 한도 초과 → **질문 1** |
| `video_cost_single` | `generate_video` | amount | (`amount_field = "cost_krw"`) | `>=` | `(TODO)` | 건당 비용 상한 |
| `video_cost_daily` | `generate_video` | daily_total | — | `>=` | `(TODO)` | 일일 누적 비용 상한 |
| `video_duration` | `generate_video` | param | `duration_s` | `>` | `(TODO)` | 길이 상한 |

필드가 없거나 정수가 아니면 걸린 것으로(fail-closed) — 기존 규칙 주석과 같은 원칙으로 부탁해요.

### 정해 주세요 (질문)
1. **일일 게시 건수 한도**: 지금 `metric`은 금액 기준(`amount`·`daily_total`…)과 `param`뿐이라 "같은 액션의 오늘 실행 **건수**"를 세는 종류가 없는 것으로 읽었어요. 새 metric(가칭 `daily_count`)을 추가하실지, 마케팅이 `today_post_count`를 `param`으로 보내면 될지(요청 값을 믿는 셈이라 약해요).
2. **`publish_sns`의 `HoldReason`**: 금액 기준이 없는 항상-C는 지금 `over_threshold`로 나오는지, `high_impact` 같은 이유가 생겼는지.
3. **`generate_video` 등급**: B(검증자 safety 코드 규칙) vs A+비용 기준표 중 어느 쪽이 관문 구현에 맞는지. B면 `verifier = "safety"`일 때 검증 skill 없이 규칙만 돌리는 방식이 지금 관문에서 지원되는지(초안을 쓸 때는 `generate_pr`가 그 선례였는데 지금 레지스트리에는 항목이 없어요).
4. **`publish_notice`의 `sns` 채널**: 마케팅 코드에서 거부할 예정인데, safety 쪽에서도 `when = { channel_code = "sns" }` 규칙으로 막아 두는 게 나은지(이중 방어).
5. **사람이 읽을 보류 설명**: C 보류 건 `summary`(예: `SNS 게시 요청: 정상화 공지 ntc_0003 → instagram/image`)를 `params.summary`로 보내면 대시보드에 보이는지.

### 하지 않는 것
- 마케팅이 등급을 정하거나 `shared/safety/`·`thresholds.toml`을 직접 고치지 않아요.
- `execute()`가 `executed`를 돌려주기 전에 Instagram·영상 공급자 API를 부르지 않아요.
- 자동 DM·댓글 수집·스토리·참여 조건부 이벤트는 범위 밖이에요.
- 계정 생성·토큰 발급·결제수단 등록은 사람이 해요.

### 시연 일정 영향
- 시나리오 A 필수 흐름(배너 공지·쿠폰)에는 영향 없어요. SNS 게시는 **선택 단계(6+)** 이고 영상은 시나리오 필수가 아니에요.
- 등록 전에도 미등록 → C로 사람 승인을 거쳐 시연은 가능하지만, 보류 사유가 `unregistered_action`으로 찍혀 "규칙으로 판정했다"는 설명이 약해져요. 리허설 전 등록을 희망해요 `(일정 TODO)`.

### 관련 문서
- `backend/docs/notes/marketing/sns-design.md`
  - 흐름: 공지·쿠폰 → `draft_sns_post`(read) → `generate_video`(B 제안, 선택) → `check_sns_post`(read, 코드 규칙) → `publish_sns`(`execute()`) → C면 사람 승인 → Graph API 컨테이너 생성·상태 확인·게시 → `execution_id`·게시 ID 기록.
  - AI 라벨: 계정 라벨 + `is_ai_generated=true` + 캡션 + 영상 후처리 오버레이(ffmpeg), 문구는 팀 승인 대기.
  - 공급자: 기본 Gemini Omni Flash(`gemini-omni-1.1-flash`, GA, 720p 8초 ≈ $0.81 추정), 환경변수 교체형, 예산·최종 공급자는 `(TODO: 추후결정)`.
- `backend/docs/notes/marketing/instagram-policy-checklist.md` — 공개 쿠폰·제약 사항 필수·시연용 표시·약관 안내.

### 완료 기준
- [ ] `publish_sns`·`generate_video` 레지스트리 등록(owner = marketing), 등급은 safety 결정
- [ ] 위 C 조건 중 채택할 규칙과 값 범위(값은 사람 PR)
- [ ] 질문 1~5 답변
