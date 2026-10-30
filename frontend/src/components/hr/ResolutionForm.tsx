import { useEffect, useState } from "react";
import type { OrchIncident } from "@/lib/agentChat";
import { hr, type HrCard } from "@/lib/hr";

// 이벤트를 고친 사람이 "무슨 일이었고 어떻게 고쳤는지"를 확인해 남기는 칸. 비슷한 장애가 다시 나면 총괄이 이 기록을 근거로 보여 준다.
// 총괄이 아는 사실(원인 후보·끝난 롤백)이 있으면 초안으로 미리 채운다. 초안은 사람이 확인하고 눌러야 저장된다.
// 소요 시간은 이벤트가 열린 시각부터 "해결한 시각"까지를 서버가 잰다. 해결한 시각은 사람이 고칠 수 있다.
const OUTCOME_KO = { resolved: "해결됨", workaround: "임시 조치", unresolved: "미해결" } as const;
type Outcome = keyof typeof OUTCOME_KO;
const MAX = 300;

interface Saved { outcome?: string; root_cause?: string | null; action?: string | null; minutes_to_resolve?: number | null }

/** datetime-local 입력용 "YYYY-MM-DDTHH:mm" (내 시간대 기준) */
function localInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function ResolutionForm({ inc }: { inc: OrchIncident }) {
  const draft = inc.resolution_draft ?? null;
  const [saved, setSaved] = useState<Saved | null>(null);
  const [outcome, setOutcome] = useState<Outcome>(draft?.outcome ?? "resolved");
  const [rootCause, setRootCause] = useState(draft?.root_cause ?? "");
  const [action, setAction] = useState(draft?.action ?? "");
  const [resolvedAt, setResolvedAt] = useState(() => localInput(new Date()));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // 이벤트가 바뀌면 그 이벤트의 초안으로 다시 채운다
  useEffect(() => {
    setOutcome(draft?.outcome ?? "resolved");
    setRootCause(draft?.root_cause ?? "");
    setAction(draft?.action ?? "");
    setResolvedAt(localInput(new Date()));
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
  const opened = new Date(inc.opened_at ?? Date.now());
  // 입력칸은 분 단위라 같은 분 안이면 열린 시각과 같은 것으로 본다(초 단위로 견주면 열린 직후에 항상 오류가 난다)
  const openedMinute = Math.floor(opened.getTime() / 60_000) * 60_000;
  const at = new Date(resolvedAt);
  const timeError = Number.isNaN(at.getTime()) ? "해결한 시각을 확인해 주세요"
    : at.getTime() < openedMinute ? "해결한 시각이 이벤트가 열린 시각보다 이를 수 없어요"
    : at.getTime() > Date.now() + 60_000 ? "해결한 시각이 지금보다 늦을 수 없어요" : null;

  async function save() {
    if (timeError) { setMsg({ ok: false, text: timeError }); return; }
    setBusy(true); setMsg(null);
    try {
      await hr.recordResolution(inc.context_id, {
        outcome,
        root_cause: rootCause.trim() || undefined,
        action: action.trim() || undefined,
        opened_at: inc.opened_at,
        // 열린 시각보다 이르면 서버가 거부하므로, 같은 분 안이면 열린 시각으로 맞춘다
        resolved_at: new Date(Math.max(at.getTime(), opened.getTime())).toISOString(),
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
        <p className="mt-1 text-xs text-ink-500">원인을 확인하고 고친 사람이 확인해 남깁니다. 비워 두면 "확인할 수 없음"으로 남습니다. 소요 시간은 이벤트가 열린 때부터 아래 "해결한 시각"까지로 자동으로 잽니다.</p>
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
        <label className="text-sm font-medium" htmlFor="res-at">해결한 시각</label>
        <div>
          <input id="res-at" type="datetime-local" className="field" value={resolvedAt} min={localInput(opened)} max={localInput(new Date())} onChange={(e) => setResolvedAt(e.target.value)} />
          <p className={`mt-1 text-xs ${timeError ? "text-red-700" : "text-ink-500"}`}>{timeError ?? "실제로 고친 시각을 적어 주세요. 이 값은 사람이 적은 것이라 시스템이 잰 시간이 아니에요."}</p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="btn-primary" disabled={busy || !!timeError} onClick={save}>{busy ? "저장 중…" : "확인했어요, 해결 내용 남기기"}</button>
        {prior && <span className="text-xs text-ink-500">이 이벤트는 시작할 때 비슷한 과거 이벤트를 근거로 봤어요(반복 장애로 기록).</span>}
        {msg && <span role={msg.ok ? "status" : "alert"} className={`text-xs ${msg.ok ? "text-emerald-700" : "text-red-700"}`}>{msg.text}</span>}
      </div>
    </section>
  );
}
