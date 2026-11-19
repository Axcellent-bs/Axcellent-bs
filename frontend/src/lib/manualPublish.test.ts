// node --test로 돈다 (npm test). 수동 게시: 링크 정규화·문구·버튼 조건·상태줄(링크).
import assert from "node:assert/strict";
import { test } from "node:test";
import { publishResultKo, publishStatusLine, type PublishState } from "./briefMedia.ts";
import {
  MANUAL_LIMIT_NOTE,
  MANUAL_STEPS,
  canRecordManual,
  manualCaptionText,
  manualRecordErrorKo,
  normalizeInstagramPermalink,
  permalinkProblemKo,
  safePermalink,
  type ManualPackage,
} from "./manualPublish.ts";

const OK = "https://www.instagram.com/p/CxAbc123_-/";

test("링크 정규화: 쿼리·프래그먼트 제거, http는 https, www 통일, 끝 슬래시", () => {
  assert.equal(normalizeInstagramPermalink(OK), OK);
  assert.equal(normalizeInstagramPermalink("http://instagram.com/reel/CxAbc123/"), "https://www.instagram.com/reel/CxAbc123/");
  assert.equal(normalizeInstagramPermalink("https://www.instagram.com/tv/CxAbc123"), "https://www.instagram.com/tv/CxAbc123/");
  assert.equal(normalizeInstagramPermalink("  https://www.instagram.com/p/CxAbc123/?igsh=a&x=1#f "), "https://www.instagram.com/p/CxAbc123/");
});

test("링크 정규화: 비었거나 다른 도메인·모양이면 거절", () => {
  for (const bad of [
    "", "   ", "CxAbc123", "https://example.com/p/CxAbc123/", "https://instagram.com.evil.com/p/CxAbc123/",
    "https://m.instagram.com/p/CxAbc123/", "https://user:pw@www.instagram.com/p/CxAbc123/",
    "https://www.instagram.com:8443/p/CxAbc123/", "ftp://www.instagram.com/p/CxAbc123/", "https://www.instagram.com/",
    "https://www.instagram.com/stories/a/123/", "https://www.instagram.com/p/ab/", "https://www.instagram.com/p/Cx$Abc123/",
    "https://www.instagram.com/p/CxAbc123/extra/", "javascript:alert(1)",
  ]) {
    assert.equal(normalizeInstagramPermalink(bad), null, bad);
  }
  assert.equal(normalizeInstagramPermalink(undefined), null);
  assert.equal(normalizeInstagramPermalink(42), null);
  assert.equal(safePermalink("javascript:alert(1)"), null);
});

test("링크 안내 문구: 비면 필수 안내, 틀리면 형식 안내, 맞으면 없음", () => {
  assert.match(permalinkProblemKo("")!, /입력해 주세요/);
  assert.match(permalinkProblemKo("  ")!, /입력해 주세요/);
  assert.match(permalinkProblemKo("https://example.com/x")!, /형식이 아닙니다/);
  assert.equal(permalinkProblemKo(OK), null);
});

test("'게시 완료로 기록' 버튼은 링크가 유효하고 AI 표시를 확인했을 때만 켠다", () => {
  const base = { permalink: OK, labelChecked: true, tokenSet: true, busy: false };
  assert.equal(canRecordManual(base), true);
  assert.equal(canRecordManual({ ...base, permalink: "" }), false);
  assert.equal(canRecordManual({ ...base, permalink: "https://example.com/p/CxAbc123/" }), false);
  assert.equal(canRecordManual({ ...base, labelChecked: false }), false);
  assert.equal(canRecordManual({ ...base, tokenSet: false }), false);
  assert.equal(canRecordManual({ ...base, busy: true }), false);
});

test("안내 문구: 한계를 솔직히 적고, 캡션은 서버가 준 전문 그대로", () => {
  assert.match(MANUAL_LIMIT_NOTE, /실제로 있는 게시물인지/);
  assert.match(MANUAL_LIMIT_NOTE, /확인하지 못/);
  assert.ok(MANUAL_STEPS.some((s) => /AI/.test(s)));
  assert.ok(MANUAL_STEPS.some((s) => /링크/.test(s)));
  const pkg = { caption: "글\n#태그" } as ManualPackage;
  assert.equal(manualCaptionText(pkg), "글\n#태그");
  assert.equal(manualCaptionText(null), "");
});

test("기록 실패는 코드별 문구이고 서버 문장은 쓰지 않는다", () => {
  assert.match(manualRecordErrorKo(400, "INVALID_PERMALINK"), /형식이 맞지 않아/);
  assert.match(manualRecordErrorKo(409, "PERMALINK_ALREADY_USED"), /이미 다른 게시 건/);
  assert.match(manualRecordErrorKo(409, "ALREADY_PUBLISHED"), /이미 게시 완료/);
  assert.match(manualRecordErrorKo(400, "AI_LABEL_NOT_CONFIRMED"), /AI 표시/);
  assert.match(manualRecordErrorKo(403), /권한/);
  assert.match(manualRecordErrorKo(500), /기록되지 않았습니다/);
});

const state = (over: Partial<PublishState>): PublishState => ({
  status: "not_requested", can_request: false, reason_code: null, review_id: null, failure_code: null, permalink: null, caption: "글", ...over,
});

test("상태줄: 수동 대기는 사람이 올리고 링크를 기록해야 닫힌다고 알린다", () => {
  const line = publishStatusLine(state({ status: "manual_pending", manual_pending: true }))!;
  assert.equal(line.tone, "info");
  assert.match(line.text, /직접 올린 뒤 게시물 링크를 기록/);
  assert.equal(line.link, undefined);
  assert.match(publishStatusLine(state({ status: "held", auto_publish_off: true }))!.text, /수동 게시로 이어집니다/);
  assert.doesNotMatch(publishStatusLine(state({ status: "held" }))!.text, /수동/);
});

test("상태줄: 직접 게시됨 + 링크(형식이 맞을 때만), 자동 게시 건은 기존 문구", () => {
  const manual = publishStatusLine(state({ status: "published", manual: true, posted_by: "재헌", permalink: OK }))!;
  assert.match(manual.text, /직접 게시됨/);
  assert.match(manual.text, /재헌/);
  assert.match(manual.text, /확인하지 않았습니다/);
  assert.equal(manual.link, OK);
  const bad = publishStatusLine(state({ status: "published", manual: true, permalink: "javascript:alert(1)" }))!;
  assert.equal(bad.link, undefined);
  const auto = publishStatusLine(state({ status: "published", permalink: null }))!;
  assert.equal(auto.text, "Instagram에 게시됐습니다.");
  assert.equal(auto.link, undefined);
});

test("게시 요청 결과: 자동 게시가 꺼져 있으면 수동 게시로 이어진다고 알린다", () => {
  const off = publishResultKo({ status: "completed", outcome: "requested", output: { auto_publish_off: true, manual_available: true } });
  assert.equal(off.kind, "requested");
  assert.match(off.text, /아직 게시되지 않았습니다/);
  assert.match(off.text, /수동 게시/);
  assert.doesNotMatch(publishResultKo({ status: "completed", outcome: "requested", output: null }).text, /수동/);
});
