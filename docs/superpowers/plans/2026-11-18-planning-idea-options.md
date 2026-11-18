# 기획 제안 방향 선택지 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 인사이트를 받으면 기획이 방향 선택지 3개(유행에 올라타기 / 자체 기획 퍼뜨리기 / 우리 상품과 엮기)를 숫자 규칙으로 계산해 "추천"을 붙이고, 사람이 하나를 고르면 그 방향으로 할 일·이유·목표·형식을 쓴다.

**Architecture:** 선택지·후보·추천은 순수 함수 모듈 `planning_agent/idea_options.py`가 계산한다(LLM 없음, 같은 입력 같은 결과). `receive_insight`가 받을 때 데이터분석 트렌드 목록과 마케팅 팔로워 수를 잠금 밖에서 읽어 선택지를 제안에 저장한다. 새 사람 버튼 `choose_angle`(잠금 밖 액션)이 LLM 초안과 재무 제작비 묻기를 하고 결과를 제안에 쓴다. 화면은 기획 제안 상세 "2 기획 판단"에 선택지 버튼을 그린다.

**Tech Stack:** Python 3.12 · pydantic · pytest (uv workspace `backend/`), React 19 + TypeScript (`frontend/`)

**Spec:** `docs/superpowers/specs/2026-11-18-planning-idea-options-design.md`

## Global Constraints

- 계약(`backend/shared/contracts`)은 추가만 한다. 새 칸은 모두 기본값이 있는 선택 칸
- 숫자는 다른 부서 응답 값 그대로 옮긴다. 기획이 넣는 숫자는 목표치 하나(`number_warnings` 검사 유지)
- `domains`끼리 import 금지. 다른 부서는 `call_agent`(A2A)로만
- LLM 모델 이름 하드코딩 금지. LLM이 없거나 실패하면 규칙 초안
- 안전규칙 파일(registry.toml·thresholds.toml·gate.py)은 바꾸지 않는다. `choose_angle`은 `edit_proposal`처럼 기획 자체 기록만 바꾸는 사람 버튼이라 관문을 새로 등록하지 않는다(`PROPOSAL_ACTIONS`의 기존 사람 버튼과 같은 짜임)
- 갈래 id: `ride_trend` "유행에 올라타기" · `own_trend` "자체 기획 퍼뜨리기" · `with_product` "우리 상품과 엮기"
- 추천 기준 기본값(설계가 만드는 사람 몫으로 남긴 것, 이 계획의 결정): 팔로워 `10000`명 미만, 지난 캠페인 조회 중앙값 `10000` 미만이면 "작음". 환경 변수 `PLANNING_SMALL_FOLLOWERS`·`PLANNING_SMALL_VIEWS`로 바꿀 수 있다(회사 설정 화면은 이번 범위 밖)
- 올라탈 후보 최대 3개
- 방향을 안 골라도 확정(`make_plan`·`approve_proposal`)은 막지 않는다
- 화면 문구는 기존 기획 화면 말투, AXcellent 공용 클래스만
- 커밋 작성자 `NYejin <kidjin36@gmail.com>`, Conventional Commits, 긴 메시지는 `git commit -F`

## Review Focus

1. 데이터분석·마케팅이 꺼져 있거나 응답이 늦을 때(목 모드 포함): 받기가 실패하지 않고 선택지는 "확인할 수 없음"으로 만들어진다 → Task 4 `test_receive_without_data_or_marketing_still_builds_options`
2. 11/18 이전 제안(`options` 없음): 화면·확정·브리프가 지금 그대로 → Task 4 `test_old_proposal_without_options_loads`, Task 6 `test_brief_without_angle_is_unchanged`
3. 막힌 선택지를 고르거나 확정된 제안에 `choose_angle`: 상태를 안 바꾸고 이유를 돌려준다 → Task 5 `test_choose_blocked_or_closed_is_refused`
4. 같은 방향을 두 번 눌러도 재무를 두 번 묻지 않고 근거가 겹치지 않는다 → Task 5 `test_choose_twice_does_not_duplicate_finance_facts`
5. 후보 조회수가 LLM 글에 섞여도 숫자 검사는 후보 숫자를 근거로 허용하고 다른 숫자만 경고한다 → Task 5 `test_candidate_numbers_count_as_facts`

