// 트렌드&콘텐츠 성과 탭 = 캠페인 목록 먼저 (11/26 시안). 순수 계산만. node --test (resultCampaigns.test.ts).
// 화면: components/data/CampaignEffect.tsx. 값은 지금 받는 기획 기록(handoff)·영상 성과(video_performance)·이 화면에서 잰 캠페인 효과만 쓴다.
// 숫자는 데이터 AI 서버 값만 옮긴다(화면에서 계산하지 않는다). 자동 측정 없음: [측정]은 폼만 채운다
import type { Proposal } from "./proposals.ts";

export interface CampaignRow { campaignId: string; proposalId: string; title: string; handoffAt: string | null }

/** 마케팅에 넘어간 캠페인: 데이터에서 온 제안 중 handoff에 캠페인 id가 있는 것 (전달 실패 뺌, 같은 캠페인은 한 줄, 받은 순서 그대로) */
export function campaignRows(list: readonly Proposal[]): CampaignRow[] {
  const seen = new Set<string>();
  const out: CampaignRow[] = [];
  for (const p of list) {
    const h = p.handoff;
    if (!h || h.ok === false || !h.campaign || seen.has(h.campaign)) continue;
    seen.add(h.campaign);
    out.push({ campaignId: h.campaign, proposalId: p.id, title: p.title, handoffAt: h.at ?? null });
  }
  return out;
}

const pad = (n: number) => String(n).padStart(2, "0");
/** 시각 → <input type="datetime-local"> 값 (이 컴퓨터 시간). 모르면 "" */
export function localInput(at: string | number | null | undefined): string {
  const t = typeof at === "number" ? at : at ? Date.parse(at) : Number.NaN;
  if (!Number.isFinite(t)) return "";
  const d = new Date(t);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export interface MeasureForm { campaignId: string; from: string; to: string }
/** [측정]이 채울 폼: campaign_id, 시작 = handoff 시각(없으면 비움), 끝 = 지금. 사람이 바꿀 수 있다 */
export const measurePrefill = (row: CampaignRow, now: number): MeasureForm =>
  ({ campaignId: row.campaignId, from: localInput(row.handoffAt), to: localInput(now) });

export const NOT_MEASURED = "측정 전";

export type Verdict = "achieved" | "missed" | "measuring" | "excluded";
export const VERDICT_LABEL: Record<Verdict, string> = { achieved: "목표 달성", missed: "목표 미달", measuring: "측정 중", excluded: "집계 제외" };

type VideoLike = { test: boolean; campaign_id?: string | null; title?: string | null; video_id?: string | null; verdict?: Verdict | null };
/** 영상 성과 칸: 이 캠페인의 영상(시험 영상 뺌) 판정 글자 그대로. 없으면 "측정 전" */
export function videoCell(videos: readonly VideoLike[] | null, campaignId: string): string {
  const mine = (videos ?? []).filter((v) => !v.test && v.campaign_id === campaignId);
  if (mine.length === 0) return NOT_MEASURED;
  return mine.map((v) => `${v.title || v.video_id || "영상"} · ${v.verdict ? VERDICT_LABEL[v.verdict] : "판정 전"}`).join(" / ");
}

/** 캠페인 효과 칸: 이 화면에서 잰 결과가 있으면 서버 요약 한 줄, 없으면 "측정 전" */
export function effectCell(out: { summary?: string | null } | null | undefined): string {
  if (!out) return NOT_MEASURED;
  return out.summary?.trim() || "측정함 (요약 없음)";
}

/** 성과 탭 꼬리표: 연결 확인 필요·확인 중은 그대로, 캠페인이 있으면 "측정 전 N"(N = 이 화면에서 아직 안 잰 캠페인), 없으면 지금 칩 글자 */
export function resultTabTag(probeLabel: string, probe: string, rows: readonly CampaignRow[], measured: ReadonlySet<string>): string {
  if (probe === "failed" || probe === "checking" || rows.length === 0) return probeLabel;
  return `${NOT_MEASURED} ${rows.filter((r) => !measured.has(r.campaignId)).length}`;
}
