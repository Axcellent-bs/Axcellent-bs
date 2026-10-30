// 재무 화면 "예시 데이터" 모드. 서버 대신 화면 안의 예시 응답(financeExample.json)으로 재무 AI 흐름을 보여 준다.
// 예시 응답은 재무 백엔드를 목 시드로 돌린 결과다(`backend/domains/finance/demo/export_example.py`로 다시 만든다).
// 사람이 누른 결정(결산 확정·대사 메모·정산 요청·감사 처리·경보 확인·보류 큐 결정)은 이 브라우저 화면 안에서만
// 바뀌고 서버에는 아무것도 보내지 않는다. 새로고침하면 처음 예시로 돌아간다.
import raw from "./financeExample.json";
import type {
  Anomaly, AnomalyList, ClosingReport, FinanceOverview, FinanceTodo, Reconciliation, Review, Settlements,
} from "./finance";

const KEY = "axcellent-finance-example";
const PERSON = "예시 사용자";

/** 예시에 없는 자료를 달라고 할 때. 호출 쪽이 쉬운 안내로 바꿔 보여 준다 */
export class ExampleMissing extends Error {}

export function exampleOn(): boolean {
  try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
}
export function setExampleOn(on: boolean): void {
  try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* 저장이 막혀도 이번 화면에서는 바뀐다 */ }
  reset();
}

type Body = { data: unknown; meta: Record<string, unknown> };
let store: Record<string, Body> = {};
let reviews: Review[] = [];
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
function reset() {
  store = clone(raw) as unknown as Record<string, Body>;
  reviews = [];
}
reset();

const now = () => new Date().toISOString();
const dataOf = <T,>(path: string) => store[path]?.data as T | undefined;
const META = { mock: true, source: "example" };

function overview(): FinanceOverview | undefined {
  return dataOf<FinanceOverview>("/api/finance/overview");
}
/** 할 일 카드에서 처리한 건을 빼고, 다 처리했으면 카드를 없앤다 */
function dropTodoItem(kind: FinanceTodo["kind"], ref: string) {
  const o = overview();
  const t = o?.todo.find((x) => x.kind === kind);
  if (!o || !t?.items) return;
  const before = t.items.length;
  t.items = t.items.filter((i) => i.ref !== ref);
  // 카드에는 앞쪽 몇 건만 실려 있어 전체 건수에서 처리한 만큼만 뺀다
  t.count = Math.max((t.count ?? 0) - (before - t.items.length), t.items.length);
  t.title = t.title.replace(/\d+건/, `${t.count}건`);
  if (!t.count) o.todo = o.todo.filter((x) => x !== t);
}
function dropTodo(kind: FinanceTodo["kind"]) {
  const o = overview();
  if (o) o.todo = o.todo.filter((x) => x.kind !== kind);
}

/** 기본 주소로 찾고, 없으면 날짜를 뺀 주소의 응답이 같은 날짜일 때 그것을 쓴다 */
function lookup(path: string): Body {
  if (store[path]) return store[path];
  const [base, q = ""] = path.split("?");
  const params = new URLSearchParams(q);
  const date = params.get("date");
  params.delete("date");
  const rest = params.toString();
  const plain = store[rest ? `${base}?${rest}` : base];
  if (plain && (!date || (plain.data as { date?: string } | null)?.date === date)) return plain;
  throw new ExampleMissing("예시 데이터에는 이 날짜·기간 자료가 없습니다. '실제 데이터'로 바꿔서 보세요.");
}

function json(body: unknown): Record<string, unknown> {
  return body && typeof body === "object" ? (body as Record<string, unknown>) : {};
}

