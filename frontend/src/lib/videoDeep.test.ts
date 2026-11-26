// node --test (npm test). 성과 탭 영상 성과 (11/27, 유튜브만) — lib/videoDeep.ts · components/data/VideoDeep.tsx
// 응답은 모두 가짜 (실제 API 호출 없음)
import assert from "node:assert/strict";
import { test } from "node:test";
import type { Proposal } from "./proposals.ts";
import {
  NEED_URL, NOT_THIS_COMPANY, ONLY_YOUTUBE, SAMPLE_SHORT, WAITING,
  adView, ageName, audienceRows, basisChip, basisLine, biggestDrop, dropLine, joinVideos, measuresVideo, registerLine,
  registerRequest, sourceRows, statCells, targetAudience, trafficName, trafficRows, youtubeId,
  type AdPerformance, type PerfVideo, type VideoInsights,
} from "./videoDeep.ts";

const ID = "dQw4w9WgXcQ";
const perf = (over: Partial<PerfVideo> = {}): PerfVideo => ({
  platform: "youtube", video_id: ID, video_url: `https://youtube.com/shorts/${ID}`, test: false, title: "AI 가상 인물 쇼", campaign_id: "wr_1",
  checkpoints: [
    { checkpoint: "1h", status: "known", views: 120, likes: 9, comments: 1, measured_at: "2026-11-26T01:00:00Z" },
    { checkpoint: "1d", status: "known", views: 3412, likes: 126, comments: 9, measured_at: "2026-11-27T01:00:00Z" },
    { checkpoint: "3d", status: "unknown", reason: "측정 중 (31.5시간 뒤)" },
    { checkpoint: "7d", status: "unknown", reason: "측정 중 (127.5시간 뒤)" },
  ],
  ...over,
});
const connected = [
  { source: "youtube_analytics", status: "connected" as const, needs: [] },
  { source: "youtube_reporting", status: "connected" as const, needs: [] },
];
const ins = (over: Partial<VideoInsights> = {}): VideoInsights => ({
  platform: "youtube", video_key: `youtube:${ID}`, video_id: ID, status: "known", as_of: "2026-11-25", collected_at: "2026-11-26T20:00:00Z",
  watch: { views: 3000, avg_view_seconds: 10.2, avg_view_percent: 68.04 },
  retention: [{ ratio: 0, watch_ratio: 1 }, { ratio: 0.1, watch_ratio: 0.82 }, { ratio: 0.5, watch_ratio: 0.7 }, { ratio: 1, watch_ratio: 0.48 }],
  traffic: [{ source: "YT_SEARCH", views: 30, share: 0.09 }, { source: "SHORTS", views: 290, share: 0.62 }, { source: "WEIRD_NEW", views: 5 }],
  audience: [{ age: "age18-24", gender: "female", percent: 34 }, { age: "age25-34", gender: "male", percent: 22 }],
  audience_basis: "video", reach: { status: "known", impressions: 21000, ctr: 0.041 }, needs: [], ...over,
});

test("회사: AXcellent만 측정, 다른 회사·로그인 전은 빈 상태 문구", () => {
  assert.equal(measuresVideo("axcellent"), true);
  for (const c of ["catbooks", "nubi", undefined, null, ""]) assert.equal(measuresVideo(c), false);
  assert.equal(NOT_THIS_COMPANY, "이 회사는 영상 성과 측정을 쓰지 않아요");
});

test("등록 요청 모양: video_url + 캠페인 번호(있을 때만), 공백은 자른다", () => {
  assert.deepEqual(registerRequest(`  https://www.youtube.com/shorts/${ID} `, ""), { ok: true, input: { video_url: `https://www.youtube.com/shorts/${ID}` } });
  assert.deepEqual(registerRequest(`https://youtu.be/${ID}`, " wr_012c98d9d9 "),
    { ok: true, input: { video_url: `https://youtu.be/${ID}`, campaign_id: "wr_012c98d9d9" } });
  assert.deepEqual(registerRequest(`https://www.youtube.com/watch?feature=x&v=${ID}`, "").ok, true);
  assert.deepEqual(registerRequest("", "wr_1"), { ok: false, message: NEED_URL });
  assert.equal(registerRequest(`https://youtu.be/${ID}`, "x".repeat(121)).ok, false);
});

