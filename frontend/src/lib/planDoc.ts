// 기획서·기획 제안 "PDF로 저장"의 순수 계산 (인쇄 머리글·열 경로). node --test (planDoc.test.ts)
// 인쇄 동작(body 클래스 print-doc + .doc-print만 남김)은 lib/print.ts · styles.css. 보고서 인쇄(print-trend-report)와 같은 방식
import { planId } from "./format.ts";

/** 인쇄 때만 붙는 머리글 한 줄: "기획서 23 · 회사 이름 · 만든 시각 · 상태"(제안이면 "제안 15"). 모르는 칸은 뺀다 */
export function planPrintHeader(x: { id: string; company?: string | null; at?: string | null; status?: string | null }): string {
  return [planId(x.id), x.company?.trim() || null, x.at?.trim() || null, x.status?.trim() || null].filter(Boolean).join(" · ");
}

/** 기획서를 만든 시각: 기획서에는 시각 칸이 없어 서버 활동 기록의 write_spec 줄(그 기획서 번호) 중 가장 이른 것. 없으면 null */
export function specCreatedAt(activity: { t: string; point: string; text: string }[], specId: string): string | null {
  const id = new RegExp(`(^|[^\\w-])${specId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\w-])`);
  const ts = activity.filter((a) => a.point === "write_spec" && a.t && id.test(a.text)).map((a) => a.t).sort();
  return ts[0] ?? null;
}

/** 인쇄로 열기 쿼리 (?print=1). 그 화면이 그려진 뒤 한 번 인쇄하고 주소에서 지운다 */
export const PRINT_QUERY = "print";
export const wantsPrint = (search: string) => new URLSearchParams(search).get(PRINT_QUERY) === "1";

/** 기획서가 있으면 기획서 화면, 없으면 제안 화면. print면 ?print=1.
 *  대화방 의뢰 카드에서 열면 ?request=wr_… (그 화면 아래 고정 줄에서 의뢰 사람 확인을 한다, 11/23) */
export function planDocPath(x: { id: string; spec?: string | null; request?: string | null }, print = false): string {
  const path = x.spec ? `/planning/work/specs/${encodeURIComponent(x.spec)}` : `/planning/proposals/${encodeURIComponent(x.id)}`;
  const q = new URLSearchParams();
  if (print) q.set(PRINT_QUERY, "1");
  if (x.request) q.set("request", x.request);
  const qs = q.toString();
  return qs ? `${path}?${qs}` : path;
}
