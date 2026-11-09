import { useCallback, useEffect, useState } from "react";
import { finance, humanTokenSet, krw, type CampaignRecord } from "@/lib/finance";
import { campaigns, type CampaignAnswer } from "@/lib/marketingLive";
import { dataAi, kpiLive, type CampaignEffectOutput } from "@/lib/data";
import { todayKst } from "@/lib/finance";

// 캠페인 시작·결과·취소. 시작은 재무 판단과 안전 장치를 지난다. 광고 플랫폼 연결이 없어
// 광고비는 사람이 보고서를 보고 적는다. 판매액·주문 수는 데이터 AI의 캠페인 효과에서 불러오거나
// 사람이 적는다. 어느 쪽인지 재무 기록에 남긴다(source). 사람이 고치면 사람이 적은 값이 된다.

const SOURCE = "marketing_screen_manual";

function explain(a: CampaignAnswer): string {
  const o = a.output ?? {};
  const review = (o.review ?? {}) as { reasons?: string[]; recommendation?: string };
  const reasons = [...(review.reasons ?? []), review.recommendation ?? ""].join(" ").trim();
  switch (a.outcome) {
    case "executed": return "집행이 승인됐습니다.";
    case "held": return "금액이 커서 사람 확인을 기다립니다. 위 보류 큐에서 결정하세요.";
    case "rejected_by_finance": return `재무가 거절했습니다. ${reasons}`.trim();
    case "over_approved_limit": return `재무 승인 한도를 넘었습니다. 금액을 줄여 다시 요청하세요. ${reasons}`.trim();
    case "rejected_by_review": return "사람이 거절했습니다.";
    case "finance_review_unavailable": return "재무 AI에 닿지 않아 시작하지 않았습니다.";
    case "recorded": return "재무에 손익을 기록했습니다. 재무 AI의 캠페인 재무 탭에서 볼 수 있습니다.";
    case "waiting_revenue": return "판매액이 없어 판단을 미뤘습니다. 판매액을 알면 다시 저장하세요.";
    case "not_started": return "시작하지 않은 캠페인이라 기록하지 않았습니다.";
    case "canceled": return "취소했습니다. 잡아 둔 예산이 풀렸습니다.";
    default: return `처리 결과: ${a.outcome ?? a.status}${reasons ? ` · ${reasons}` : ""}`;
  }
}

interface Effect {
  gmv: number | null; // 재무 칸에 넣는 값. 모르면 null, 증분이 음수면 0
  orders: number | null;
  measuredGmv: number | null; // 데이터가 잰 값 그대로
  measuredOrders: number | null;
  generatedAt: string | null;
  source: string;
  note: string;
}

const atLeastZero = (v: number | null) => (v === null ? null : Math.max(0, v));

/** campaign_effect → 재무 칸. gmv_krw = sales.incremental_gross_krw, orders = incremental_orders */
function toEffect(o: CampaignEffectOutput): Effect {
  const s = o.sales;
  const gross = s?.status === "known" ? s.incremental_gross_krw ?? null : null;
  const inc = o.incremental_orders ?? null;
  const notes: string[] = [];
  if (gross === null) notes.push(`판매액 확인할 수 없음: ${s?.unknown_reason ?? "응답에 판매액이 없습니다"}.`);
  else notes.push(`증분 판매액 ${krw(gross)} (기준선 ${s?.baseline_days ?? 0}일, 범위 ${krw(s?.incremental_low_krw ?? gross)} ~ ${krw(s?.incremental_high_krw ?? gross)}).`);
  if (s?.direction === "uncertain") notes.push("범위가 0에 걸쳐 늘었다고 단정할 수 없습니다.");
  notes.push(inc === null ? "증분 주문 수 확인할 수 없음." : `증분 주문 ${inc.toLocaleString("ko-KR")}건.`);
  if ((gross ?? 0) < 0 || (inc ?? 0) < 0) notes.push("기준선보다 줄어 재무 칸에는 0으로 넣고 잰 값을 함께 남깁니다.");
  return {
    gmv: atLeastZero(gross), orders: atLeastZero(inc), measuredGmv: gross, measuredOrders: inc,
    generatedAt: o.generated_at ?? null, source: `data.${o.schema_version || "campaign_effect"}`, note: notes.join(" "),
  };
}

