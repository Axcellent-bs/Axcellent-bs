import { useCallback, useEffect, useState } from "react";
import { hr, type DuplicateGroup, type DuplicateView } from "@/lib/hr";

// 같은 결정을 다시 쓴 카드를 묶어 보여 준다. 확신이 매우 높은 것은 AI가 이미 병합했고(아래에서 되돌릴 수 있다),
// 애매한 것만 여기서 사람이 병합하거나 그대로 둔다. 병합해도 옛 카드는 지워지지 않고 "대체됨"으로 남는다.
const pct = (n: number | null) => (n == null ? "–" : `${Math.round(n * 100)}%`);
const VERDICT_KO = { same: "같은 결정", changed: "내용이 바뀜", separate: "별개" } as const;

function Group({ g, onDone }: { g: DuplicateGroup; onDone: () => void }) {
  const [title, setTitle] = useState(g.suggested_title ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function act(decision: "merge" | "dismiss") {
    setBusy(true); setErr(null);
    try { await hr.decideDuplicate(g, decision, decision === "merge" ? title : undefined); onDone(); }
    catch (e) { setErr(e instanceof Error ? e.message : "처리하지 못했습니다"); }
    finally { setBusy(false); }
  }

  return (
    <li className="space-y-3 rounded-2xl border border-ink-100 p-4 text-sm">
      <div>
        <p className="text-xs font-semibold text-ink-500">새 결정 (병합 후 남는 카드)</p>
        <p className="mt-0.5 font-medium">{g.representative.decision}</p>
      </div>
      <div>
        <p className="text-xs font-semibold text-ink-500">같은 결정으로 보이는 옛 카드 {g.olders.length}장 (병합하면 대체됨으로 표시)</p>
        <ul className="mt-1 space-y-1">
          {g.olders.map((o) => (
            <li key={o.card_id} className="rounded-lg bg-ink-50 px-3 py-2">
              <span>{o.decision}</span>
              <span className="ml-2 text-xs tabular-nums text-ink-500">
                유사도 {pct(o.cosine)}{o.verdict ? ` · AI 판정: ${VERDICT_KO[o.verdict]}` : " · AI 판정 없음"}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-xs font-semibold text-ink-500" htmlFor={`dup-title-${g.group_id}`}>대표 제목</label>
        <input id={`dup-title-${g.group_id}`} className="field min-w-[16rem] flex-1" maxLength={80} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="비워 두면 지금 제목 그대로" />
      </div>
      {g.suggested_title && <p className="text-xs text-ink-500">대표 제목은 AI가 이 카드들의 내용으로만 제안한 것이고, 확인한 뒤 병합됩니다.</p>}
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="btn-primary" disabled={busy} onClick={() => act("merge")}>{busy ? "처리 중…" : "병합하기"}</button>
        <button type="button" className="btn-ghost" disabled={busy} onClick={() => act("dismiss")}>그대로 두기</button>
        {err && <span role="alert" className="text-xs text-red-700">{err}</span>}
      </div>
    </li>
  );
}

export default function DuplicateGroups() {
  const [view, setView] = useState<DuplicateView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    hr.duplicateGroups()
      .then((v) => { setView(v); setErr(null); })
      .catch((e) => setErr(e instanceof Error ? e.message : "중복 결정 목록을 받지 못했습니다"));
  }, []);
  useEffect(() => {
    load();
    const poll = setInterval(load, 15000);
    return () => clearInterval(poll);
  }, [load]);

  async function restore(cardId: string) {
    setBusy(cardId);
    try { await hr.restoreMerge(cardId); load(); }
    catch (e) { setErr(e instanceof Error ? e.message : "되돌리지 못했습니다"); }
    finally { setBusy(null); }
  }

  return (
    <section className="card space-y-4 p-6">
      <div>
        <h2 className="text-lg font-semibold">중복 결정 정리</h2>
        <p className="mt-1 text-xs text-ink-500">같은 결정을 다른 말로 다시 쓴 카드를 묶습니다. 확신이 매우 높은 것(유사도와 AI 판정이 모두 기준 이상)은 AI가 병합하고, 애매한 것은 아래에서 사람이 정합니다. 병합해도 옛 카드는 지워지지 않고 되돌릴 수 있습니다. 이벤트 기록 카드는 대상이 아닙니다.</p>
      </div>
      {err && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{err}</p>}
      {!view ? (!err && <p className="text-xs text-ink-500">불러오는 중…</p>) : (
        <>
          <div>
            <h3 className="text-sm font-semibold">사람이 정할 묶음 · {view.groups.length}건</h3>
            {view.groups.length === 0
              ? <p className="mt-1 text-sm text-ink-500">지금 사람이 정할 중복 결정이 없습니다.</p>
              : <ul className="mt-2 space-y-3">{view.groups.map((g) => <Group key={g.group_id} g={g} onDone={load} />)}</ul>}
          </div>
          <div>
            <h3 className="text-sm font-semibold">병합된 카드 · {view.merged.length}장</h3>
            {view.merged.length === 0
              ? <p className="mt-1 text-sm text-ink-500">병합된 카드가 없습니다.</p>
              : (
                <ul className="mt-2 space-y-1.5">
                  {view.merged.map((m) => (
                    <li key={m.card_id} className="flex flex-wrap items-center gap-2 rounded-lg bg-ink-50 px-3 py-2 text-sm">
                      <span className="min-w-0 flex-1 truncate" title={m.title}>{m.title}</span>
                      <span className="chip bg-ink-100 text-ink-700">{m.how === "auto" ? "AI가 자동 병합" : "사람이 병합"}{m.cosine != null ? ` · ${pct(m.cosine)}` : ""}</span>
                      <button type="button" className="btn-ghost" disabled={busy === m.card_id} onClick={() => restore(m.card_id)}>{busy === m.card_id ? "되돌리는 중…" : "되돌리기"}</button>
                    </li>
                  ))}
                </ul>
              )}
          </div>
        </>
      )}
    </section>
  );
}