async function financeCall(method: string, path: string, body: Record<string, unknown>): Promise<unknown> {
  if (method === "GET") {
    if (path === "/health") return { status: "ok" };
    if (path === "/api/finance/self-test") throw new ExampleMissing("예시 데이터에서는 재무 AI 테스트를 돌리지 않습니다.");
    return lookup(path);
  }
  if (path === "/api/finance/closing/confirm") {
    const date = String(body.date ?? "");
    const target = [`/api/finance/closing?period=${body.period}&date=${date}`, `/api/finance/closing?period=${body.period}`]
      .map((p) => store[p]).find((b) => (b?.data as ClosingReport | undefined)?.date === date);
    if (!target) throw new ExampleMissing("예시 데이터에는 이 날짜 결산이 없습니다.");
    Object.assign(target.data as ClosingReport, { status: "confirmed", confirmed_by: PERSON, confirmed_at: now() });
    const o = overview();
    if (o) o.todo = o.todo.filter((t) => !(t.kind === "closing_confirm" && t.action?.date === date));
    return target;
  }
  const recon = /^\/api\/finance\/reconciliation\/([^/]+)\/note$/.exec(path);
  if (recon) {
    const id = decodeURIComponent(recon[1]);
    const item = dataOf<Reconciliation>("/api/finance/reconciliation")?.items.find((i) => i.payment_id === id);
    if (!item) throw new ExampleMissing("예시 데이터에 없는 결제입니다.");
    item.note = String(body.note ?? "");
    dropTodoItem("recon_exception", id);
    return { data: item, meta: { ...META, noted_by: PERSON } };
  }
  if (path === "/api/finance/settlements/batch") {
    const sett = store["/api/finance/settlements"];
    const s = sett?.data as Settlements | undefined;
    if (!s) throw new ExampleMissing("예시 데이터에 정산 내역이 없습니다.");
    const ready = s.statements.filter((r) => !r.hold_reasons.length && (r.status === "ready" || r.status === "rejected"));
    const reviewId = `rv_example_${s.period}`;
    ready.forEach((r) => Object.assign(r, { status: "requested", review_id: reviewId }));
    const total = ready.reduce((n, r) => n + (r.payout ?? 0), 0);
    s.batch = { status: "requested", review_id: reviewId, execution_id: `exe_example_${s.period}`, total_payout: total, seller_ids: ready.map((r) => r.seller_id) };
    const created = now();
    reviews.unshift({
      review_id: reviewId, execution_id: s.batch.execution_id ?? "", context_id: `ctx_req_settlement_${s.period}`,
      action: "confirm_settlement", requester: "finance", created_at: created,
      expires_at: new Date(Date.now() + 10 * 60_000).toISOString(), status: "pending",
      decided_by: null, decided_at: null, note: null,
      report: { hold_reason: "always_human", totals: { total_payout: total, sellers: ready.length } },
    });
    const t = overview()?.todo.find((x) => x.kind === "settlement_batch");
    if (t) Object.assign(t, {
      title: `판매자 정산 ${ready.length}곳 지급 승인 대기 (안전 장치)`,
      action: { type: "review_decision", period: s.period, review_id: reviewId },
    });
    return sett;
  }
  const anomaly = /^\/api\/finance\/anomalies\/([^/]+)\/resolve$/.exec(path);
  if (anomaly) {
    const ref = decodeURIComponent(anomaly[1]);
    const list = dataOf<AnomalyList>("/api/finance/anomalies");
    const entry = list?.open.find((a) => a.finding_ref === ref);
    if (!list || !entry) throw new ExampleMissing("예시 데이터에 없는 의심 건입니다.");
    const done: Anomaly = { ...entry, status: "resolved", resolved_by: PERSON, resolved_at: now(), note: String(body.note ?? "") };
    list.open = list.open.filter((a) => a !== entry);
    list.open_count = list.open.length;
    list.resolved = [done, ...list.resolved];
    dropTodoItem("audit_finding", ref);
    return { data: done, meta: { ...META, resolved_by: PERSON } };
  }
  const ack = /^\/api\/finance\/policy\/([^/]+)\/ack$/.exec(path);
  if (ack) {
    const key = decodeURIComponent(ack[1]);
    dropTodoItem("policy_alert", key);
    return { data: { key, note: String(body.note ?? ""), by: PERSON, at: now() }, meta: { ...META, acked_by: PERSON } };
  }
  throw new ExampleMissing("예시 데이터에서는 이 동작을 할 수 없습니다.");
}

async function safetyCall(method: string, path: string, body: Record<string, unknown>): Promise<unknown> {
  if (method === "GET") {
    if (path === "/health") return { status: "ok" };
    if (path === "/api/safety/reviews") return { data: reviews };
    if (path === "/api/safety/executions") return { data: [] };
    throw new ExampleMissing("예시 데이터에는 이 안전 장치 자료가 없습니다.");
  }
  const decision = /^\/api\/safety\/reviews\/([^/]+)\/decision$/.exec(path);
  const review = decision && reviews.find((r) => r.review_id === decodeURIComponent(decision[1]));
  if (!review) throw new ExampleMissing("예시 데이터에 없는 보류 건입니다.");
  const approved = body.decision === "approved";
  Object.assign(review, { status: approved ? "approved" : "rejected", decided_by: PERSON, decided_at: now(), note: (body.note as string | null) ?? null });
  const s = dataOf<Settlements>("/api/finance/settlements");
  if (s && s.batch.review_id === review.review_id) {
    s.batch.status = approved ? "approved" : "rejected";
    s.statements.filter((r) => r.review_id === review.review_id).forEach((r) => { r.status = approved ? "approved" : "rejected"; });
    if (approved) dropTodo("settlement_batch");
    else {
      const t = overview()?.todo.find((x) => x.kind === "settlement_batch");
      if (t) Object.assign(t, { title: "판매자 정산 지급 요청이 멈춤. 다시 요청할 수 있음", action: { type: "settlement_request", period: s.period } });
    }
  }
  return { data: review };
}

/** 예시 모드의 서버 호출. 응답 모양은 실제 서버와 같다 */
export async function exampleCall(target: "finance" | "safety", path: string, method: string, body: unknown): Promise<unknown> {
  const result = target === "finance" ? await financeCall(method, path, json(body)) : await safetyCall(method, path, json(body));
  return clone(result);
}
