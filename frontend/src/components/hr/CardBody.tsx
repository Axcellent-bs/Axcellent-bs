import { cardRows, type HrCard } from "@/lib/hr";

// 결정 카드 본문: 읽는 사람에게 꼭 필요한 줄만 보여 준다. 저장된 본문은 그대로이고 화면에서만 줄인다.
export default function CardBody({ card }: { card: Pick<HrCard, "title" | "body" | "kind"> }) {
  const rows = cardRows(card);
  if (rows.length === 0) return null;
  return (
    <dl className="space-y-2 rounded-xl bg-ink-50 px-4 py-3 text-[15px] leading-relaxed text-ink-900">
      {rows.map((r) => (
        <div key={r.label} className="grid grid-cols-[5.5rem_1fr] gap-3">
          <dt className="text-ink-500">{r.label}</dt>
          <dd className="min-w-0 break-words">{r.text}</dd>
        </div>
      ))}
    </dl>
  );
}
