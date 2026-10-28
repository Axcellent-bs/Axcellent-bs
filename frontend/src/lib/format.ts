export const won = (n: number) => `${n.toLocaleString("ko-KR")}원`;

const kst = (iso: string) => new Date(iso);

export function dateStr(iso: string | null | undefined) {
  if (!iso) return "–";
  return kst(iso).toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric" });
}

export function shortDateTime(iso: string | null | undefined) {
  if (!iso) return "–";
  return kst(iso).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function ago(iso: string) {
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (min < 1) return "방금 전";
  if (min < 60) return `${min}분 전`;
  if (min < 60 * 24) return `${Math.round(min / 60)}시간 전`;
  return `${Math.round(min / 60 / 24)}일 전`;
}

export const pct = (v: number | null | undefined, digits = 0) => (v == null ? "–" : `${(v * 100).toFixed(digits)}%`);
export const shortDate = (iso: string) => { const d = new Date(iso + "T00:00:00"); return `${d.getMonth() + 1}/${d.getDate()}`; };
export const longDate = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("ko-KR", { month: "long", day: "numeric", weekday: "short" });

// ---------------------------------------------------------------- 기록 번호 → 쉬운 말
// 화면 문장에서 기록 번호(buy_0007, ord_2156, ctx_inc_0100 …)를 "고객 0007", "주문 2156", "장애 #0100"처럼 바꾼다.
// 원래 번호는 직원이 찾아볼 수 있게 "기록 번호 보기"에 따로 둔다.

const ID_WORD: Record<string, string> = {
  buy: "고객", ord: "주문", pay: "결제", rfd: "환불", cpn: "쿠폰", exe: "처리 기록", rev: "보류 건", sel: "판매자",
};

/** 문장 속 기록 번호를 찾는 식. `ctx_inc_…`(장애 사건)과 위 접두어만 바꾸고 나머지는 그대로 둔다 */
const ID_RE = /\b(?:ctx_inc_[A-Za-z0-9_]*[A-Za-z0-9]|(?:buy|ord|pay|rfd|cpn|exe|rev|sel)_[A-Za-z0-9]+)\b/g;

/** "ctx_inc_scn_a_0001" → "0001", "ctx_inc_0100" → "0100" */
export const incidentNo = (contextId: string) => contextId.replace(/^ctx_inc_/, "").replace(/^scn_[a-z0-9]+_/i, "");

/** 기록 번호 하나를 쉬운 말로. 판매자는 이름을 알면 이름. 모르는 모양이면 그대로 */
export function humanId(id: string, sellers?: Record<string, string>): string {
  if (id.startsWith("ctx_inc_")) return `장애 #${incidentNo(id)}`;
  const m = /^([a-z]+)_(.+)$/.exec(id);
  if (!m || !(m[1] in ID_WORD)) return id;
  if (m[1] === "sel" && sellers?.[id]) return sellers[id];
  return `${ID_WORD[m[1]]} ${m[2]}`;
}

/** 바로 앞 낱말까지 같이 본다: "주문 ord_2156" → "주문 2156" ("주문 주문 2156"이 되지 않게) */
const WORD_ID_RE = new RegExp(`(?:(주문|결제|환불|쿠폰|고객|판매자|사건|장애) )?(${ID_RE.source})`, "g");

/** 문장 속 기록 번호를 모두 쉬운 말로 바꾼다 */
export function humanizeIds(text: string, sellers?: Record<string, string>): string {
  return text.replace(WORD_ID_RE, (_all, before: string | undefined, id: string) => {
    const word = humanId(id, sellers);
    if (!before) return word;
    if (id.startsWith("ctx_inc_") && (before === "사건" || before === "장애")) return word;
    if (word.startsWith(`${before} `)) return word;
    return `${before} ${word}`;
  });
}

/** 쉬운 말로 바꿀 수 있는 기록 번호인지 (바꿔도 같으면 false) */
export const isKnownId = (id: string) => humanId(id) !== id;
