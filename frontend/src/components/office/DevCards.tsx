import { Link } from "@/lib/nav";
import { PLANNING_REQUESTS_HREF } from "@/lib/navCounts";

// 개발 AI 대화의 계획 카드와 "기획에 넘김" 줄. AI 대화방(전체 대화방·직접 대화)과 AI 오피스 단톡방이 같이 쓴다.
// "이대로 진행"은 개발 화면(components/dev/chat.tsx)과 같은 문구·동작이다: 같은 대화에 "진행해"를 보낸다.

export const PROCEED_TEXT = "진행해";

export function PlanCard({ plan, onGo, disabled = false }: { plan: { title: string; done: string[] }; onGo?: () => void; disabled?: boolean }) {
  return (
    <div className="mt-2 rounded-xl bg-surface-2 p-3 text-ink-900 ring-1 ring-ink-100">
      <p className="text-sm font-semibold">계획: {plan.title}</p>
      {plan.done.length > 0 && (
        <ul className="mt-1 list-disc pl-4 text-xs text-ink-700">{plan.done.map((d) => <li key={d}>{d}</li>)}</ul>
      )}
      {onGo && (
        <div className="mt-2 flex justify-end">
          <button type="button" className="btn-primary px-3 py-1.5 text-xs" onClick={onGo} disabled={disabled}>이대로 진행</button>
        </div>
      )}
    </div>
  );
}

export function HandoffLine({ requestId }: { requestId: string | null }) {
  return (
    <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg bg-surface-2 px-2.5 py-1.5 text-xs text-ink-700 ring-1 ring-ink-100">
      <span>↪ 기획에 넘김{requestId ? ` · 요청 ${requestId}` : ""}</span>
      <Link href={PLANNING_REQUESTS_HREF} className="font-medium text-brand-700 underline-offset-2 hover:underline">기획 화면에서 보기 →</Link>
    </p>
  );
}
