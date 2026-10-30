import { useEffect, useState } from "react";
import type { OrchIncident } from "@/lib/agentChat";
import { hr, type HrCard } from "@/lib/hr";

// 이벤트를 고친 사람이 "무슨 일이었고 어떻게 고쳤는지"를 확인해 남기는 칸. 비슷한 장애가 다시 나면 총괄이 이 기록을 근거로 보여 준다.
// 총괄이 아는 사실(원인 후보·끝난 롤백)이 있으면 초안으로 미리 채운다. 초안은 사람이 확인하고 눌러야 저장된다.
// 이 칸은 원인과 조치를 남기는 곳이고 시간은 재지 않는다. 시간은 시스템이 감지부터 정상 복귀까지 잰다(이벤트 타임라인).
const OUTCOME_KO = { resolved: "해결됨", workaround: "임시 조치", unresolved: "미해결" } as const;
type Outcome = keyof typeof OUTCOME_KO;
const MAX = 300;

interface Saved { outcome?: string; root_cause?: string | null; action?: string | null }

export default function ResolutionForm({ inc }: { inc: OrchIncident }) {
  const draft = inc.resolution_draft ?? null;
  const [saved, setSaved] = useState<Saved | null>(null);
  const [outcome, setOutcome] = useState<Outcome>(draft?.outcome ?? "resolved");
  const [rootCause, setRootCause] = useState(draft?.root_cause ?? "");
  const [action, setAction] = useState(draft?.action ?? "");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // 이벤트가 바뀌면 그 이벤트의 초안으로 다시 채운다
  useEffect(() => {
    setOutcome(draft?.outcome ?? "resolved");
    setRootCause(draft?.root_cause ?? "");
    setAction(draft?.action ?? "");
    setMsg(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inc.context_id]);

  // 이미 적은 해결 내용이 있으면 보여 준다 (카드의 extra.resolution)
  useEffect(() => {
    let alive = true;
    setSaved(null);
    hr.decisionCards(inc.context_id, 5)
      .then((cards: HrCard[]) => {
        const hit = cards.find((c) => c.extra && typeof c.extra.resolution === "object" && c.extra.resolution);
        if (alive) setSaved((hit?.extra.resolution as Saved | undefined) ?? null);
      })
      .catch(() => { /* 조회 실패는 입력을 막지 않는다 */ });
    return () => { alive = false; };
  }, [inc.context_id]);

  const prior = inc.recommendation?.status === "found" ? inc.recommendation.card_id ?? null : null;
  const matchesDraft = !!draft
    && rootCause.trim() === (draft.root_cause ?? "")
    && action.trim() === (draft.action ?? "")
    && (draft.outcome == null || outcome === draft.outcome);

  async function save() {
    setBusy(true); setMsg(null);
    try {
      await hr.recordResolution(inc.context_id, {
        outcome,
        root_cause: rootCause.trim() || undefined,
        action: action.trim() || undefined,
        prior_case_id: prior,
        draft_source: !draft ? "written_by_person" : matchesDraft ? "ai_draft_kept" : "ai_draft_edited",
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
        <h2 className="text-lg font-semibold">이 이벤트의 해결 내용 확인하고 남기기</h2>
        <p className="mt-1 text-xs text-ink-500">원인을 확인하고 고친 사람이 확인해 남깁니다. 비워 두면 "확인할 수 없음"으로 남습니다. 시간은 여기서 재지 않고, 시스템이 감지부터 정상 복귀까지 위 타임라인에서 잽니다.</p>
      </div>
      {draft && (
        <div className="rounded-xl bg-amber-50 px-4 py-3 text-xs text-amber-900">
          <b>{matchesDraft ? "AI 초안 · 사람 확인 전" : "AI 초안을 고쳤어요"}</b>
          <span className="ml-2">총괄이 아는 사실만 채웠어요. 맞는지 확인하거나 고친 뒤 남겨 주세요.</span>
          <ul className="mt-1 list-disc pl-5">{draft.basis.map((b) => <li key={b}>{b}</li>)}</ul>
        </div>
      )}
      {saved && (
        <p className="rounded-xl bg-emerald-50 px-4 py-2 text-xs text-emerald-800">
          지금 남아 있는 기록: {OUTCOME_KO[saved.outcome as Outcome] ?? saved.outcome} · 원인 {saved.root_cause ?? "확인할 수 없음"} · 조치 {saved.action ?? "확인할 수 없음"}
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
        <button type="button" className="btn-primary" disabled={busy} onClick={save}>{busy ? "저장 중…" : "확인했어요, 해결 내용 남기기"}</button>
        {prior && <span className="text-xs text-ink-500">이 이벤트는 시작할 때 비슷한 과거 이벤트를 근거로 봤어요(반복 장애로 기록).</span>}
        {msg && <span role={msg.ok ? "status" : "alert"} className={`text-xs ${msg.ok ? "text-emerald-700" : "text-red-700"}`}>{msg.text}</span>}
      </div>
    </section>
  );
}