test("유튜브가 아닌 주소는 막는다 (인스타·틱톡·채널 주소·아무 글)", () => {
  for (const u of ["https://www.instagram.com/reel/Cabc123/", "https://www.tiktok.com/@a/video/123", "https://www.youtube.com/@Axcellent7", "영상"]) {
    assert.deepEqual(registerRequest(u, ""), { ok: false, message: ONLY_YOUTUBE }, u);
  }
  assert.equal(ONLY_YOUTUBE, "이번엔 유튜브만 측정해요");
  assert.equal(youtubeId(`https://youtube.com/live/${ID}?si=1`), ID);
});

test("등록 결과 한 줄은 서버 응답 그대로", () => {
  assert.match(registerLine(perf({ tracking: true })).text, /^등록했어요 · AI 가상 인물 쇼 · 캠페인 wr_1/);
  const bad = registerLine({ test: false, checkpoints: [], reason: "영상 주소를 알아보지 못함 (YouTube·인스타그램·틱톡 주소만)" });
  assert.deepEqual(bad, { ok: false, text: "등록하지 못했어요 · 영상 주소를 알아보지 못함 (YouTube·인스타그램·틱톡 주소만)" });
});

test("숫자 4칸: 공개 수치는 값이 있는 가장 늦은 시점, 깊은 지표는 Analytics·Reporting 값", () => {
  const [views, reactions, pct, ctr] = statCells(perf(), ins());
  assert.equal(views.value, "3,412");
  assert.equal(views.sub, "1일 시점 · 공개 수치");
  assert.equal(reactions.value, "126 · 9");
  assert.equal(pct.value, "68%");
  assert.equal(pct.sub, "평균 10.2초 · 유튜브 분석");
  assert.equal(ctr.value, "4.1%");
  assert.equal(ctr.sub, "노출 21,000 · Reporting");
});

test("값 없음 ≠ 0: 없으면 null + 이유, 0은 0으로 그린다", () => {
  const zero = perf({ checkpoints: [{ checkpoint: "1h", status: "known", views: 0, likes: 0, comments: 0 }] });
  const [v0, r0] = statCells(zero, ins({ watch: { avg_view_percent: 0 }, reach: { status: "known", ctr: 0, impressions: 0 } }));
  assert.equal(v0.value, "0");
  assert.equal(r0.value, "0 · 0");

  // Analytics 연결 대기 · 공개 수치 측정 중
  const waiting = ins({ status: "unknown", reason: "연결 대기: YOUTUBE_ANALYTICS_REFRESH_TOKEN 없음", watch: null, reach: null,
    needs: [{ source: "youtube_analytics", status: "waiting", needs: ["서버 env YOUTUBE_ANALYTICS_REFRESH_TOKEN"] }] });
  const early = perf({ checkpoints: [{ checkpoint: "1h", status: "unknown", reason: "측정 중 (0.5시간 뒤)" }] });
  const cells = statCells(early, waiting);
  for (const c of cells) assert.equal(c.value, null, c.key);
  assert.equal(cells[0].missing, "측정 중");
  assert.equal(cells[2].missing, WAITING);
  assert.equal(cells[3].missing, WAITING);

  // 좋아요 숨김 (None) · Reporting 리포트 기다리는 중 · 깊은 지표 없음
  const hidden = perf({ checkpoints: [{ checkpoint: "1d", status: "known", views: 50, likes: null, comments: 2 }] });
  const pendingReach = ins({ reach: { status: "unknown", reason: "리포트 기다리는 중 (첫 리포트는 예약 다음 날 이후)" } });
  const [, rh, , ch] = statCells(hidden, pendingReach);
  assert.equal(rh.value, "숨김 · 2");
  assert.equal(ch.value, null);
  assert.equal(ch.missing, "리포트 기다리는 중");
  assert.equal(statCells(perf(), null)[2].missing, WAITING);
});

