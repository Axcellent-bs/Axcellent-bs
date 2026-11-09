import { idText } from "@/components/planning-common/links";
import { cardRows, plainText, type HrCard } from "@/lib/hr";
import { Link } from "@/lib/nav";

// 근거 줄: 기획 AI가 보낸 "spec:SPEC-9"·"decision:D-1"을 "기획서-9의 결정-1"로 보여 주고, 기획서는 기획 화면으로 이어 준다.
// 번호 표기는 기획 화면과 같은 규칙(idText)을 쓰고, 원래 번호는 마우스를 올리면 보인다.
function Refs({ refs }: { refs: string[] }) {
  const specs = refs.filter((t) => t.startsWith("spec:")).map((t) => t.slice(5));
  const decisions = refs.filter((t) => t.startsWith("decision:")).map((t) => t.slice(9));
  const others = refs.filter((t) => !t.startsWith("spec:") && !t.startsWith("decision:"));
  const decisionText = decisions.map(idText).join(", ");
  return (
    <>
      {specs.map((id, i) => (
        <span key={id}>
          {i > 0 && ", "}
          <Link href={`/planning/work/specs/${encodeURIComponent(id)}`} className="font-medium text-brand-600 underline underline-offset-2" title={id}>{idText(id)}</Link>
          {i === specs.length - 1 && decisionText && <>의 {decisionText}</>}
        </span>
      ))}
      {specs.length === 0 && decisionText && <>{decisionText}</>}
      {others.length > 0 && <>{specs.length + decisions.length > 0 ? " · " : ""}{others.map(plainText).join(", ")}</>}
    </>
  );
}

// 결정 카드 본문: 읽는 사람에게 꼭 필요한 줄만 보여 준다. 저장된 본문은 그대로이고 화면에서만 줄인다.
export default function CardBody({ card }: { card: Pick<HrCard, "title" | "body" | "kind"> }) {
  const rows = cardRows(card);
  if (rows.length === 0) return null;
  return (
    <dl className="space-y-2 rounded-xl bg-ink-50 px-4 py-3 text-[15px] leading-relaxed text-ink-900">
      {rows.map((r) => (
        <div key={r.label} className="grid grid-cols-[5.5rem_1fr] gap-3">
          <dt className="text-ink-500">{r.label}</dt>
          <dd className="min-w-0 break-words">{r.refs ? <Refs refs={r.refs} /> : r.text}</dd>
        </div>
      ))}
    </dl>
  );
}
