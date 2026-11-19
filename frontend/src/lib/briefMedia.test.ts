// node --test로 돈다 (npm test). 미디어 만들기의 비용 줄·결과 문구가 서버 값만 쓰는지.
import assert from "node:assert/strict";
import { test } from "node:test";
import { clampDuration, durationCostKrw, durationNoteKo, durationProblemKo, estimateBlockKo, estimateLines, gateLongStart, longWarningKo, needsLongWarning, mediaHttpErrorKo, mediaResultKo, publishHttpErrorKo, publishReasonKo, publishResultKo, publishStatusLine, remainingSeconds, remainingKo, canDecideNow, decideErrorKo, approvalLink, audioPending, canChooseAudio, audioIntroKo, bgmCostKo, audioDecisionKo, audioResultKo, audioHttpErrorKo, type AudioState, type PreparedMedia, type MediaEstimate, type PublishState } from "./briefMedia.ts";

const estimate = (over: Partial<MediaEstimate> = {}): MediaEstimate => ({
  status: "ok", reason_code: null, media_kind: "video", cost_krw: 835, settled: false, basis: "price_table",
  per_item_limit_krw: 2000, monthly_budget_krw: 30000, month_total_krw: 1670, ...over,
});

test("비용 줄은 서버가 준 숫자를 그대로 쓴다", () => {
  assert.deepEqual(estimateLines(estimate()), [
    "예상 비용 835원 (단가표 기준 추정)",
    "건당 상한 2,000원",
    "이달 누적 1,670원 / 월 상한 30,000원",
  ]);
});

test("서버가 주지 않은 값은 지어내지 않고 읽을 수 없다고 적는다", () => {
  const lines = estimateLines(estimate({ cost_krw: null, basis: null, per_item_limit_krw: null, monthly_budget_krw: null, month_total_krw: null }));
  assert.deepEqual(lines, ["예상 비용을 계산할 수 없습니다.", "건당 상한 설정을 읽을 수 없음", "이달 누적을 읽을 수 없음 / 월 상한 설정을 읽을 수 없음"]);
  assert.ok(!lines.join(" ").match(/\d+원/));
});

test("이미 만든 미디어는 새 비용이 나지 않는다고 적는다", () => {
  assert.match(estimateLines(estimate({ settled: true, basis: "ledger" }))[0], /새 비용이 나지 않습니다/);
});

test("막힌 이유는 사람이 읽는 문구로, 모르는 코드는 코드만 덧붙인다", () => {
  assert.match(estimateBlockKo("carousel_items_undecided"), /캐러셀/);
  assert.match(estimateBlockKo("budget_exceeded"), /이달 미디어 비용 상한/);
  assert.equal(estimateBlockKo("new_code"), "지금은 만들 수 없습니다 (new_code).");
});

test("만들기 결과: 준비됨·검사 걸림·보류·실패", () => {
  const ready = mediaResultKo({ status: "completed", outcome: null, output: { stage: "draft_ready" } });
  assert.equal(ready.kind, "ready");
  assert.equal(mediaResultKo({ status: "completed", outcome: "already_prepared", output: { stage: "draft_ready" } }).kind, "ready");
  assert.equal(mediaResultKo({ status: "completed", outcome: "rejected_by_check", output: { stage: "check_rejected" } }).kind, "check_rejected");
  const held = mediaResultKo({ status: "input-required", outcome: "held", output: { review_id: "rev_1" } });
  assert.equal(held.kind, "held");
  assert.match(held.text, /사람 확인으로 보류/);
  const over = mediaResultKo({ status: "failed", outcome: "budget_exceeded", output: null });
  assert.equal(over.kind, "failed");
  assert.match(over.text, /상한/);
  assert.match(mediaResultKo({ status: "failed", outcome: "in_progress", output: null }).text, /이미 만드는 중/);
});

