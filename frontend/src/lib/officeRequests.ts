// AI 오피스 자리 불: 고객 의뢰(wr_, GET /api/requests) 단계 → 부서 자리 상태 (lib/office.ts deriveOffice가 쓴다). node --test (officeRequests.test.ts).
// 11/20 14:36 의뢰 wr_732efb95e9: 데이터가 보고서를 만드는 20초 동안 데이터분석 자리에 불이 켜지지 않았다.
// deriveOffice가 자리 상태를 장애 사건(ctx_inc_) 단계로만 정했기 때문이다. 의뢰 단계도 같은 규칙으로 본다.
// - 열린 의뢰(OPEN_REQUEST_STATES) 중 가장 최근 updated_at 하나만
// - 단계 running·waiting_async → 그 부서 자리 working + 말풍선(단계 label, note에 진행률 %가 있으면 붙임. 부서 문장은 옮기지 않는다)
// - waiting_human(사람 확인·승인) → 총괄 자리 waiting, held → 안전 처리 로그 자리 waiting (사건 규칙과 같다)
// - 사건 단계가 이미 쓰는 자리는 사건이 먼저 (장애 대응 우선)
// - 11/20: 오전 12:40 의뢰 2개가 오후 내내 총괄 "승인 대기"를 띄웠다. updated_at이 STALE_HOURS 넘게 지난 열린 의뢰는 자리 상태에 쓰지 않는다
//   (대화방 의뢰 진행 카드·고객 의뢰 넘김 카드에는 그대로 보이고 "오래 멈춤 · N시간" 칩이 붙는다: staleHours)
import { isOpenRequest, type WorkRequestRow } from "./workRequests.ts";

/** 열린 의뢰가 이 시간 넘게 바뀌지 않으면 "오래 멈춤" (자리 상태에서 뺀다) */
export const STALE_HOURS = 6;

/** 오래 멈춘 열린 의뢰면 멈춘 시간(시간 단위, 내림), 아니면 null. 끝난 의뢰·시각을 못 읽으면 null */
export function staleHours(r: Pick<WorkRequestRow, "status" | "updated_at">, now: number): number | null {
  if (!isOpenRequest(r)) return null;
  const t = Date.parse(r.updated_at);
  if (!Number.isFinite(t)) return null;
  const h = (now - t) / 3_600_000;
  return h > STALE_HOURS ? Math.floor(h) : null;
}

export interface RequestDesk { desk: string; state: "working" | "waiting"; bubble: string | null; skill: string }

/** note 안의 진행률 "45%" (코드 문장, 없으면 null) */
export function progressOf(note: string | null | undefined): number | null {
  const m = /(\d{1,3})\s*%/.exec(note ?? "");
  if (!m) return null;
  const n = Number(m[1]);
  return n >= 0 && n <= 100 ? n : null;
}

/** 가장 최근(updated_at) 열린 의뢰. now를 주면 오래 멈춘 의뢰(staleHours)는 뺀다 */
export function latestOpenRequest(requests: WorkRequestRow[] | null | undefined, now?: number): WorkRequestRow | null {
  let best: WorkRequestRow | null = null;
  for (const r of requests ?? []) {
    if (!isOpenRequest(r)) continue;
    if (now !== undefined && staleHours(r, now) !== null) continue;
    if (!best || r.updated_at > best.updated_at) best = r;
  }
  return best;
}

/** 의뢰 단계 → 자리 상태 (plan 순서). desk는 서버 부서 이름 그대로(화면 자리로 바꾸는 건 부르는 쪽). now를 주면 오래 멈춘 의뢰는 쓰지 않는다(deriveOffice가 준다) */
export function requestDesks(requests: WorkRequestRow[] | null | undefined, now?: number): RequestDesk[] {
  const r = latestOpenRequest(requests, now);
  if (!r) return [];
  const out: RequestDesk[] = [];
  for (const p of r.plan) {
    const s = r.steps[p.step_id];
    if (!s) continue;
    if ((s.status === "running" || s.status === "waiting_async") && p.dept) {
      const pct = progressOf(s.note);
      out.push({ desk: p.dept, state: "working", bubble: pct === null ? p.label : `${p.label} ${pct}%`, skill: p.skill });
    } else if (s.status === "waiting_human") {
      out.push({ desk: "orchestrator", state: "waiting", bubble: null, skill: p.skill });
    } else if (s.status === "held") {
      out.push({ desk: "safety", state: "waiting", bubble: null, skill: p.skill });
    }
  }
  return out;
}

/**
 * 의뢰 자리 상태를 자리 표에 얹는다. 사건 단계가 이미 쓰는(idle이 아닌) 자리는 건드리지 않는다.
 * deskOf: 서버 부서 이름 → 화면 자리 (lib/office.ts deskOf). 자리가 없으면 건너뛴다
 */
export function applyRequestDesks<K extends string>(
  desks: Record<K, { state: "idle" | "working" | "waiting"; bubble: { text: string; kind: string } | null; skill: string | null }>,
  reqDesks: RequestDesk[],
  deskOf: (name: string) => K | null,
): void {
  for (const d of reqDesks) {
    const k = deskOf(d.desk);
    if (!k || desks[k].state !== "idle") continue;
    desks[k] = {
      state: d.state,
      bubble: d.state === "working" && d.bubble ? { text: d.bubble, kind: "think" } : null,
      skill: d.skill,
    };
  }
}
