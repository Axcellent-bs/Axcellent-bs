// 안전 처리 로그 합치기 (AI 대화방·AI 오피스 단톡방, lib/agentChat.ts buildTimeline이 쓴다). node --test (safetyFold.test.ts)
// 11/20 16:28: 같은 일 하나에 "실행 요청" · "✓ 검증 통과" · "✓ 실행함" · "실행해도 되는지 확인하고 있어요" · "실행해도 돼요"가
// 말풍선 하나씩 차지했다. 통과한 확인 줄은 그 요청 말풍선 옆 "🛡 안전 확인 통과" 칩 하나로 합친다(눌러서 펼침).
// - 묶는 기준: 같은 execution_id(execId). execution_id가 없는 safety 줄은 2분 안에 바로 앞서 그 부서(받는 쪽)가 보낸 요청 줄에
// - 통과 줄만 합친다: 실행 요청(:req) · 검증 통과(tone ok) · 실행함(tone ok) · safety의 in_progress·completed
// - 한 묶음에 보류·거절·사람 확인·실패·검증 중 줄이 하나라도 있으면 그 묶음은 합치지 않는다 (지금처럼 따로 크게)
// - 안전 처리 로그 방(SAFETY_ROOM)은 부르지 않는다 (그 방은 하나하나 본다)

export interface FoldLine { text: string; at: string; from: string }

type Msg = {
  kind: "msg"; id: string; at: string; fromKey: string; from: string; text: string; tone: string;
  toKey?: string; execId?: string; human?: boolean; safety?: FoldLine[]; safetyRole?: "request" | "check";
};
type Item = { kind: string; id: string; at: string };

const FOLD_WINDOW_MS = 2 * 60_000;
const PASS_TONES = new Set(["ok", "request"]);
const ms = (iso: string) => { const t = Date.parse(iso); return Number.isFinite(t) ? t : 0; };
const isMsg = (it: Item): it is Item & Msg => it.kind === "msg";

/** 안전 확인 흐름의 줄인가: 안전 관문 실행 기록(safetyRole)이거나 safety가 보낸 공통 로그 줄 */
const isSafetyLine = (m: Msg) => !!m.safetyRole || m.fromKey === "safety";
/** 통과 줄 (합쳐도 되는 줄) */
const isPass = (m: Msg) => PASS_TONES.has(m.tone);

export function foldSafety<T extends Item>(items: readonly T[]): T[] {
  const msgs = items.filter(isMsg) as (T & Msg)[];
  // execId 묶음: 안전 흐름 줄이 하나라도 통과가 아니면 그 묶음은 그대로
  const groups = new Map<string, (T & Msg)[]>();
  for (const m of msgs) if (m.execId) (groups.get(m.execId) ?? groups.set(m.execId, []).get(m.execId)!).push(m);
  const anchorOf = new Map<string, string>(); // 합칠 줄 id → 붙일 말풍선 id
  for (const g of groups.values()) {
    const safety = g.filter(isSafetyLine);
    if (safety.length === 0 || !safety.every(isPass)) continue;
    // 붙일 말풍선: 안전 흐름 밖의 같은 건 줄(부서가 보낸 요청) → 없으면 실행 요청(:req) 줄 → 없으면 첫 줄
    const anchor = g.find((m) => !isSafetyLine(m) && !m.human) ?? g.find((m) => m.safetyRole === "request") ?? g[0];
    for (const m of safety) if (m.id !== anchor.id) anchorOf.set(m.id, anchor.id);
  }
  // execution_id 없는 safety 줄(공통 로그): 2분 안에 바로 앞서 받는 부서가 보낸 요청 줄에
  const lastFrom = new Map<string, T & Msg>();
  for (const m of msgs) {
    if (!m.execId && m.fromKey === "safety" && isPass(m) && m.toKey) {
      const req = lastFrom.get(m.toKey);
      if (req && ms(m.at) - ms(req.at) <= FOLD_WINDOW_MS) { anchorOf.set(m.id, anchorOf.get(req.id) ?? req.id); continue; }
    }
    if (!isSafetyLine(m) && !m.human) lastFrom.set(m.fromKey, m);
  }
  if (anchorOf.size === 0) return [...items];

  const folded = new Map<string, FoldLine[]>();
  for (const m of msgs) {
    const a = anchorOf.get(m.id);
    if (a) (folded.get(a) ?? folded.set(a, []).get(a)!).push({ text: m.text, at: m.at, from: m.from });
  }
  const out: T[] = [];
  for (const it of items) {
    if (anchorOf.has(it.id)) continue;
    const add = folded.get(it.id);
    out.push(add && isMsg(it) ? ({ ...it, safety: [...((it as T & Msg).safety ?? []), ...add] } as T) : it);
  }
  return out;
}