test("서버 오류 문장은 화면에 그대로 나오지 않는다", () => {
  const raw = "Traceback: GeminiVideoError 503 UNAVAILABLE https://example.invalid/v1";
  const failed = mediaResultKo({ status: "failed", outcome: "provider_failed", output: { reason: raw } });
  assert.ok(!failed.text.includes("Traceback") && !failed.text.includes("https://"));
  for (const status of [401, 403, 404, 422, 502, 504, 500, undefined]) {
    const text = mediaHttpErrorKo(status);
    assert.ok(text.length > 0 && !text.includes("Traceback"));
  }
  assert.match(mediaHttpErrorKo(504), /두 번 만들지 않습니다/);
});

const publish = (over: Partial<PublishState> = {}): PublishState => ({
  status: "not_requested", can_request: true, reason_code: null, review_id: null, failure_code: null, permalink: null, caption: "글", ...over,
});

test("게시 요청 상태: 요청 전에는 줄이 없고, 대기·게시·거절을 구분한다", () => {
  assert.equal(publishStatusLine(publish()), null);
  assert.equal(publishStatusLine(null), null);
  assert.match(publishStatusLine(publish({ status: "held", can_request: false }))!.text, /10분 안에.*만료/);
  assert.match(publishStatusLine(publish({ status: "publishing", can_request: false }))!.text, /승인됐습니다/);
  assert.equal(publishStatusLine(publish({ status: "published", can_request: false }))!.tone, "ok");
  assert.match(publishStatusLine(publish({ status: "rejected", can_request: false }))!.text, /거절됐거나.*만료/);
  assert.match(publishStatusLine(publish({ status: "failed", failure_code: "content_changed" }))!.text, /내용이 바뀌어/);
  assert.match(publishStatusLine(publish({ status: "failed", failure_code: "new_code" }))!.text, /new_code/);
});

test("게시 요청 결과: 요청됨은 아직 게시되지 않았다고 알리고, 실패는 코드별 문구", () => {
  const ok = publishResultKo({ status: "completed", outcome: "requested", output: null });
  assert.equal(ok.kind, "requested");
  assert.match(ok.text, /아직 게시되지 않았습니다/);
  assert.equal(publishResultKo({ status: "completed", outcome: "already_requested", output: null }).kind, "already");
  for (const code of ["daily_limit_reached", "check_not_passed", "media_kind_unsupported", "adapter_unavailable", "publish_halted", "publish_paused", "publish_control_error"]) {
    const r = publishResultKo({ status: "failed", outcome: code, output: null });
    assert.equal(r.kind, "failed");
    assert.ok(!r.text.includes(code), code);
  }
  assert.equal(publishReasonKo("new_code"), "게시를 요청하지 못했습니다. 잠시 뒤 다시 시도해 주세요 (new_code).");
});

test("자동 게시 꺼짐: 요청 거절·승인 뒤 실패 문구와 사유", () => {
  const paused = publishResultKo({ status: "failed", outcome: "publish_paused", output: { pause_reason: "계정 정지 재고 대기" } });
  assert.equal(paused.kind, "failed");
  assert.match(paused.text, /자동 게시가 꺼져/);
  assert.doesNotMatch(paused.text, /전환 예정/);
  assert.match(paused.text, /초안과 미디어 생성은 계속 쓸 수 있습니다/);
  assert.match(paused.text, /담당자 확인/);
  assert.match(paused.text, /꺼 둔 사유: 계정 정지 재고 대기/);
  // 사유가 없으면 붙이지 않는다. 다른 코드의 pause_reason은 쓰지 않는다
  assert.doesNotMatch(publishResultKo({ status: "failed", outcome: "publish_paused", output: null }).text, /꺼 둔 사유/);
  assert.doesNotMatch(publishResultKo({ status: "failed", outcome: "daily_limit_reached", output: { pause_reason: "x" } }).text, /꺼 둔 사유/);
  assert.match(publishResultKo({ status: "failed", outcome: "publish_control_error", output: null }).text, /스위치를 읽을 수 없어/);
  const failed = publishStatusLine({ status: "failed", can_request: false, failure_code: "publish_paused" } as PublishState)!;
  assert.equal(failed.tone, "bad");
  assert.match(failed.text, /자동 게시가 꺼져 자동으로 올리지 않았습니다/);
  assert.match(failed.text, /수동 게시로 안내/);
  assert.match(publishStatusLine({ status: "failed", can_request: false, failure_code: "publish_control_error" } as PublishState)!.text, /스위치를 읽을 수 없어/);
});