---

### Task 1: 계약·상태 칸 추가

**Files:**
- Modify: `backend/shared/contracts/src/ax_contracts/schemas/planning.py` (`InsightPast`, `CampaignBriefInput`)
- Create: `backend/shared/contracts/src/ax_contracts/schemas/marketing_channels.py`
- Modify: `backend/shared/workflow-core/src/ax_workflow/models.py` (`Proposal` 앞에 새 모델, `Proposal`에 칸)
- Test: `backend/shared/contracts/tests/test_planning_idea_contracts.py`

**Interfaces:**
- Produces (contracts):
  - `InsightPast.views: int | None = Field(default=None, ge=0)`, `InsightPast.views_at: UtcDatetime | None = None`
  - `CampaignBriefInput.idea_angle: Literal["ride_trend","own_trend","with_product"] | None = None`, `CampaignBriefInput.trend_refs: list[str] = Field(default_factory=list, max_length=3)`
  - `marketing_channels.ChannelFollowers(channel: str, followers: int >= 0, at: UtcDatetime | None)`, `ChannelFollowersOutput(status: Literal["known","unknown"], channels: list[ChannelFollowers] = [], reason: str | None = None)`, 상수 `CHANNEL_FOLLOWERS_SKILL = "channel_followers"`
- Produces (models.py, 모두 `DomainModel`):
  - `IdeaAngle = Literal["ride_trend","own_trend","with_product"]`
  - `TrendRef(topic: str, median_views: float | None, at: UtcDatetime | None)`
  - `ScaleFacts(followers: int | None, followers_channel: str | None, past_views_median: float | None, past_views_count: int = 0)`
  - `IdeaOption(id: IdeaAngle, label: str, available: bool, blocked_reason: str | None = None, refs: list[TrendRef] = [], recommended: bool = False, reason: str | None = None)`
  - `Proposal.options: list[IdeaOption] = []`, `Proposal.scale: ScaleFacts | None = None`, `Proposal.angle: IdeaAngle | None = None`

- [ ] **Step 1: Write the failing test** — `test_new_fields_default_and_old_payload_still_parses`: `InsightPast(campaign_id="c").views is None`; 기존 필수 칸만 준 `CampaignBriefInput`은 `idea_angle is None and trend_refs == []`; `ChannelFollowersOutput(status="unknown").channels == []`; `trend_refs` 4개면 `ValidationError`
- [ ] **Step 2: Run** `cd backend && uv run pytest -q shared/contracts/tests/test_planning_idea_contracts.py` → FAIL (칸 없음)
- [ ] **Step 3: 칸·모델 추가** (위 Interfaces 그대로, 주석 "11/18 기획 방향 선택지")
- [ ] **Step 4: Run** 같은 명령 + `uv run pytest -q domains/planning` → PASS
- [ ] **Step 5: Commit** `feat(contracts,planning): 기획 방향 선택지 칸을 추가한다`

### Task 2: 선택지 계산 모듈

**Files:**
- Create: `backend/domains/planning/src/planning_agent/idea_options.py`
- Test: `backend/domains/planning/tests/test_idea_options.py`

**Interfaces:**
- Consumes: Task 1 모델, `ax_contracts.schemas.data.TrendInsight`, `ReceiveInsightInput`, `ChannelFollowersOutput`
- Produces:
  - `ANGLE_LABEL: dict[IdeaAngle, str]`
  - `small_followers() -> int`, `small_views() -> int` (환경 변수, 기본 10000)
  - `trend_candidates(inp: ReceiveInsightInput, trends: list[TrendInsight]) -> list[TrendRef]`
  - `scale_facts(past: list[InsightPast], followers: ChannelFollowersOutput | None) -> ScaleFacts`
  - `build_options(inp: ReceiveInsightInput, trends: list[TrendInsight] | None, scale: ScaleFacts) -> list[IdeaOption]` — 항상 3개, 순서 `ride_trend, own_trend, with_product`