/** 회사 날짜(KST) 구간 → 시각 구간. 끝 날은 포함하고, 아직 오지 않은 시각은 자른다 */
function windowOf(from: string, to: string): [string, string] {
  const end = new Date(new Date(`${to}T00:00:00+09:00`).getTime() + 86_400_000);
  const now = new Date();
  return [new Date(`${from}T00:00:00+09:00`).toISOString(), (end > now ? now : end).toISOString()];
}

export function CampaignControl() {
  const [name, setName] = useState("");
  const [channel, setChannel] = useState("");
  const [budget, setBudget] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState<{ campaign_id: string; active_krw: number }[]>([]);
  const [waiting, setWaiting] = useState<CampaignRecord[]>([]);
  const [names, setNames] = useState<Record<string, { name: string; channel: string }>>({});
  const [left, setLeft] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await finance.campaigns();
      setRunning(data.running);
      setWaiting(data.campaigns.filter((c) => c.status === "waiting_revenue"));
      setNames(Object.fromEntries(data.reviews.map((r) => [r.campaign_id, { name: r.campaign_name, channel: r.channel }])));
      setLeft(data.budget.committed_krw === null ? null : `${krw(Math.max(data.budget.monthly_krw - data.budget.committed_krw, 0))} 남음 · ${data.budget.basis}`);
    } catch { setLeft(null); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function run(job: () => Promise<CampaignAnswer>, after?: () => void) {
    setBusy(true);
    setProblem(null);
    setMessage(null);
    try {
      setMessage(explain(await job()));
      after?.();
      await load();
    } catch (e) { setProblem((e as Error).message); }
    finally { setBusy(false); }
  }

  const start = () => run(
    () => campaigns.start({ campaign_id: `cmp_${Date.now()}`, campaign_name: name.trim(), channel: channel.trim(), budget_krw: Number(budget) }),
    () => { setName(""); setBudget(""); },
  );

  return (
    <section className="card p-4 sm:p-5">
      <h2 className="text-base font-semibold text-ink-900">캠페인 집행</h2>
      <p className="mt-0.5 text-sm text-ink-500">시작하면 재무가 예산을 판단하고 안전 장치를 지납니다. 광고 플랫폼 연결이 없어 실제 광고는 나가지 않고, 광고비는 보고서를 보고 적습니다. 판매액과 주문 수는 데이터 AI에서 불러오거나 직접 적습니다.{left ? ` 이달 예산 ${left}.` : ""}</p>
      <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_1fr_160px_auto] sm:items-end">
        <label className="block text-sm"><span className="text-ink-700">캠페인 이름</span><input className="field mt-0.5 w-full" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="block text-sm"><span className="text-ink-700">채널</span><input className="field mt-0.5 w-full" placeholder="예: 네이버 검색" value={channel} onChange={(e) => setChannel(e.target.value)} /></label>
        <label className="block text-sm"><span className="text-ink-700">예산(원)</span><input className="field mt-0.5 w-full" inputMode="numeric" value={budget} onChange={(e) => setBudget(e.target.value.replace(/[^0-9]/g, ""))} /></label>
        <button className="btn-primary" disabled={busy || !humanTokenSet || !name.trim() || !channel.trim() || !Number(budget)} onClick={start}>시작 요청</button>
      </div>
      {message && <p role="status" className="mt-3 rounded-xl bg-brand-50 px-4 py-2 text-sm text-brand-700">{message}</p>}
      {problem && <p role="alert" className="mt-3 rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{problem}</p>}
      {(running.length > 0 || waiting.length > 0) && (
        <ul className="mt-4 space-y-3">
          {running.map((r) => (
            <ResultRow key={r.campaign_id} id={r.campaign_id} amount={r.active_krw} name={names[r.campaign_id]?.name ?? r.campaign_id} channel={names[r.campaign_id]?.channel ?? ""} busy={busy} run={run} canCancel />
          ))}
          {waiting.map((c) => (
            <ResultRow key={c.campaign_id} id={c.campaign_id} amount={c.spend_krw} name={c.campaign_name} channel={c.channel} busy={busy} run={run} canCancel={false} />
          ))}
        </ul>
      )}
    </section>
  );
}

