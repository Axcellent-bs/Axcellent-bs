// 영상 제작안 스토리보드 화면(components/planning/storyboard.tsx)의 순수 계산. node --test (videoStoryboard.test.ts)
// 11/23 재현 님 시안 ④: 콘셉트 → 길이·비율·장면·대상·근거 → 장면 시간표 막대 → 장면 카드 → 대본·프롬프트·근거 접기
// 값은 기획 state 그대로다. 장면 역할(훅·전환·증거·마무리)은 계약에 칸이 없어 장면 자리로만 붙인다(지어낸 내용이 아니라 순서 이름)
import type { VideoSceneView } from "./videoPlan.ts";
import { requestFilterOf } from "./requestVideoView.ts";

/** 장면 역할 이름: 첫 장면 훅, 마지막 마무리, 4장면 이상이면 둘째 전환, 나머지 증거. 장면이 하나면 훅 */
export function sceneRole(i: number, total: number): string {
  if (i === 0) return "훅";
  if (i === total - 1) return "마무리";
  if (i === 1 && total >= 4) return "전환";
  return "증거";
}

/** 시간표 막대 폭(%): 장면 길이 비율. 합이 100이 되게 마지막 칸이 나머지를 갖는다. 길이를 모르면 똑같이 나눈다 */
export function sceneShares(scenes: Pick<VideoSceneView, "startS" | "endS">[]): number[] {
  if (scenes.length === 0) return [];
  const total = scenes.reduce((a, s) => a + Math.max(0, s.endS - s.startS), 0);
  const raw = total > 0 ? scenes.map((s) => (Math.max(0, s.endS - s.startS) / total) * 100) : scenes.map(() => 100 / scenes.length);
  const out = raw.map((x) => Math.round(x * 10) / 10);
  out[out.length - 1] = Math.round((100 - out.slice(0, -1).reduce((a, b) => a + b, 0)) * 10) / 10;
  return out;
}

const REPORT_PREFIX = "trend_report:";

/** 근거 줄: 분석 보고서 번호(제안이 보고서에서 왔을 때만 "TR-xxxx") + 핵심 근거 앞 2개 "이름 값" */
export function storyBasis(p: { insightId?: string | null; facts: { label: string; value: string }[] }): { report: string | null; points: string[] } {
  const rid = p.insightId?.startsWith(REPORT_PREFIX) ? p.insightId.slice(REPORT_PREFIX.length) : null;
  const report = rid ? `TR-${rid.replace(/^tr_/, "").slice(0, 4).toUpperCase()}` : null;
  const points = p.facts
    .map((f) => [f.label, f.value].map((x) => (x ?? "").trim()).filter(Boolean).join(" "))
    .filter(Boolean)
    .slice(0, 2);
  return { report, points };
}

/** 대화 카드에서 들어온 의뢰 번호 (?request=wr_…). 형식이 다르면 null (서버에 보내지 않는다) */
export function requestOfSearch(search: string): string | null {
  return requestFilterOf(new URLSearchParams(search).get("request"));
}