규칙(설계 1·3절):
- 후보: `t.shorts_format == inp.shorts_format`(인사이트 형식이 없으면 후보 없음) and `t.ad_material is True` and `t.real_entity is False` and `t.insight_id != inp.insight_id` and `t.topic != inp.title`, `score` 내림차순 최대 3개. `TrendRef(topic=t.topic, median_views=t.shorts_format_median_views, at=t.shorts_format_at)`
- `ride_trend` 막힘 이유: 형식 없음 "형식 정보 없음" / `trends is None` "트렌드 목록을 받지 못함" / 후보 0 "지금 뜨는 후보 없음"
- `with_product` 막힘: `inp.products`가 비면 "연결된 우리 상품 없음"
- 팔로워: `status == "known"` 채널 중 가장 큰 값과 그 채널. 지난 조회: `views`가 있는 `past`의 중앙값(`statistics.median`)과 개수
- 추천: ① `ride_trend` 가능 and (팔로워 < 기준 or 조회 중앙값 < 기준) ② 아니고 `with_product` 가능하면 그것 ③ 아니고 규모 숫자가 하나라도 있으면 `own_trend` ④ 숫자가 하나도 없으면 추천 없음
- 추천 이유는 코드 문장. 예: `"팔로워 300(instagram) · 지난 캠페인 조회 중앙값 1,200(1건) → 새로 퍼뜨리기 어려움"`, 모르는 값은 `"확인할 수 없음"`, ④는 `"규모 숫자를 받지 못해 추천 없음"`(추천 없는 선택지 공통 안내로 `own_trend.reason`에)

- [ ] **Step 1: Write failing tests**
  - `test_candidates_filter_format_ad_entity_self`: 통과 2 + 다른 형식 1 + `ad_material=None` 1 + `real_entity=True` 1 + 자기 자신 1 → 2개, score 순
  - `test_small_scale_recommends_ride_trend`: 팔로워 300, 후보 있음 → `ride_trend.recommended`, 이유에 `"300"`
  - `test_one_number_is_enough`: 팔로워 없음·조회 중앙값 50000·상품 있음 → `with_product` 추천
  - `test_no_scale_numbers_no_recommendation`
  - `test_blocked_reasons` (형식 없음 / `trends=None` / 상품 없음)
  - `test_same_input_same_output`
  - `test_followers_use_largest_channel`, `test_past_median_ignores_missing_views`
- [ ] **Step 2: Run** `uv run pytest -q domains/planning/tests/test_idea_options.py` → FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** → PASS, `uv run ruff check domains/planning`
- [ ] **Step 5: Commit** `feat(planning): 방향 선택지와 추천을 숫자 규칙으로 계산한다`

### Task 3: 데이터분석 트렌드·마케팅 팔로워 읽기

**Files:**
- Modify: `backend/domains/planning/src/planning_agent/proposals.py` (`DataInsights`, `MarketingBrief`, `FinanceBudget.ask`, `_past`)
- Test: `backend/domains/planning/tests/test_proposals_sources.py`

**Interfaces:**
- Produces:
  - `DataInsights.trends(ctx: str) -> list[TrendInsight] | str` — `trend_insight_brief`를 불러 주제 목록 그대로(기존 `_brief`와 호출 부분을 나눠 같이 쓴다). 실패는 이유 문자열
  - `MarketingBrief.followers(ctx: str) -> ChannelFollowersOutput | str` — 마케팅 `channel_followers`(read). 아직 skill이 없으면 이유 문자열
  - `FinanceBudget.ask(ctx, category: str | None, fmt, proposal_id)` — `category=None` 허용
  - `_past`: 데이터분석 응답에 조회수 숫자 칸이 있으면 `views`·`views_at`에 옮긴다(칸 이름은 재현 님 답에 맞춤, 없으면 `None`)

- [ ] **Step 1: Write failing tests** (가짜 caller)
  - `test_trends_returns_topics_or_reason`
  - `test_followers_unknown_skill_is_reason`
  - `test_finance_ask_without_category` (요청 input의 `content_category is None`)
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** `uv run pytest -q domains/planning` → PASS
- [ ] **Step 5: Commit** `feat(planning): 트렌드 목록·팔로워 수를 읽어 온다`

### Task 4: 받을 때 선택지 저장

**Files:**
- Modify: `backend/domains/planning/src/planning_agent/domain/proposal_actions.py` (`receive_insight`)
- Test: `backend/domains/planning/tests/test_proposal_angles.py`

