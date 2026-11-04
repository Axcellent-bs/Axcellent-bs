import { useCallback, useEffect, useState } from "react";
import { finance, humanTokenSet, krw, type CampaignRecord } from "@/lib/finance";
import { campaigns, type CampaignAnswer } from "@/lib/marketingLive";
import { todayKst } from "@/lib/finance";

// 캠페인 시작·결과·취소. 시작은 재무 판단과 안전 장치를 지난다. 광고 플랫폼 연결이 없어
// 광고비와 판매액은 사람이 보고서를 보고 적는다. 적은 값이라는 것을 재무 기록에 남긴다(source).

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
      <p className="mt-0.5 text-sm text-ink-500">시작하면 재무가 예산을 판단하고 안전 장치를 지납니다. 광고 플랫폼 연결이 없어 실제 광고는 나가지 않고, 광고비와 판매액은 보고서를 보고 적습니다.{left ? ` 이달 예산 ${left}.` : ""}</p>
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
  const [spend, setSpend] = useState("");
  const [gmv, setGmv] = useState("");
  const [coupon, setCoupon] = useState("");
  const [reason, setReason] = useState("");
  const today = todayKst();
  const save = () => run(() => campaigns.result(id, {
    campaign_name: name, channel: channel || "미지정", period_from: today, period_to: today,
    spend_krw: Number(spend), coupon_krw: Number(coupon || 0), gmv_krw: gmv ? Number(gmv) : null, source: SOURCE,
  }));
  const digits = (set: (v: string) => void) => (e: { target: { value: string } }) => set(e.target.value.replace(/[^0-9]/g, ""));
  return (
    <li className="rounded-2xl border border-ink-100 p-4 text-sm">
      <p className="font-medium text-ink-900">{name} <span className="font-normal text-ink-500">{channel} · 승인 {krw(amount)}</span></p>
      <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_1fr_1fr_auto] sm:items-end">
        <label className="block"><span className="text-ink-700">실제 광고비(원)</span><input className="field mt-0.5 w-full" inputMode="numeric" value={spend} onChange={digits(setSpend)} /></label>
        <label className="block"><span className="text-ink-700">늘어난 판매액(원, 모르면 비움)</span><input className="field mt-0.5 w-full" inputMode="numeric" value={gmv} onChange={digits(setGmv)} /></label>
        <label className="block"><span className="text-ink-700">회사 부담 쿠폰(원)</span><input className="field mt-0.5 w-full" inputMode="numeric" value={coupon} onChange={digits(setCoupon)} /></label>
        <button className="btn-primary" disabled={busy || !humanTokenSet || spend === ""} onClick={save}>결과 저장</button>
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
