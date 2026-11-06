import { ago } from "@/lib/format";
import { readableBody, type HrCard } from "@/lib/hr";

// 결정 기록 화면: 확정된(후보가 아닌) 결정 카드 한 장. 값은 인사 AI가 저장한 그대로 보여 준다.
const CONF_KO = { low: "낮음", mid: "보통", high: "높음" } as const;
const Chip = ({ label, tone }: { label: string; tone: string }) => <span className={`chip ${tone}`}>{label}</span>;

// 기획 AI가 record_case로 보낸 카드는 요청자(author)가 planning이다. 시연 주입 카드도 같은 신원으로 들어가므로
// 이유 칸의 "시연 주입" 표식으로 구분한다.
const PLANNING_AUTHOR = "planning";
const DEMO_MARK = "시연 주입";

export function DecisionCard({ card }: { card: HrCard }) {
  const isDemo = card.body.includes(DEMO_MARK);
  const fromPlanning = card.author === PLANNING_AUTHOR;
  return (
    <article className="space-y-2 rounded-2xl border border-ink-100 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <b className="text-[15px]">{card.title}</b>
        <Chip label="확정" tone="bg-emerald-50 text-emerald-700" />
        {card.confidence && <Chip label={`AI 확신도 ${CONF_KO[card.confidence]}`} tone="bg-ink-100 text-ink-500" />}
        {fromPlanning && <Chip label="기획 AI" tone="bg-violet-50 text-violet-700" />}
        {isDemo && <Chip label="시연 주입" tone="bg-amber-50 text-amber-700" />}
      </div>
      <pre className="whitespace-pre-wrap rounded-lg bg-ink-50 px-3 py-2 text-xs leading-relaxed text-ink-700">{readableBody(card.body)}</pre>
      <p className="text-xs text-ink-500" title={`카드 ${card.card_id} · 이벤트 ${card.context_id}`}>{card.author}가 {ago(card.created_at)} 남김</p>
    </article>
  );
}