**Interfaces:**
- Consumes: Task 2·3
- Produces: 새 제안의 `options`(3개)·`scale`·`angle=None`. 할 일·이유는 지금처럼 `ext.write` 초안

방법: `draft`를 만드는 자리(잠금 밖, 중복·최근 삭제 확인 뒤)에서 `self._insights.trends(ctx)`와 `self._marketing.followers(ctx)`를 `asyncio.gather`로 부른다(각각 `None`이면 부르지 않음). 문자열(실패)은 `None`으로 바꿔 `build_options`에 넘긴다. 활동 기록 문장에 `추천 {라벨}` 또는 `추천 없음`을 붙인다.

- [ ] **Step 1: Write failing tests**
  - `test_receive_stores_three_options_with_recommendation`
  - `test_receive_without_data_or_marketing_still_builds_options` (`_svc()` → 3개, `ride_trend.available is False`, 추천 없음)
  - `test_duplicate_does_not_call_sources` (같은 인사이트 두 번 → trends 1번)
  - `test_old_proposal_without_options_loads`
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** `uv run pytest -q domains/planning` → PASS
- [ ] **Step 5: Commit** `feat(planning): 인사이트를 받으면 방향 선택지를 만든다`

### Task 5: 사람 버튼 `choose_angle`

**Files:**
- Modify: `backend/domains/planning/src/planning_agent/proposals.py` (`ProposalWriter.draft_for_angle`, `rule_draft_for_angle`, `write_for_angle`)
- Modify: `backend/domains/planning/src/planning_agent/domain/proposal_actions.py` (`OUTSIDE_LOCK`, `dispatch_proposal`, `choose_angle`)
- Test: `backend/domains/planning/tests/test_proposal_angles.py`

**Interfaces:**
- Produces:
  - `ProposalWriter.draft_for_angle(p: Proposal, option: IdeaOption, context_id: str) -> ProposalDraft | None` — 기존 `WRITE_PROMPT`에 방향 지시 한 줄 + 후보 목록(`untrusted`). 후보 주제 이름만 쓰고 새 주제·숫자를 만들지 않는다
  - `rule_draft_for_angle(p: Proposal, option: IdeaOption) -> ProposalDraft` — 할 일 `"{라벨}: {제목}"`(ride면 `" · 후보 {topic, …}"`), 이유 = 근거 두 줄 + 추천 이유
  - `write_for_angle(writer, p, option, ctx) -> ProposalDraft` — 초안 + `number_warnings`(근거 = 제안 facts + 후보 `median_views`)
  - `ProposalMixin.choose_angle(self, proposal: str, angle: str) -> ActionResult` — `OUTSIDE_LOCK`에 `"choose_angle"`, `dispatch_proposal`이 `action["proposal"]`·`action["angle"]`로 부른다. 화면 액션 `chooseAngle`
- 동작:
  - 거절(`ActionError`, 상태 그대로): 제안 없음 `NOT_FOUND`, `status != "확인 대기"` `PROPOSAL_CLOSED`, 모르는 angle·`available False` `ANGLE_BLOCKED`(막힌 이유 그대로)
  - 잠금 밖: `write_for_angle`(30초) + 재무 `FinanceBudget.ask(ctx, p.content_category, p.content_format, p.id)`(이미 `source == ext.FINANCE_ADVICE` 근거가 있으면 묻지 않음, 실패는 활동 기록만)
  - `_apply` 안: 다시 찾아 상태 확인 → `angle`·`idea`·`why`·`target`·`format`·`written_by`·`warnings` 갱신, 재무 근거 덧붙임, 활동 기록 `"{pid} 방향 선택: {라벨}"`(kind `사람`, point `choose_angle`)

- [ ] **Step 1: Write failing tests**
  - `test_choose_writes_draft_for_angle`
  - `test_choose_falls_back_to_rule_draft`
  - `test_choose_blocked_or_closed_is_refused`
  - `test_choose_asks_finance_once_with_proposal_id`, `test_choose_twice_does_not_duplicate_finance_facts`
  - `test_candidate_numbers_count_as_facts`
  - `test_confirm_without_angle_still_allowed`
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** `uv run pytest -q domains/planning` → PASS, ruff
- [ ] **Step 5: Commit** `feat(planning): 방향을 고르면 그 방향으로 기획 판단을 쓴다`