function ResultRow({ id, amount, name, channel, busy, run, canCancel }: {
  id: string; amount: number; name: string; channel: string; busy: boolean; canCancel: boolean;
  run: (job: () => Promise<CampaignAnswer>) => Promise<void>;
}) {
  const today = todayKst();
  const [from, setFrom] = useState(today);
  const [to, setTo] = useState(today);
  const [spend, setSpend] = useState("");
  const [gmv, setGmv] = useState("");
  const [orders, setOrders] = useState("");
  const [coupon, setCoupon] = useState("");
  const [reason, setReason] = useState("");
  const [effect, setEffect] = useState<Effect | null>(null);
  const [effectNote, setEffectNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // 사람이 값을 고치면 불러온 값이 아니다. 기간을 바꾸면 불러온 값은 맞지 않아 지운다
  const typed = (set: (v: string) => void) => (e: { target: { value: string } }) => { set(e.target.value.replace(/[^0-9]/g, "")); setEffect(null); setEffectNote(null); };
  const period = (set: (v: string) => void) => (e: { target: { value: string } }) => {
    set(e.target.value);
    if (effect) { setGmv(""); setOrders(""); setEffect(null); }
    setEffectNote(null);
  };
  const periodOk = from !== "" && to !== "" && from <= to;
  async function pull() {
    setLoading(true);
    setEffectNote(null);
    try {
      const [start, end] = windowOf(from, to);
      const next = toEffect((await dataAi.campaignEffect(start, end, id)).output);
      setGmv(next.gmv === null ? "" : String(next.gmv));
      setOrders(next.orders === null ? "" : String(next.orders));
      setEffect(next.gmv === null && next.orders === null ? null : next);
      setEffectNote(next.note);
    } catch (e) {
      setEffect(null);
      setEffectNote(`불러오지 못했습니다: ${(e as Error).message}`);
    } finally { setLoading(false); }
  }
  const save = () => run(() => campaigns.result(id, {
    campaign_name: name, channel: channel || "미지정", period_from: from, period_to: to,
    spend_krw: Number(spend), coupon_krw: Number(coupon || 0), gmv_krw: gmv ? Number(gmv) : null, orders: orders ? Number(orders) : null,
    source: effect?.source ?? SOURCE,
    measured_gmv_krw: effect?.measuredGmv ?? null, measured_orders: effect?.measuredOrders ?? null, effect_generated_at: effect?.generatedAt ?? null,
  }));
  const digits = (set: (v: string) => void) => (e: { target: { value: string } }) => set(e.target.value.replace(/[^0-9]/g, ""));
  return (
    <li className="rounded-2xl border border-ink-100 p-4 text-sm">
      <p className="font-medium text-ink-900">{name} <span className="font-normal text-ink-500">{channel} · 승인 {krw(amount)}</span></p>
      <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <label className="block"><span className="text-ink-700">시작일</span><input type="date" className="field mt-0.5 w-full" value={from} onChange={period(setFrom)} /></label>
        <label className="block"><span className="text-ink-700">종료일</span><input type="date" className="field mt-0.5 w-full" value={to} onChange={period(setTo)} /></label>
        <button className="btn-ghost" disabled={loading || !kpiLive || !periodOk} onClick={() => void pull()}>{loading ? "불러오는 중" : "데이터 AI에서 불러오기"}</button>
      </div>
      {effectNote && <p role="status" className="mt-2 rounded-xl bg-ink-50 px-3 py-2 text-ink-700">{effectNote}</p>}
      <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_1fr_1fr_1fr_auto] sm:items-end">
        <label className="block"><span className="text-ink-700">실제 광고비(원)</span><input className="field mt-0.5 w-full" inputMode="numeric" value={spend} onChange={digits(setSpend)} /></label>
        <label className="block"><span className="text-ink-700">늘어난 판매액(원, 모르면 비움)</span><input className="field mt-0.5 w-full" inputMode="numeric" value={gmv} onChange={typed(setGmv)} /></label>
        <label className="block"><span className="text-ink-700">증분 주문(건, 모르면 비움)</span><input className="field mt-0.5 w-full" inputMode="numeric" value={orders} onChange={typed(setOrders)} /></label>
        <label className="block"><span className="text-ink-700">회사 부담 쿠폰(원)</span><input className="field mt-0.5 w-full" inputMode="numeric" value={coupon} onChange={digits(setCoupon)} /></label>
        <button className="btn-primary" disabled={busy || loading || !humanTokenSet || spend === "" || !periodOk} onClick={save}>결과 저장</button>
      </div>
      {canCancel && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input className="field min-w-0 flex-1" aria-label="취소 이유" placeholder="쓰지 않고 멈출 때 취소 이유" value={reason} onChange={(e) => setReason(e.target.value)} />
          <button className="btn-ghost" disabled={busy || !humanTokenSet || !reason.trim()} onClick={() => run(() => campaigns.cancel(id, reason.trim()))}>캠페인 취소</button>
        </div>
      )}
    </li>
  );
}