test("기준 시각: 공개 수치 잰 시각과 유튜브 분석 날짜를 같이 보인다", () => {
  const line = basisLine(perf(), ins(), (iso) => iso.slice(0, 10));
  assert.equal(line, "공개 수치 2026-11-27 · 유튜브 분석 2026-11-25 기준 (하루 이틀 늦게 들어와요)");
  assert.equal(basisLine(perf({ checkpoints: [] }), null, String), "공개 수치 아직 없음 · 유튜브 분석 아직 없음");
});

test("유지 곡선 최대 하락 구간 (이웃한 두 점, 순서 섞여도)", () => {
  const d = biggestDrop(ins().retention);
  assert.equal(d?.from, 0.5);
  assert.equal(d?.to, 1);
  assert.ok(Math.abs((d?.drop ?? 0) - 0.22) < 1e-9); // 1→0.82(0.18) · 0.82→0.7(0.12) · 0.7→0.48(0.22)
  assert.equal(dropLine({ from: 0, to: 0.1, drop: 0.18 }), "영상 0~10% 구간에서 18%p 빠짐");
  const shuffled = biggestDrop([{ ratio: 0.1, watch_ratio: 0.82 }, { ratio: 0, watch_ratio: 1 }, { ratio: 0.2, watch_ratio: 0.8 }]);
  assert.equal(shuffled?.from, 0);
  assert.equal(shuffled?.to, 0.1);
  assert.equal(biggestDrop([{ ratio: 0, watch_ratio: 1 }]), null);
  assert.equal(biggestDrop([{ ratio: 0, watch_ratio: 0.9 }, { ratio: 1, watch_ratio: 1.1 }]), null); // 다시 보기로 오르기만
});

test("유입 경로: 이름만 바꾸고 값·비율은 그대로, 조회 많은 순", () => {
  const rows = trafficRows(ins().traffic);
  assert.deepEqual(rows.map((r) => [trafficName(r.source), r.views, r.share ?? null]), [["쇼츠 피드", 290, 0.62], ["검색", 30, 0.09], ["WEIRD_NEW", 5, null]]);
  assert.equal(trafficName("EXT_URL"), "외부");
});

test("시청층: 계정 기준 칩, 이름, 그 밖 묶음, 기획 타깃은 캠페인 제안의 audience", () => {
  assert.equal(basisChip("account"), "계정 기준");
  assert.equal(basisChip("video"), "영상별");
  assert.equal(basisChip(null), null);
  assert.equal(ageName("age18-24"), "18–24세");
  assert.equal(ageName("age65-"), "65세 이상");
  const cells = [30, 20, 15, 10, 8, 7, 6, 4].map((p, i) => ({ age: `age${18 + i}-${19 + i}`, gender: i % 2 ? "male" : "female", percent: p }));
  const rows = audienceRows(cells);
  assert.equal(rows.length, 6);
  assert.deepEqual(rows[0], { name: "18–19세 여성", percent: 30 });
  assert.deepEqual(rows[5], { name: "그 밖", percent: 17 });
  assert.deepEqual(audienceRows([]), []);
  const prop = (id: string, campaign: string, audience?: string) => ({ id, handoff: { ok: true, campaign }, audience }) as unknown as Proposal;
  const list = [prop("P1", "wr_0", "40대"), prop("P2", "wr_1"), prop("P3", "wr_1", " 20대 여성 ")];
  assert.equal(targetAudience(list, "wr_1"), "20대 여성");
  assert.equal(targetAudience(list, "wr_9"), null);
  assert.equal(targetAudience(list, null), null);
  assert.equal(SAMPLE_SHORT, "표본 부족");
});

const adWaiting: AdPerformance = {
  status: "unknown", platforms: [{ platform: "google", status: "unknown", reason: "연결 대기: GOOGLE_ADS_DEVELOPER_TOKEN 없음", needs: ["서버 env GOOGLE_ADS_DEVELOPER_TOKEN"] }], campaigns: [],
};

