import type { HrCard } from "@/lib/hr";
import { DecisionCard } from "@/components/hr/DecisionCards";
import DuplicateGroups from "@/components/hr/DuplicateGroups";

// 결정 기록 화면: 확정된 카드를 두 종류로 나눠 색으로 구별한다.
// - 장애 대응 카드(보라): 장애 이벤트가 끝날 때 총괄이 남긴 기록(kind=incident). 비슷한 이벤트 검색에 쓰이고 병합하지 않는다.
// - 업무 결정 카드(민트): 기획서 확정·정책·기각 같은 업무 결정(kind=decision). 같은 내용이 여러 장이면 병합 대상이다.
// 그 밖의 종류(사례·공지·정책·사기)는 맨 아래에 접어 둔다.
const KINDS = {
  incident: {
    label: "장애 대응",
    chip: "bg-violet-100 text-violet-800",
    rail: "border-violet-400",
    head: "bg-violet-50 ring-violet-100",
  },
  decision: {
    label: "업무 결정",
    chip: "bg-teal-100 text-teal-800",
    rail: "border-teal-400",
    head: "bg-teal-50 ring-teal-100",
  },
} as const;
type Kind = keyof typeof KINDS;

function Kinded({ kind, card }: { kind: Kind; card: HrCard }) {
  const k = KINDS[kind];
  return (
    <div className={`space-y-1.5 border-l-4 pl-3 ${k.rail}`}>
      <span className={`chip ${k.chip}`}>{k.label}</span>
      <DecisionCard card={card} />
    </div>
  );
}

function Head({ kind, title, count, desc }: { kind: Kind; title: string; count: number; desc: string }) {
  const k = KINDS[kind];
  return (
    <div className={`rounded-2xl px-4 py-3 ring-1 ${k.head}`}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold">{title}</h3>
        <span className={`chip tabular-nums ${k.chip}`}>{count}장</span>
      </div>
      <p className="mt-1 text-xs text-ink-700">{desc}</p>
    </div>
  );
}

export default function CardSections({ cards }: { cards: HrCard[] | null }) {
  if (cards === null) {
    return (
      <section className="card p-6">
        <h2 className="text-lg font-semibold">확정된 카드</h2>
        <p className="mt-2 text-xs text-amber-800">확정 카드 목록을 받지 못했습니다. 서버가 이 조회를 아직 지원하지 않을 수 있습니다.</p>
      </section>
    );
  }
  const incidents = cards.filter((c) => c.kind === "incident");
  const decisions = cards.filter((c) => c.kind === "decision");
  const others = cards.filter((c) => c.kind !== "incident" && c.kind !== "decision");
  return (
    <div className="space-y-6">
      <section aria-labelledby="sec-incident" className="card space-y-3 p-6">
        <div id="sec-incident">
          <Head
            kind="incident"
            title="장애 대응 카드(이벤트 기록)"
            count={incidents.length}
            desc="장애가 끝날 때 총괄이 남기고 비슷한 이벤트 검색에 쓰입니다."
          />
        </div>
        {incidents.length === 0
          ? <p className="py-4 text-center text-sm text-ink-500">아직 장애 대응 카드가 없습니다.</p>
          : <div className="space-y-3">{incidents.map((c) => <Kinded key={c.card_id} kind="incident" card={c} />)}</div>}
      </section>

      <section aria-labelledby="sec-decision" className="card space-y-3 p-6">
        <div id="sec-decision">
          <Head
            kind="decision"
            title="업무 결정 카드(기획·정책·기각 결정)"
            count={decisions.length}
            desc="같은 내용이 여러 장이면 자동 또는 승인으로 병합됩니다. 장애 대응 카드는 병합하지 않습니다."
          />
        </div>
        <DuplicateGroups />
        {decisions.length === 0
          ? <p className="py-4 text-center text-sm text-ink-500">아직 업무 결정 카드가 없습니다.</p>
          : <div className="space-y-3">{decisions.map((c) => <Kinded key={c.card_id} kind="decision" card={c} />)}</div>}
      </section>

      {others.length > 0 && (
        <details className="card p-6">
          <summary className="cursor-pointer text-base font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">
            그 밖의 카드 <span className="chip ml-2 bg-ink-100 text-ink-700 tabular-nums">{others.length}장</span>
          </summary>
          <p className="mt-2 text-xs text-ink-500">사례·공지·정책·사기 카드입니다. 병합 대상이 아닙니다.</p>
          <div className="mt-3 space-y-3">{others.map((c) => <DecisionCard key={c.card_id} card={c} />)}</div>
        </details>
      )}
    </div>
  );
}