test("게시 요청 연결 오류는 서버 문장 없이 상태별 문구", () => {
  assert.match(publishHttpErrorKo(403), /권한/);
  assert.match(publishHttpErrorKo(404), /지원하지 않/);
  assert.match(publishHttpErrorKo(504), /두 번 만들지 않습니다/);
  assert.match(publishHttpErrorKo(undefined), /다시 시도/);
});

test("남은 시간은 서버가 준 마감만 쓰고 1분 단위로 적는다", () => {
  const now = Date.parse("2030-01-01T00:00:00Z");
  assert.equal(remainingSeconds(null, now), null);
  assert.equal(remainingSeconds("not-a-date", now), null);
  assert.equal(remainingSeconds("2030-01-01T00:10:00Z", now), 600);
  assert.equal(remainingKo(null), "남은 시간을 알 수 없습니다");
  assert.equal(remainingKo(600), "약 10분 남음");
  assert.equal(remainingKo(61), "약 2분 남음");
  assert.equal(remainingKo(30), "1분 미만 남음");
  assert.match(remainingKo(0), /지났습니다/);
});

test("승인 버튼은 확인 체크·이름·마감 전일 때만 켜진다", () => {
  const ok = { tokenSet: true, reviewed: true, approver: "병환", remaining: 300, busy: false };
  assert.equal(canDecideNow(ok), true);
  assert.equal(canDecideNow({ ...ok, reviewed: false }), false);
  assert.equal(canDecideNow({ ...ok, approver: null }), false);
  assert.equal(canDecideNow({ ...ok, tokenSet: false }), false);
  assert.equal(canDecideNow({ ...ok, busy: true }), false);
  assert.equal(canDecideNow({ ...ok, remaining: 0 }), false);
  assert.equal(canDecideNow({ ...ok, remaining: null }), true);
});

test("만료와 사람 거절은 다른 문구, 만료만 다시 요청을 안내한다", () => {
  const expired = publishStatusLine(publish({ status: "expired", can_retry: true }))!.text;
  assert.match(expired, /만료/);
  assert.match(expired, /다시 요청/);
  assert.doesNotMatch(publishStatusLine(publish({ status: "expired", can_retry: false }))!.text, /다시 요청/);
  assert.match(publishStatusLine(publish({ status: "rejected", rejected_cause: "human" }))!.text, /사람이 거절/);
  assert.match(publishStatusLine(publish({ status: "rejected", rejected_cause: "unknown" }))!.text, /구분할 수 없습니다/);
});

test("재요청 거부 코드는 코드별 한국어 안내", () => {
  for (const code of ["retry_not_expired", "retry_rejected_by_human", "retry_cause_unknown", "retry_already_published", "retry_other_held"]) {
    assert.ok(!publishReasonKo(code).includes(code), code);
  }
  assert.match(publishResultKo({ status: "failed", outcome: "retry_rejected_by_human", output: null }).text, /사람이 거절/);
});

test("승인 호출 오류는 본인 승인·이미 결정·권한을 가른다", () => {
  assert.match(decideErrorKo(403, "SELF_APPROVAL"), /본인/);
  assert.match(decideErrorKo(409, "ALREADY_DECIDED"), /이미 결정/);
  assert.match(decideErrorKo(403, "HUMAN_ONLY"), /권한/);
  assert.match(decideErrorKo(404, null), /찾을 수 없/);
});