test("광고: 연결 대기면 칸 하나(빈 표 없음), 읽었으면 구글 캠페인 줄", () => {
  assert.deepEqual(adView(adWaiting), { kind: "waiting", reason: "연결 대기: GOOGLE_ADS_DEVELOPER_TOKEN 없음", needs: ["서버 env GOOGLE_ADS_DEVELOPER_TOKEN"] });
  assert.equal(adView(null, "받지 못함").kind, "waiting");
  const known: AdPerformance = {
    status: "known", period: "2026-11-20~2026-11-26", platforms: [{ platform: "google", status: "known", needs: [] }],
    campaigns: [{ platform: "google", campaign_id: "1", name: "AXcellent 숏츠 11월", cost: 30000, currency: "KRW" },
      { platform: "meta", campaign_id: "2", name: "메타" }],
  };
  const v = adView(known);
  assert.equal(v.kind, "rows");
  assert.deepEqual(v.kind === "rows" ? v.rows.map((r) => r.name) : [], ["AXcellent 숏츠 11월"]);
  assert.equal(adView({ ...known, campaigns: [] }).kind, "waiting");
});

test("출처 연결 상태 표: 유튜브 3줄 + 구글애즈, needs 그대로, 인스타 줄 없음", () => {
  const waitingIns = ins({ needs: [
    { source: "youtube_analytics", status: "waiting", reason: "연결 대기: YOUTUBE_ANALYTICS_REFRESH_TOKEN 없음", needs: ["서버 env YOUTUBE_ANALYTICS_REFRESH_TOKEN", "채널 주인 계정의 OAuth 동의 (yt-analytics.readonly)"] },
    { source: "youtube_reporting", status: "check", reason: "예약 필요 (Reporting job channel_reach_basic_a1)", needs: ["YouTube Reporting job channel_reach_basic_a1 예약 (채널 주인)"] },
    { source: "instagram_insights", status: "waiting", needs: ["x"] },
  ] });
  const rows = sourceRows([perf()], waitingIns, adWaiting);
  assert.deepEqual(rows.map((r) => r.name), ["YouTube 공개 수치", "YouTube Analytics", "YouTube Reporting", "Google Ads"]);
  assert.deepEqual(rows.map((r) => r.status), ["connected", "waiting", "check", "waiting"]);
  assert.equal(rows[1].label, "연결 대기 · YOUTUBE_ANALYTICS_REFRESH_TOKEN 없음");
  assert.deepEqual(rows[1].needs, ["서버 env YOUTUBE_ANALYTICS_REFRESH_TOKEN", "채널 주인 계정의 OAuth 동의 (yt-analytics.readonly)"]);
  assert.equal(rows[2].label, "권한 확인 · 예약 필요 (Reporting job channel_reach_basic_a1)");
  assert.deepEqual(rows[3].needs, ["서버 env GOOGLE_ADS_DEVELOPER_TOKEN"]);

  const all = sourceRows([perf()], ins({ needs: connected }), { status: "known", platforms: [{ platform: "google", status: "known", needs: [] }], campaigns: [] });
  assert.deepEqual(all.map((r) => r.label), ["연결됨", "연결됨", "연결됨", "연결됨"]);
  const none = sourceRows([], null, null, "받지 못함");
  assert.equal(none[0].label, "등록한 영상 없음");
  assert.equal(none[0].status, "waiting");
  assert.equal(none[1].status, "waiting");
});

test("묶기: 유튜브·시험 아님만, 같은 영상 key의 깊은 지표", () => {
  const list = [perf(), perf({ video_id: "aaaaaaaaaaa", test: true }), perf({ platform: "instagram", video_id: "C1" }), perf({ video_id: "bbbbbbbbbbb" })];
  const rows = joinVideos(list, { ...ins(), videos: [ins()] } as VideoInsights);
  assert.deepEqual(rows.map((r) => [r.perf.video_id, r.ins?.video_key ?? null]), [[ID, `youtube:${ID}`], ["bbbbbbbbbbb", null]]);
  assert.equal(joinVideos(list, null)[0].ins, null);
});