### Task 6: 마케팅 브리프에 방향 싣기

**Files:**
- Modify: `backend/domains/planning/src/planning_agent/domain/proposal_actions.py` (`_spawn_brief`)
- Test: `backend/domains/planning/tests/test_proposal_angles.py`

**Interfaces:**
- Produces: `CampaignBriefInput.idea_angle = p.angle`, `trend_refs = [r.topic for r in 고른 선택지 refs]`(`ride_trend`일 때만)

- [ ] **Step 1: Test** `test_brief_carries_angle_and_trend_refs`(`_svc(marketing=Brief())` → 받기 → `ride_trend` 고르기 → `make_plan`·`approve_proposal` → `_flush` → `idea_angle == "ride_trend"`, `trend_refs` 일치), `test_brief_without_angle_is_unchanged`
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** `uv run pytest -q domains/planning shared/contracts` → PASS
- [ ] **Step 5: Commit** `feat(planning): 마케팅 브리프에 고른 방향과 올라탈 후보를 싣는다`

### Task 7: 화면

**Files:**
- Modify: `frontend/src/lib/proposals.ts` (`Proposal`에 `options`·`scale`·`angle`, 타입 `IdeaAngle`·`IdeaOption`·`TrendRef`·`ScaleFacts`, 액션 `{ type: 'chooseAngle'; proposal: string; angle: IdeaAngle }`)
- Modify: `frontend/src/lib/planning.ts` (제안 매핑: `options ?? []`, `angle ?? undefined`, `scale ?? undefined`, 후보 시각 `kst`)
- Modify: `frontend/src/components/planning/proposals.tsx` ("2 기획 판단" 맨 위 선택지 블록)
- Modify: `frontend/src/components/planning/planning-ui.css` (`pp-` 접두어 몇 줄)

화면(설계 6절):
```
이 인사이트를 어떤 방향으로 쓸까요?            방향 선택 대기 | 고른 방향: 유행에 올라타기
[ 유행에 올라타기 · 추천 ]  올라탈 후보: ○○ (중앙값 조회 ○○만, 11-18 기준)
                            추천 이유 문장
[ 자체 기획 퍼뜨리기 ]
[ 우리 상품과 엮기 ]  (막힘: 연결된 우리 상품 없음)
```
- `p.options`가 비면 블록을 그리지 않는다(예전 제안)
- 확인 대기일 때만 버튼. 막힌 선택지는 `disabled` + 이유
- 고른 방향은 `aria-pressed`. `p.writtenBy === 'human'`인데 다른 방향을 누르면 `window.confirm('고친 글을 새 방향 글로 바꿀까요?')`
- 누르면 `d({ type: 'chooseAngle', proposal: p.id, angle })` + 토스트 `"{id} 방향: {라벨}"`

- [ ] **Step 1:** 타입·매핑·액션 → `cd frontend && npm run typecheck` → PASS
- [ ] **Step 2:** 블록 → `npm run typecheck && npx vite build && npm test` → PASS
- [ ] **Step 3:** 3090 라이브 체크아웃에서 실제 모드로 기획 제안 화면 확인(예진 님 규칙: UI는 3090 미리보기 먼저) — 선택지·막힘·추천·고르기, 콘솔 오류 없음
- [ ] **Step 4: Commit** `feat(frontend): 기획 제안에 방향 선택지를 보인다`

### 마무리

- [ ] `cd backend && uv run pytest -q domains/planning shared/contracts shared/workflow-core`, `uv run ruff check`, 바꾼 파일 `uv run ruff format --check`
- [ ] PR 하나(`feat/planning-idea-options`), 본문에 설계·계획 링크와 다른 부서 부탁 상태(재현: 형식 거르기 확인·`InsightPast.views`, 재헌: `channel_followers`·`idea_angle`/`trend_refs` 사용), 올린 뒤 자동 머지 예약
- [ ] 머지 뒤 재헌 님께 `channel_followers` 계약(`marketing_channels.py`)과 브리프 새 칸을 태그 새 글로 알림
