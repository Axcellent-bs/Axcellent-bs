// 성과 탭 "영상 성과" (11/27 시안, 유튜브만). 순수 계산만. node --test (videoDeep.test.ts).
// 화면: components/data/VideoDeep.tsx. 값은 데이터 AI video_performance(공개 수치)·video_insights(유튜브 Analytics)·
// ad_performance(구글애즈 읽기) 응답 그대로 옮긴다. 값이 없으면 0이 아니라 "연결 대기·표본 부족" 같은 말로 둔다.
// 화면이 직접 계산하는 것은 표시용 둘뿐: 유지 곡선 가장 큰 하락 구간, 시청층 칸 묶음 순서.
import type { Proposal } from "./proposals.ts";

// ---------------------------------------------------------------- 응답 모양 (ax_contracts data.py · ad_performance.py에서 쓰는 칸만)

export type SourceStatus = "connected" | "waiting" | "check";

export interface InsightsSource { source: string; status: SourceStatus; reason?: string | null; needs: string[] }
export interface InsightsWatch { views?: number | null; avg_view_seconds?: number | null; avg_view_percent?: number | null; watch_minutes?: number | null }
export interface RetentionPoint { ratio: number; watch_ratio: number }
export interface TrafficRow { source: string; views: number; share?: number | null; ad?: boolean }
export interface AudienceCell { age: string; gender: string; percent: number }
export interface InsightsReach { status: "known" | "unknown"; reason?: string | null; impressions?: number | null; ctr?: number | null; period?: string | null }
export interface VideoInsights {
  platform?: string | null; video_key?: string | null; video_id?: string | null; video_url?: string | null; campaign_id?: string | null;
  status: "known" | "unknown"; reason?: string | null; as_of?: string | null; collected_at?: string | null;
  watch?: InsightsWatch | null; retention: RetentionPoint[]; traffic: TrafficRow[];
  audience: AudienceCell[]; audience_basis?: "video" | "account" | null; audience_reason?: string | null;
  reach?: InsightsReach | null; needs: InsightsSource[]; videos?: VideoInsights[];
}

export interface PerfCheckpoint {
  checkpoint: "1h" | "1d" | "3d" | "7d"; status: "known" | "unknown"; reason?: string | null; measured_at?: string | null;
  views?: number | null; likes?: number | null; comments?: number | null;
}
export interface PerfVideo {
  status?: "known" | "unknown"; reason?: string | null; platform?: string | null; video_id?: string | null; video_url?: string | null;
  campaign_id?: string | null; test: boolean; title?: string | null; published_at?: string | null; tracking?: boolean;
  checkpoints: PerfCheckpoint[]; needs?: string[]; limits?: string[];
}

export interface AdPlatformStatus { platform: "google" | "meta"; status: "known" | "unknown"; reason?: string | null; needs: string[]; as_of?: string | null }
export interface AdCampaign {
  platform: "google" | "meta"; campaign_id: string; name: string; cost?: number | null; currency?: string | null;
  impressions?: number | null; clicks?: number | null; ctr?: number | null; ctr_unit?: "ratio" | "percent" | null;
  views?: number | null; view_rate?: number | null; video_key?: string | null;
}
export interface AdPerformance { status: "known" | "unknown"; reason?: string | null; period?: string | null; platforms: AdPlatformStatus[]; campaigns: AdCampaign[] }

// ---------------------------------------------------------------- 회사

/** 영상 성과 측정을 쓰는 회사 (이번엔 AXcellent 자사 유튜브만). 다른 회사·로그인 전은 빈 상태 */
export const VIDEO_COMPANIES: readonly string[] = ["axcellent"];
export const NOT_THIS_COMPANY = "이 회사는 영상 성과 측정을 쓰지 않아요";
export const measuresVideo = (companyId: string | undefined | null): boolean => !!companyId && VIDEO_COMPANIES.includes(companyId);