test("승인 화면 링크는 건을 강조할 뿐 승인 정보를 담지 않는다", () => {
  assert.equal(approvalLink("rev_1"), "/marketing/campaign?review=rev_1");
  assert.equal(approvalLink(null), "/marketing/campaign");
  assert.ok(!/approve|decision|token/i.test(approvalLink("rev_1")));
});

const video = { min_s: 1, max_s: 60, absolute_max_s: 90, current_s: 15, cost_by_duration: { "10": 800, "15": 1200, "40": 3300, "60": 5000 } };

test("영상 길이: 비용은 서버 표 값만 쓰고 표에 없는 길이는 만들 수 없다고 한다", () => {
  const e = estimate({ video, per_item_limit_krw: 4000, monthly_budget_krw: 30000, month_total_krw: 0 });
  assert.equal(durationCostKrw(video, 40), 3300);
  assert.equal(durationCostKrw(video, 2), null);
  assert.equal(durationProblemKo(e, 40), null);
  assert.match(durationProblemKo(e, 60) ?? "", /건당 상한 4,000원을 넘어/);
  assert.match(durationProblemKo(e, 2) ?? "", /만들 수 없습니다/);
  assert.match(durationProblemKo({ ...e, month_total_krw: 29000 }, 40) ?? "", /이달 비용 상한/);
  assert.equal(estimateLines(e, 40)[0], "영상 40초 예상 비용 3,300원 (단가표 기준 추정)");
});

test("영상 길이: 입력은 서버가 알려 준 범위(1~운영자 상한)로 맞추고 상한이 운영자 값임을 적는다", () => {
  assert.equal(clampDuration(video, 200), 60);
  assert.equal(clampDuration(video, 0), 1);
  assert.equal(clampDuration(video, 12.4), 12);
  assert.equal(clampDuration(video, Number.NaN), null);
  assert.match(durationNoteKo(video), /운영자가 서버 설정/);
  assert.match(durationNoteKo(video), /최대 90초/);
  assert.match(estimateBlockKo("video_max_duration_invalid"), /1~90초/);
});

// --- 긴 영상 경고창(화면 안내일 뿐 안전 관문이 아니다) ---

const warnVideo = (over: Partial<NonNullable<MediaEstimate["video"]>> = {}) => ({
  min_s: 1, max_s: 90, absolute_max_s: 90, current_s: 15, warn_duration_s: 30,
  cost_by_duration: { "15": 835, "30": 1700, "31": 1800, "60": 3400, "90": 5100 },
  clips_by_duration: { "15": 1, "30": 2, "31": 3, "60": 5, "90": 7 }, ...over,
});

test("경고창은 경고 길이를 넘을 때만 뜬다(같은 길이는 경고 없음)", () => {
  const v = warnVideo();
  assert.equal(needsLongWarning(v, 15), false);
  assert.equal(needsLongWarning(v, 30), false);
  assert.equal(needsLongWarning(v, 31), true);
  assert.equal(needsLongWarning(v, 90), true);
});

test("서버가 경고 길이를 주지 않으면(옛 서버)·길이가 없으면 경고창을 띄우지 않는다", () => {
  assert.equal(needsLongWarning(warnVideo({ warn_duration_s: undefined }), 90), false);
  assert.equal(needsLongWarning(null, 90), false);
  assert.equal(needsLongWarning(warnVideo(), null), false);
});

test("경고창 문구는 서버 값(길이·클립 수·비용·건당/월 한도)과 쉬운 안내를 담는다", () => {
  const w = longWarningKo(estimate({ video: warnVideo() }), 60);
  const text = w.lines.join(" ");
  assert.match(text, /선택한 길이: 60초/);
  assert.match(text, /이어 붙일 클립 수: 5개/);
  assert.match(text, /예상 비용: 3,400원/);
  assert.match(text, /건당 상한: 2,000원/);
  assert.match(text, /이달 누적 1,670원 \/ 월 상한 30,000원/);
  const notes = w.notes.join(" ");
  assert.match(notes, /장면이나 인물이 이어지지 않을 수 있습니다/);
  assert.match(notes, /클립 수만큼 늘어납니다/);
  assert.match(notes, /건당 상한을 넘으면.*사람 승인/);
  assert.equal(w.confirmLabel, "계속");
  assert.equal(w.cancelLabel, "길이 줄이기");
});

