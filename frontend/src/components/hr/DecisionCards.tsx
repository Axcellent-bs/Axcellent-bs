import { ago } from "@/lib/format";
import type { HrCard } from "@/lib/hr";

// 결정 기록 화면: 확정된(후보가 아닌) 결정 카드 한 장. 값은 인사 AI가 저장한 그대로 보여 준다.
const CONF_KO = { low: "낮음", mid: "보통", high: "높음" } as const;
const Chip = ({ label, tone }: { label: string; tone: string }) => <span className={`chip ${tone}`}>{label}</span>;

export function DecisionCard({ card }: { card: HrCard }) {
  return (
    <article className="space-y-2 rounded-2xl border border-ink-100 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <b className="text-[15px]">{card.title}</b>
        <Chip label="확정" tone="bg-emerald-50 text-emerald-700" />
        {card.confidence && <Chip label={`확신 ${CONF_KO[card.confidence]}`} tone="bg-ink-100 text-ink-500" />}
      </div>
      <pre className="whitespace-pre-wrap rounded-lg bg-ink-50 px-3 py-2 text-xs leading-relaxed text-ink-700">{card.body}</pre>
      <p className="text-xs text-ink-500" title={`카드 ${card.card_id} · 이벤트 ${card.context_id}`}>{card.author}가 {ago(card.created_at)} 남김</p>
    </article>
  );
}