// ---------------------------------------------------------------- ① 영상 주소 등록

export const ONLY_YOUTUBE = "이번엔 유튜브만 측정해요";
export const NEED_URL = "영상 주소를 넣어 주세요";
// 데이터 AI parse_video의 유튜브 주소 두 가지와 같다 (watch?v= · shorts/ · live/ · embed/ · youtu.be/)
const YT = [
  /youtube\.com\/(?:watch\?(?:.*&)?v=|shorts\/|live\/|embed\/)([A-Za-z0-9_-]{11})/,
  /youtu\.be\/([A-Za-z0-9_-]{11})/,
];

/** 유튜브 영상 id. 유튜브 주소가 아니면 null */
export function youtubeId(url: string): string | null {
  for (const re of YT) {
    const m = re.exec(url);
    if (m) return m[1];
  }
  return null;
}

export type RegisterInput = { video_url: string; campaign_id?: string };
/** 등록 요청 (video_performance video_url 등록). 유튜브가 아니면 막고 한 줄 */
export function registerRequest(url: string, campaign: string): { ok: true; input: RegisterInput } | { ok: false; message: string } {
  const u = url.trim();
  const c = campaign.trim();
  if (!u) return { ok: false, message: NEED_URL };
  if (u.length > 500 || !youtubeId(u)) return { ok: false, message: ONLY_YOUTUBE };
  if (c.length > 120) return { ok: false, message: "캠페인 번호가 너무 길어요 (120자까지)" };
  return { ok: true, input: c ? { video_url: u, campaign_id: c } : { video_url: u } };
}

/** 등록 결과 한 줄 (서버 응답 그대로) */
export function registerLine(out: PerfVideo): { ok: boolean; text: string } {
  if (out.tracking && out.video_id) {
    const name = out.title || out.video_id;
    const camp = out.campaign_id ? ` · 캠페인 ${out.campaign_id}` : "";
    return { ok: true, text: `등록했어요 · ${name}${camp} · 공개 수치는 1시간마다, 유튜브 분석은 하루 1번 들어와요` };
  }
  return { ok: false, text: `등록하지 못했어요 · ${out.reason || "서버가 이유를 주지 않았어요"}` };
}

// ---------------------------------------------------------------- ② 영상 카드 숫자 4칸

const CP_ORDER = ["1h", "1d", "3d", "7d"] as const;
const CP_NAME = { "1h": "1시간", "1d": "1일", "3d": "3일", "7d": "7일" } as const;

/** 값이 있는 가장 늦은 시점 (없으면 null) */
export function latestKnown(v: PerfVideo): PerfCheckpoint | null {
  for (const cp of [...CP_ORDER].reverse()) {
    const c = v.checkpoints.find((x) => x.checkpoint === cp);
    if (c && c.status === "known" && typeof c.views === "number") return c;
  }
  return null;
}

export const WAITING = "연결 대기";
export const SAMPLE_SHORT = "표본 부족";
const num = (v: number) => v.toLocaleString("ko-KR");
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** 한 칸: value가 null이면 화면은 숫자 대신 missing(연결 대기·측정 중 등)을 쓴다. 0은 0으로 */
export interface StatCell { key: "views" | "reactions" | "avg_percent" | "ctr"; label: string; value: string | null; sub: string; missing: string }

