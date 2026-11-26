// 영상 제작안 검사 결과 (11/26): 의뢰 지시 ✓/✕. 기획 화면 제작안·대화방 카드 안 기획서·의뢰 카드
// 사람 확인 단계가 같은 컴포넌트를 쓴다(값은 lib/videoPlan.ts, 지켰는지는 기획 코드 검사 결과 그대로).
// 예전 제작안(칸 없음)은 아무것도 그리지 않는다
import { useState, type ReactNode } from 'react'
import { usePrinting } from '@/lib/print'
import {
  adjustmentLine, checkAttemptTitle, checkChip, checkProblemLine, planChecksCount, planChecksOpen, requestRuleLine, requestRulesChip,
  type VideoPlanView,
} from '@/lib/videoPlan'

import { reviewNote, reviewProblemLine, toPlanReview, versionCell } from '@/lib/planReview'

/** 제작안 판 기록 (11/26 I, 의뢰 카드 ⑦ 마케팅 준비): 1판 마케팅 확인 ✕ N개 → 2판 ✓. 기록 없으면 안 그림 */
export function PlanVersions({ raw }: { raw: unknown }) {
  const r = toPlanReview(raw)
  if (!r) return null
  return (
    <div className="plan-versions space-y-1.5 text-xs" aria-label="제작안 판 기록">
      <p className="flex flex-wrap items-center gap-1.5">
        <b className="text-ink-900">제작안 판 기록</b>
        {r.needsHuman && <span className="chip bg-red-50 text-[11px] text-red-700">사람 확인 필요</span>}
      </p>
      <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        {r.versions.map((v, i) => {
          const c = versionCell(r, v, i)
          return (
            <div key={v.version} className={`min-w-0 space-y-0.5 rounded-lg border p-2 ${c.current ? 'border-brand-500' : 'border-ink-100'}`}>
              <b className="block text-ink-900">{c.title}</b>
              <span className={`chip text-[11px] ${c.ok ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-700'}`}>{c.chip}</span>
              <span className="block text-ink-500">{c.note}</span>
              {v.problems.length > 0 && (
                <ul className="ml-4 list-disc text-ink-700">{v.problems.map((p, k) => <li key={k} className="break-words">{reviewProblemLine(p)}</li>)}</ul>
              )}
            </div>
          )
        })}
      </div>
      <p className={r.needsHuman ? 'text-amber-800' : 'text-ink-500'}>{reviewNote(r)}</p>
    </div>
  )
}

const CHIP_TONE: Record<string, string> = {
  '검사 통과': 'bg-emerald-50 text-emerald-800',
  '다시 써서 통과': 'bg-amber-50 text-amber-800',
  '확인 필요': 'bg-red-50 text-red-700',
}

/** 11/26 기본 접힘 칸: 접힌 줄은 "▸ 제목 + 중립 개수 칩"만(✓/✕·빨강/초록·통과 낱말 없음), 펼치면 지금 내용.
 * 펼침 상태는 저장하지 않는다(새로 열면 접힘). open(PDF·인쇄용 고정)이나 인쇄 중이면 펼친다. 네이티브 <details>라
 * Enter/Space로 열리고 스크린리더가 열림/닫힘을 읽는다 */
function ChecksFold({ title, count, open = false, className, children }: { title: string; count: string; open?: boolean; className: string; children: ReactNode }) {
  const printing = usePrinting()
  const [shown, setShown] = useState(open)
  return (
    <details className={`${className} group min-w-0 rounded-lg border border-ink-100 bg-surface px-2.5 py-1.5 text-xs`}
      open={planChecksOpen(shown, printing)} onToggle={(e) => { if (!printing) setShown(e.currentTarget.open) }}>
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-1.5 text-ink-700 [&::-webkit-details-marker]:hidden">
        <span aria-hidden="true" className="inline-block text-ink-500 transition-transform group-open:rotate-90">▸</span>
        <b className="text-ink-900">{title}</b>
        <span className="chip bg-ink-50 text-[11px] text-ink-500">{count}</span>
      </summary>
      <div className="mt-1.5 space-y-1">{children}</div>
    </details>
  )
}

/** 제작 검사 기록 (11/26 G): 판마다 ✕ 사유 → ✓, "다시 써서 통과" 칩, 의뢰와 달라진 점. 기록 없으면 안 그림.
 * 11/26 기본 접힘 (접힌 줄 "제작 검사 기록 · N번 검사") */
export function CheckLog({ v, open = false }: { v: Pick<VideoPlanView, 'checkLog' | 'adjustments'>; open?: boolean }) {
  const chip = checkChip(v)
  if (!chip) return null
  const log = v.checkLog ?? []
  const adjustments = v.adjustments ?? []
  return (
    <ChecksFold className="plan-check-log" title="제작 검사 기록" count={planChecksCount(v, 'checks')} open={open}>
      <p className="flex flex-wrap items-center gap-1.5">
        <span className={`chip text-[11px] ${CHIP_TONE[chip]}`}>{chip}</span>
      </p>
      <ol className="space-y-1 border-l-2 border-ink-100 pl-2.5">
        {log.map((a) => (
          <li key={`${a.by}-${a.attempt}`}>
            <span className={a.passed ? 'text-emerald-700' : 'text-red-700'}>{a.passed ? '✓' : '✕'}</span>{' '}
            <b className="font-semibold text-ink-900">{checkAttemptTitle(a)}</b>
            {a.problems.length > 0 && (
              <ul className="ml-4 list-disc text-ink-700">
                {a.problems.map((p, i) => <li key={i} className="break-words">{checkProblemLine(p)}</li>)}
              </ul>
            )}
          </li>
        ))}
      </ol>
      {adjustments.length > 0 && (
        <div className="rounded-md bg-amber-50 px-2 py-1.5 text-amber-900">
          <b>의뢰와 달라진 점</b>
          <ul className="ml-4 list-disc">{adjustments.map((a, i) => <li key={i} className="break-words">{adjustmentLine(a)}</li>)}</ul>
        </div>
      )}
    </ChecksFold>
  )
}

/** 의뢰 지시 ✓/✕ (11/26). 11/26 기본 접힘 (접힌 줄 "의뢰 지시 · N개") */
export function RequestRules({ v, open = false }: { v: Pick<VideoPlanView, 'requestRules'>; open?: boolean }) {
  const chip = requestRulesChip(v)
  const rules = v.requestRules ?? []
  if (!chip) return null
  const all = rules.every((r) => r.kept)
  return (
    <ChecksFold className="plan-rules" title="의뢰 지시" count={planChecksCount(v, 'rules')} open={open}>
      <p className="flex flex-wrap items-center gap-1.5">
        <span className={`chip text-[11px] ${all ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-700'}`}>{chip}</span>
      </p>
      <ul className="space-y-0.5">
        {rules.map((r) => (
          <li key={`${r.kind}-${r.textKo}`} className="flex gap-1.5">
            <span aria-label={r.kept ? '지킴' : '못 지킴'} className={r.kept ? 'text-emerald-700' : 'text-red-700'}>{r.kept ? '✓' : '✕'}</span>
            <span className="min-w-0 break-words text-ink-700">{requestRuleLine(r)}</span>
          </li>
        ))}
      </ul>
      {!all && <p className="text-amber-800">AI가 한 번 다시 썼지만 못 지킨 지시가 있어요. 확인 후 진행해 주세요.</p>}
    </ChecksFold>
  )
}