test("경고창은 서버가 주지 않은 값을 지어내지 않는다", () => {
  const w = longWarningKo(estimate({ per_item_limit_krw: null, month_total_krw: null, video: warnVideo({ clips_by_duration: undefined, cost_by_duration: {} }) }), 60);
  const text = w.lines.join(" ");
  assert.match(text, /클립 수를 읽을 수 없음/);
  assert.match(text, /예상 비용을 계산할 수 없습니다/);
  assert.match(text, /건당 상한 설정을 읽을 수 없음/);
});

test("경고 길이 이하면 묻지 않고 바로 보낸다(확인 표시 없음)", async () => {
  const sent: boolean[] = [];
  let asked = 0;
  const ok = await gateLongStart(estimate({ video: warnVideo() }), 30, async () => { asked += 1; return true; }, async (c) => { sent.push(c); });
  assert.equal(ok, true);
  assert.equal(asked, 0);
  assert.deepEqual(sent, [false]);
});

test("경고 길이를 넘고 취소하면 요청이 나가지 않는다", async () => {
  const sent: boolean[] = [];
  const ok = await gateLongStart(estimate({ video: warnVideo() }), 60, async () => false, async (c) => { sent.push(c); });
  assert.equal(ok, false);
  assert.deepEqual(sent, []);
});

test("경고 길이를 넘고 확인하면 요청이 나가고 확인 표시가 붙는다", async () => {
  const sent: boolean[] = [];
  let asked = 0;
  const ok = await gateLongStart(estimate({ video: warnVideo() }), 60, async () => { asked += 1; return true; }, async (c) => { sent.push(c); });
  assert.equal(ok, true);
  assert.equal(asked, 1);
  assert.deepEqual(sent, [true]);
});

// ---- 영상 소리 선택 ----

const audioState = (over: Partial<AudioState> = {}): AudioState => ({ mode: "source", has_source_audio: true, needs_decision: true, decision: null, ...over });
const prepared = (audio: AudioState | null | undefined, kind = "reel"): PreparedMedia => ({
  post_draft_id: "spd_1", stage: "draft_ready", media_kind: kind, asset_ref: "a", asset_sha256: "x", cost_krw: 1000, label_text: "AI 생성",
  check: { verdict: "approved", publishable: true, problems: [] }, prepared_at: null, audio,
});

test("소리를 아직 정하지 않은 영상만 고르는 단계를 보인다", () => {
  assert.equal(audioPending(prepared(audioState())), true);
  assert.equal(audioPending(prepared(audioState({ decision: { choice: "keep", decided_at: null, had_source_audio: true, confirmed_listened: true } }))), false);
  assert.equal(audioPending(prepared(audioState({ mode: "off", has_source_audio: false, needs_decision: false }))), false); // 예전 설정
  assert.equal(audioPending(prepared(null)), false); // 옛 서버·이미지
  assert.equal(audioPending(prepared(undefined)), false);
  assert.equal(audioPending(prepared(audioState(), "image")), false);
  assert.equal(audioPending(null), false);
});

test("게시 요청이 이미 올라간 뒤에는 소리를 바꿀 수 없다", () => {
  const p = prepared(audioState());
  assert.equal(canChooseAudio(p, undefined), true);
  assert.equal(canChooseAudio(p, "not_requested"), true);
  for (const status of ["held", "publishing", "published", "rejected", "expired"]) assert.equal(canChooseAudio(p, status), false);
});