/** 깊은 지표가 없을 때의 짧은 말: 저장 전이면 서버 이유, 출처가 대기면 "연결 대기" */
function insightsMissing(ins: VideoInsights | null): string {
  if (!ins) return WAITING;
  const yt = ins.needs.find((s) => s.source === "youtube_analytics");
  if (yt && yt.status !== "connected") return yt.status === "check" ? "권한 확인" : WAITING;
  return ins.reason ? shortReason(ins.reason) : WAITING;
}
const shortReason = (r: string) => r.split(/[:(]/)[0].trim() || r;

export function statCells(perf: PerfVideo, ins: VideoInsights | null): StatCell[] {
  const last = latestKnown(perf);
  const pending = perf.checkpoints.find((c) => c.status !== "known")?.reason;
  const perfMissing = perf.needs?.length ? WAITING : shortReason(pending || perf.reason || "측정 중");
  const at = last ? `${CP_NAME[last.checkpoint]} 시점 · 공개 수치` : "공개 수치";
  const likes = last?.likes;
  const comments = last?.comments;
  const reactions = isNum(likes) || isNum(comments) ? `${isNum(likes) ? num(likes) : "숨김"} · ${isNum(comments) ? num(comments) : "숨김"}` : null;
  const w = ins?.status === "known" ? ins.watch : null;
  const pct = w && isNum(w.avg_view_percent) ? `${round1(w.avg_view_percent)}%` : null;
  const reach = ins?.status === "known" ? ins.reach : null;
  const ctr = reach?.status === "known" && isNum(reach.ctr) ? `${round1(reach.ctr * 100)}%` : null;
  const insMiss = insightsMissing(ins);
  return [
    { key: "views", label: "조회", value: last && isNum(last.views) ? num(last.views) : null, sub: at, missing: perfMissing },
    { key: "reactions", label: "좋아요 · 댓글", value: reactions, sub: at, missing: last ? "숨김" : perfMissing },
    { key: "avg_percent", label: "평균 시청 비율", value: pct,
      sub: w && isNum(w.avg_view_seconds) ? `평균 ${round1(w.avg_view_seconds)}초 · 유튜브 분석` : "유튜브 분석", missing: insMiss },
    { key: "ctr", label: "노출 클릭률", value: ctr,
      sub: reach?.status === "known" && isNum(reach.impressions) ? `노출 ${num(reach.impressions)} · Reporting` : "Reporting",
      missing: reach && reach.status !== "known" && reach.reason ? shortReason(reach.reason) : insMiss },
  ];
}
const round1 = (v: number) => Math.round(v * 10) / 10;

/** 기준 시각 한 줄: 공개 수치 잰 시각 · 유튜브 분석 기준 날짜 (Analytics는 하루 이틀 늦다) */
export function basisLine(perf: PerfVideo, ins: VideoInsights | null, fmt: (iso: string) => string): string {
  const last = latestKnown(perf);
  const pub = last?.measured_at ? `공개 수치 ${fmt(last.measured_at)}` : "공개 수치 아직 없음";
  const deep = ins?.status === "known" && ins.as_of ? `유튜브 분석 ${ins.as_of} 기준 (하루 이틀 늦게 들어와요)` : "유튜브 분석 아직 없음";
  return `${pub} · ${deep}`;
}

// ---------------------------------------------------------------- ③ 유지 곡선

export interface Drop { from: number; to: number; drop: number }
/** 이웃한 두 점 사이 가장 크게 떨어진 구간 (표시용). 떨어진 곳이 없거나 점이 2개 미만이면 null */
export function biggestDrop(points: readonly RetentionPoint[]): Drop | null {
  const pts = [...points].sort((a, b) => a.ratio - b.ratio);
  let best: Drop | null = null;
  for (let i = 0; i + 1 < pts.length; i++) {
    const d = pts[i].watch_ratio - pts[i + 1].watch_ratio;
    if (d > 0 && (!best || d > best.drop)) best = { from: pts[i].ratio, to: pts[i + 1].ratio, drop: d };
  }
  return best;
}

export const dropLine = (d: Drop) =>
  `영상 ${Math.round(d.from * 100)}~${Math.round(d.to * 100)}% 구간에서 ${Math.round(d.drop * 100)}%p 빠짐`;

// ---------------------------------------------------------------- ④ 유입 경로

/** insightTrafficSourceType → 화면 이름. 모르는 값은 받은 그대로 */
export const TRAFFIC_NAME: Readonly<Record<string, string>> = {
  SHORTS: "쇼츠 피드", YT_SEARCH: "검색", EXT_URL: "외부", ADVERTISING: "광고", SUBSCRIBER: "구독자 피드", RELATED_VIDEO: "추천 영상",
  NO_LINK_OTHER: "직접·기타", PLAYLIST: "재생목록", YT_CHANNEL: "채널 페이지", YT_OTHER_PAGE: "유튜브 다른 화면", NOTIFICATION: "알림",
  END_SCREEN: "최종 화면", HASHTAGS: "해시태그", SOUND_PAGE: "사운드 페이지", YT_PLAYLIST_PAGE: "재생목록 화면", CAMPAIGN_CARD: "캠페인 카드",
};
export const trafficName = (s: string) => TRAFFIC_NAME[s] ?? s;
/** 조회 많은 순 (서버 값 그대로, 순서만) */
export const trafficRows = (rows: readonly TrafficRow[]) => [...rows].sort((a, b) => b.views - a.views);

// ---------------------------------------------------------------- ⑤ 기획 타깃 vs 실제 시청층

const GENDER: Readonly<Record<string, string>> = { female: "여성", male: "남성", user_specified: "기타", F: "여성", M: "남성", U: "기타" };
/** age18-24 · 18-24 → "18–24세", 65- → "65세 이상" */
export function ageName(age: string): string {
  const a = age.replace(/^age/, "");
  if (!a) return "";
  const m = /^(\d+)-(\d+)$/.exec(a);
  if (m) return `${m[1]}–${m[2]}세`;
  const up = /^(\d+)-?\+?$/.exec(a);
  return up ? `${up[1]}세 이상` : a;
}
export const audienceName = (c: AudienceCell) => [ageName(c.age), GENDER[c.gender] ?? c.gender].filter(Boolean).join(" ") || "모름";
/** 실제 시청층: 큰 순으로 top개, 나머지는 "그 밖" 한 줄 (받은 비율의 합만) */
export function audienceRows(cells: readonly AudienceCell[], top = 5): { name: string; percent: number }[] {
  const sorted = [...cells].sort((a, b) => b.percent - a.percent);
  const head = sorted.slice(0, top).map((c) => ({ name: audienceName(c), percent: c.percent }));
  const rest = sorted.slice(top).reduce((s, c) => s + c.percent, 0);
  return rest > 0 ? [...head, { name: "그 밖", percent: round1(rest) }] : head;
}

/** 기획 타깃: 이 영상 캠페인으로 마케팅에 넘어간 제안의 audience (없으면 null) */
export function targetAudience(proposals: readonly Proposal[], campaignId: string | null | undefined): string | null {
  if (!campaignId) return null;
  const p = proposals.find((x) => x.handoff?.campaign === campaignId && x.audience?.trim());
  return p?.audience?.trim() || null;
}

/** 시청층 칸 머리 칩: 계정 기준이면 "계정 기준", 영상별이면 "영상별" */
export const basisChip = (basis: VideoInsights["audience_basis"]): string | null =>
  basis === "account" ? "계정 기준" : basis === "video" ? "영상별" : null;

// ---------------------------------------------------------------- ⑥ 광고 · ⑦ 출처 연결 상태

export type AdView = { kind: "waiting"; reason: string; needs: string[] } | { kind: "rows"; rows: AdCampaign[]; period: string | null };
/** 구글애즈: 읽었으면 표, 못 읽었으면 칸 하나 "구글애즈 연결 대기" (빈 표는 그리지 않는다) */
export function adView(ad: AdPerformance | null, failed?: string): AdView {
  const g = ad?.platforms.find((p) => p.platform === "google");
  if (!ad || !g || g.status !== "known") {
    return { kind: "waiting", reason: g?.reason || failed || ad?.reason || WAITING, needs: g?.needs ?? [] };
  }
  const rows = ad.campaigns.filter((c) => c.platform === "google");
  if (rows.length === 0) return { kind: "waiting", reason: "읽은 기간에 구글애즈 캠페인이 없어요", needs: [] };
  return { kind: "rows", rows, period: ad.period ?? null };
}

export interface SourceRow { name: string; reads: string; status: SourceStatus; label: string; needs: string[] }
const STATUS_LABEL: Record<SourceStatus, string> = { connected: "연결됨", waiting: "연결 대기", check: "권한 확인" };

/** 출처 연결 상태 표: 유튜브 공개 수치 / 유튜브 Analytics / 유튜브 Reporting / 구글애즈 (응답의 needs 그대로) */
export function sourceRows(videos: readonly PerfVideo[], ins: VideoInsights | null, ad: AdPerformance | null, adFailed?: string): SourceRow[] {
  const yt = videos.filter((v) => v.platform === "youtube");
  const pubKnown = yt.some((v) => latestKnown(v) !== null);
  const pubNeeds = [...new Set(yt.flatMap((v) => v.needs ?? []))];
  const pub: SourceRow = pubKnown
    ? { name: "YouTube 공개 수치", reads: "조회·좋아요·댓글", status: "connected", label: STATUS_LABEL.connected, needs: [] }
    : { name: "YouTube 공개 수치", reads: "조회·좋아요·댓글", status: "waiting",
        label: pubNeeds.length ? STATUS_LABEL.waiting : yt.length ? "아직 잰 값 없음 (1시간마다)" : "등록한 영상 없음", needs: pubNeeds };
  const from = (id: string, name: string, reads: string): SourceRow => {
    const s = ins?.needs.find((x) => x.source === id);
    if (!s) return { name, reads, status: ins ? "connected" : "waiting", label: ins ? STATUS_LABEL.connected : WAITING, needs: [] };
    const label = s.status === "connected" ? STATUS_LABEL.connected : s.reason ? `${STATUS_LABEL[s.status]} · ${shortWhy(s.reason)}` : STATUS_LABEL[s.status];
    return { name, reads, status: s.status, label, needs: s.status === "connected" ? [] : s.needs };
  };
  const g = ad?.platforms.find((p) => p.platform === "google");
  const gads: SourceRow = g?.status === "known"
    ? { name: "Google Ads", reads: "광고 비용·노출·조회", status: "connected", label: STATUS_LABEL.connected, needs: [] }
    : { name: "Google Ads", reads: "광고 비용·노출·조회", status: g?.reason?.startsWith(WAITING) || !g ? "waiting" : "check",
        label: g?.reason ? (g.reason.startsWith(WAITING) ? WAITING : `${STATUS_LABEL.check} · ${g.reason}`) : adFailed ? `${WAITING} · ${adFailed}` : WAITING,
        needs: g?.needs ?? [] };
  return [
    pub,
    from("youtube_analytics", "YouTube Analytics", "평균 시청·유지 곡선·유입 경로·시청층"),
    from("youtube_reporting", "YouTube Reporting", "노출·클릭률"),
    gads,
  ];
}
// "연결 대기: A, B 없음" 같은 이유는 상태 글자와 겹치니 뒤쪽만
const shortWhy = (r: string) => r.replace(/^(연결 대기|권한 확인)\s*[:·]?\s*/, "") || r;

// ---------------------------------------------------------------- 묶기

export interface VideoRow { perf: PerfVideo; ins: VideoInsights | null }
/** 공개 수치 영상(유튜브·시험 아님)마다 같은 영상의 깊은 지표를 붙인다 (video_key = youtube:<id>) */
export function joinVideos(perf: readonly PerfVideo[], insights: VideoInsights | null): VideoRow[] {
  const list = insights?.videos ?? [];
  return perf
    .filter((v) => v.platform === "youtube" && !v.test)
    .map((v) => ({ perf: v, ins: list.find((i) => i.video_key === `youtube:${v.video_id}` || (i.platform === "youtube" && i.video_id === v.video_id)) ?? null }));
}
