// node --test (npm test). 성과 탭 = 캠페인 목록 먼저 (11/26, lib/resultCampaigns.ts · components/data/CampaignEffect.tsx)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import type { Proposal } from "./proposals.ts";
import { NOT_MEASURED, campaignRows, effectCell, localInput, measurePrefill, resultTabTag, videoCell } from "./resultCampaigns.ts";

const prop = (id: string, over: Partial<Proposal> = {}): Proposal => ({
  id, title: `제목 ${id}`, from: "data", skill: "trend_report_insight", insight: "x", facts: [], products: [], idea: "", why: "",
  writtenBy: "llm", warnings: [], status: "마케팅 전달", created: "11-20 09:00", options: [], ...over,
});

// 운영 모양: 마케팅에 넘어간 캠페인 8개 + 넘기지 않은 것·전달 실패·같은 캠페인 중복
const HANDED = Array.from({ length: 8 }, (_, i) =>
  prop(`PROP-${i + 1}`, { handoff: { ok: true, campaign: `cmp_PROP-${i + 1}`, at: "2026-11-20T00:00:00Z" } }));
const LIST: Proposal[] = [
  ...HANDED,
  prop("PROP-20", { status: "확인 대기" }),
  prop("PROP-21", { handoff: { ok: false, reason: "응답 없음" } }),
  prop("PROP-22", { handoff: { ok: true, campaign: "cmp_PROP-1" } }), // 같은 캠페인
];

test("목록 행 수 = handoff(캠페인 id)가 있는 제안 수, 전달 실패·중복 캠페인은 뺌, 받은 순서 그대로", () => {
  const rows = campaignRows(LIST);
  assert.equal(rows.length, 8);
  assert.deepEqual(rows.map((r) => r.campaignId), HANDED.map((p) => p.handoff!.campaign));
  assert.deepEqual(rows[0], { campaignId: "cmp_PROP-1", proposalId: "PROP-1", title: "제목 PROP-1", handoffAt: "2026-11-20T00:00:00Z" });
  assert.deepEqual(campaignRows([]), []);
});

test("[측정]이 폼에 campaign_id·시작(handoff 시각)·끝(지금)을 채운다, handoff 시각이 없으면 시작은 비움", () => {
  const now = Date.parse("2026-11-26T03:30:00Z");
  const [row] = campaignRows(LIST);
  const form = measurePrefill(row, now);
  assert.equal(form.campaignId, "cmp_PROP-1");
  assert.equal(form.from, localInput("2026-11-20T00:00:00Z"));
  assert.equal(form.to, localInput(now));
  assert.match(form.from, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
  assert.equal(Date.parse(form.from), Date.parse("2026-11-20T00:00:00Z")); // 이 컴퓨터 시간으로 읽으면 같은 시각
  assert.equal(measurePrefill({ ...row, handoffAt: null }, now).from, "");
  assert.equal(localInput("모름"), "");
});

test("측정 전 문구: 영상 성과에 그 캠페인이 없거나 아직 안 쟀으면 '측정 전', 있으면 서버 값 그대로", () => {
  assert.equal(NOT_MEASURED, "측정 전");
  assert.equal(videoCell(null, "cmp_PROP-1"), "측정 전");
  const videos = [
    { test: false, campaign_id: "cmp_PROP-1", title: "공연 티켓 반전", verdict: "achieved" as const },
    { test: true, campaign_id: "cmp_PROP-1", title: "시험", verdict: "missed" as const },
    { test: false, campaign_id: "cmp_PROP-2", video_id: "v2", verdict: null },
  ];
  assert.equal(videoCell(videos, "cmp_PROP-1"), "공연 티켓 반전 · 목표 달성"); // 시험 영상 뺌
  assert.equal(videoCell(videos, "cmp_PROP-2"), "v2 · 판정 전");
  assert.equal(videoCell(videos, "cmp_PROP-3"), "측정 전");
  assert.equal(effectCell(undefined), "측정 전");
  assert.equal(effectCell({ summary: "주문이 기준선보다 12건 늘었어요" }), "주문이 기준선보다 12건 늘었어요");
  assert.equal(effectCell({ summary: null }), "측정함 (요약 없음)");
});

test("탭 꼬리표: 측정 대기 → 측정 전 N (연결 확인 필요·확인 중·캠페인 없음은 지금 글자)", () => {
  const rows = campaignRows(LIST);
  assert.equal(resultTabTag("측정 대기", "idle", rows, new Set()), "측정 전 8");
  assert.equal(resultTabTag("동작 중", "ok", rows, new Set(["cmp_PROP-1", "cmp_PROP-2", "cmp_없음"])), "측정 전 6");
  assert.equal(resultTabTag("연결 확인 필요", "failed", rows, new Set()), "연결 확인 필요");
  assert.equal(resultTabTag("확인 중", "checking", rows, new Set()), "확인 중");
  assert.equal(resultTabTag("측정 대기", "idle", [], new Set()), "측정 대기");
});

test("성과 탭 화면: 캠페인 표가 먼저, [측정]은 줄 아래 폼을 펼치고 누를 때만 부름, 단독 폼은 접힘", () => {
  const src = readFileSync(new URL("../components/data/CampaignEffect.tsx", import.meta.url), "utf8");
  assert.match(src, /title=\{`마케팅에 넘어간 캠페인 \$\{campaigns\.length/);
  assert.match(src, /measurePrefill\(row, Date\.now\(\)\)/);
  assert.match(src, /<MeasureFormBox key=\{row\.campaignId\} initial=\{open\.form\}/);
  assert.match(src, /<summary[^>]*>다른 기간·캠페인 직접 넣기<\/summary>/);
  assert.doesNotMatch(src, /<details[^>]*\bopen\b/);
  // 측정 호출은 폼 제출(사람이 누름)로만: useEffect가 query(제출로만 생김)를 기다린다
  assert.match(src, /if \(!kpiLive \|\| !query\) return;/);
  assert.equal(src.match(/dataAi\.campaignEffect\(/g)?.length, 1);
  assert.doesNotMatch(src, /text-\[(10|10\.5|11|11\.5)px\]/);
  const scan = readFileSync(new URL("../components/data/TrendScan.tsx", import.meta.url), "utf8");
  const result = scan.slice(scan.indexOf("// 성과 (11/26)"));
  assert.ok(result.indexOf("<CampaignEffect") < result.indexOf("<VideoPerformance"), "캠페인 목록이 영상 성과보다 위");
  assert.match(scan, /campaignRows\(dataProposals\(planningAll\)\)/);
  assert.match(scan, /\{resultTag\}/);
});
