import { useEffect, useState } from "react";
import { DecisionCard } from "@/components/hr/DecisionCards";
import { hr, type HrCard } from "@/lib/hr";

// 이 이슈가 끝날 때 남은 장애 대응 카드(보라). 이슈 번호가 같은 카드만 보여 준다.
export function IncidentCards({ contextId }: { contextId: string }) {
  const [cards, setCards] = useState<HrCard[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setCards(null); setErr(null);
    hr.decisionCards(undefined, 200)
      .then((all) => { if (alive) setCards(all.filter((c) => c.kind === "incident" && c.context_id === contextId)); })
      .catch((e) => { if (alive) setErr(e instanceof Error ? e.message : "카드를 받지 못했습니다"); });
    return () => { alive = false; };
  }, [contextId]);

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold">장애 대응 카드</h4>
      {err ? <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{err}</p>
        : !cards ? <p className="text-xs text-ink-500">불러오는 중…</p>
        : cards.length === 0 ? <p className="text-xs text-ink-500">이 이슈로 남은 카드가 아직 없습니다. 이슈가 끝나면 아카이브 AI가 남깁니다.</p>
        : cards.map((c) => (
          <div key={c.card_id} className="space-y-1.5 border-l-4 border-violet-400 pl-3">
            <span className="chip bg-violet-100 text-violet-800">장애 대응</span>
            <DecisionCard card={c} />
          </div>
        ))}
    </div>
  );
}
