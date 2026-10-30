import { useEffect, useState } from "react";
import type { OrchIncident } from "@/lib/agentChat";
import { hr, type HrCard } from "@/lib/hr";

// 이벤트를 고친 사람이 "무슨 일이었고 어떻게 고쳤는지"를 적는 칸. 비슷한 장애가 다시 나면 총괄이 이 기록을 근거로 보여 준다.
// 소요 시간은 적지 않는다: 이벤트가 열린 시각부터 저장한 시각까지를 서버가 잰다.
const OUTCOME_KO = { resolved: "해결됨", workaround: "임시 조치", unresolved: "미해결" } as const;
type Outcome = keyof typeof OUTCOME_KO;
const MAX = 300;

interface Saved { outcome?: string; root_cause?: string | null; action?: string | null; minutes_to_resolve?: number | null }

export default function ResolutionForm({ inc }: { inc: OrchIncident }) {
  const [saved, setSaved] = useState<Saved | null>(null);
  const [outcome, setOutcome] = useState<Outcome>("resolved");
  const [rootCause, setRootCause] = useState("");
  const [action, setAction] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // 이미 적은 해결 내용이 있으면 보여 준다 (카드의 extra.resolution)
  useEffect(() => {
    let alive = true;
    setSaved(null); setMsg(null);
    hr.decisionCards(inc.context_id, 5)
      .then((cards: HrCard[]) => {
        const hit = cards.find((c) => c.extra && typeof c.extra.resolution === "object" && c.extra.resolution);
        if (alive) setSaved((hit?.extra.resolution as Saved | undefined) ?? null);
      })
      .catch(() => { /* 조회 실패는 입력을 막지 않는다 */ });
    return () => { alive = false; };
  }, [inc.context_id]);

  const prior = inc.recommendation?.status === "found" ? inc.recommendation.card_id ?? null : null;

  async function save() {
    setBusy(true); setMsg(null);
    try {
      await hr.recordResolution(inc.context_id, {
        outcome,
        root_cause: rootCause.trim() || undefined,
        action: action.trim() || undefined,
        opened_at: inc.opened_at,
        prior_case_id: prior,
      });
      setMsg({ ok: true, text: "해결 내용을 남겼어요. 같은 장애가 다시 나면 근거로 보여 줘요." });
      const cards = await hr.decisionCards(inc.context_id, 5);
      const hit = cards.find((c) => c.extra && typeof c.extra.resolution === "object" && c.extra.resolution);
      setSaved((hit?.extra.resolution as Saved | undefined) ?? null);
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "저장하지 못했습니다" });
    } finally { setBusy(false); }
  }

  return (
    <section className="card space-y-3 p-6">
      <div>
        <h2 className="text-lg font-semibold">이 이벤트의 해결 내용 적기</h2>
        <p className="mt-1 text-xs text-ink-500">원인을 확인하고 고친 사람이 적습니다. 비워 두면 "확인할 수 없음"으로 남고, 소요 시간은 이벤트가 열린 때부터 저장할 때까지 자동으로 잽니다.</p>
      </div>
      {saved && (
        <p className="rounded-xl bg-emerald-50 px-4 py-2 text-xs text-emerald-800">
          지금 남아 있는 기록: {OUTCOME_KO[saved.outcome as Outcome] ?? saved.outcome} · 원인 {saved.root_cause ?? "확인할 수 없음"} · 조치 {saved.action ?? "확인할 수 없음"}
          {saved.minutes_to_resolve != null && ` · ${saved.minutes_to_resolve}분`}
        </p>
      )}
      <div className="grid gap-3 sm:grid-cols-[10rem_1fr]">
        <label className="text-sm font-medium" htmlFor="res-outcome">결과</label>
        <select id="res-outcome" className="field" value={outcome} onChange={(e) => setOutcome(e.target.value as Outcome)}>
          {(Object.keys(OUTCOME_KO) as Outcome[]).map((k) => <option key={k} value={k}>{OUTCOME_KO[k]}</option>)}
        </select>
        <label className="text-sm font-medium" htmlFor="res-cause">원인</label>
        <input id="res-cause" className="field" maxLength={MAX} value={rootCause} onChange={(e) => setRootCause(e.target.value)} placeholder="예: 결제 대행사 응답 지연" />
        <label className="text-sm font-medium" htmlFor="res-action">한 조치</label>
        <input id="res-action" className="field" maxLength={MAX} value={action} onChange={(e) => setAction(e.target.value)} placeholder="예: 타임아웃을 늘리고 재시도" />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="btn-primary" disabled={busy} onClick={save}>{busy ? "저장 중…" : "해결 내용 남기기"}</button>
        {prior && <span className="text-xs text-ink-500">이 이벤트는 시작할 때 비슷한 과거 이벤트를 근거로 봤어요(반복 장애로 기록).</span>}
        {msg && <span role={msg.ok ? "status" : "alert"} className={`text-xs ${msg.ok ? "text-emerald-700" : "text-red-700"}`}>{msg.text}</span>}
      </div>
    </section>
  );
}