test("소리가 있으면 끝까지 듣고 고르게 하고, 없으면 이유를 알린다", () => {
  const withSound = audioIntroKo(audioState()).join(" ");
  assert.match(withSound, /AI가 만든 소리/);
  assert.match(withSound, /끝까지 들어/);
  assert.match(withSound, /AI 표시/);
  assert.match(audioIntroKo(audioState({ has_source_audio: false, needs_decision: false })).join(" "), /소리가 없습니다/);
  assert.match(audioIntroKo(audioState({ has_source_audio: false, needs_decision: false, note: "clip_concat_muted" })).join(" "), /이어 붙인 영상이라 소리를 뺐습니다/);
});

test("배경음악 추가 비용은 서버가 준 값만 쓴다", () => {
  assert.match(bgmCostKo({ cost_krw: 66, settled: false, reason_code: null }), /추가 비용 약 66원/);
  assert.match(bgmCostKo({ cost_krw: 66, settled: false, reason_code: null }), /영상 비용은 다시 나지 않습니다/);
  assert.match(bgmCostKo({ cost_krw: 66, settled: true, reason_code: null }), /새 비용이 나지 않습니다/);
  assert.match(bgmCostKo({ cost_krw: null, settled: false, reason_code: "price_not_found" }), /계산할 수 없습니다/);
  assert.match(bgmCostKo(undefined), /알려 주지 않았습니다/);
  assert.doesNotMatch(bgmCostKo(null), /\d+원/); // 지어낸 숫자 없음
});

test("정해진 소리 선택은 기록대로 한 줄로 보여 준다", () => {
  assert.match(audioDecisionKo({ choice: "keep", decided_at: "2026-11-19T09:30:00Z", had_source_audio: true, confirmed_listened: true }), /이 영상 그대로 사용 \(2026-11-19 09:30 UTC\)/);
  const bgm = audioDecisionKo({ choice: "bgm", decided_at: null, had_source_audio: true, confirmed_listened: null, bgm_cost_krw: 66 });
  assert.match(bgm, /배경음악을 따로 만들어 입힘/);
  assert.match(bgm, /66원/);
});

test("소리 선택 결과: 그대로 사용·배경음악 완료·보류·실패", () => {
  assert.equal(audioResultKo({ status: "completed", outcome: "audio_kept", output: { stage: "draft_ready" } }, "keep").kind, "kept");
  assert.equal(audioResultKo({ status: "completed", outcome: null, output: { stage: "draft_ready" } }, "bgm").kind, "bgm_ready");
  assert.equal(audioResultKo({ status: "completed", outcome: "rejected_by_check", output: { stage: "check_rejected" } }, "bgm").kind, "check_rejected");
  const held = audioResultKo({ status: "input-required", outcome: "bgm_held", output: { review_id: "r1" } }, "bgm");
  assert.equal(held.kind, "held");
  assert.match(held.text, /영상은 그대로/);
  assert.match(audioResultKo({ status: "failed", outcome: "audio_already_decided", output: null }, "keep").text, /바꿀 수 없습니다/);
  assert.match(audioResultKo({ status: "failed", outcome: "publish_already_requested", output: null }, "keep").text, /게시 요청이 이미/);
  const unknown = audioResultKo({ status: "failed", outcome: "weird_code", output: null }, "bgm");
  assert.equal(unknown.kind, "failed");
  assert.match(unknown.text, /weird_code/);
});

test("소리 선택 연결 오류와 게시 조건 문구는 서버 문장 없이 고정 문구", () => {
  for (const status of [401, 403, 404, 400, 422, 502, 504, 500, undefined]) {
    assert.doesNotMatch(audioHttpErrorKo(status), /Traceback|exception/i);
  }
  assert.match(audioHttpErrorKo(504), /같은 배경음악을 두 번 만들지 않습니다/);
  assert.match(publishReasonKo("audio_decision_required"), /소리를 듣고/);
});
