// 영상 제작안 검사 결과 (11/26): 의뢰 지시 ✓/✕. 기획 화면 제작안·대화방 카드 안 기획서·의뢰 카드
// 사람 확인 단계가 같은 컴포넌트를 쓴다(값은 lib/videoPlan.ts, 지켰는지는 기획 코드 검사 결과 그대로).
// 예전 제작안(칸 없음)은 아무것도 그리지 않는다
import {
  adjustmentLine, checkAttemptTitle, checkChip, checkProblemLine, requestRuleLine, requestRulesChip, type VideoPlanView,
} from '@/lib/videoPlan'

const CHIP_TONE: Record<string, string> = {
  '검사 통과': 'bg-emerald-50 text-emerald-800',
  '다시 써서 통과': 'bg-amber-50 text-amber-800',
  '확인 필요': 'bg-red-50 text-red-700',
}

/** 제작 검사 기록 (11/26 G): 판마다 ✕ 사유 → ✓, "다시 써서 통과" 칩, 의뢰와 달라진 점. 기록 없으면 안 그림 */
export function CheckLog({ v }: { v: Pick<VideoPlanView, 'checkLog' | 'adjustments'> }) {
  const chip = checkChip(v)
  if (!chip) return null
  const log = v.checkLog ?? []
  const adjustments = v.adjustments ?? []
  return (
    <div className="plan-check-log space-y-1 text-xs" aria-label="제작 검사 기록">
      <p className="flex flex-wrap items-center gap-1.5">
        <b className="text-ink-900">제작 검사 기록</b>
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
    </div>
  )
}

export function RequestRules({ v }: { v: Pick<VideoPlanView, 'requestRules'> }) {
  const chip = requestRulesChip(v)
  const rules = v.requestRules ?? []
  if (!chip) return null
  const all = rules.every((r) => r.kept)
  return (
    <div className="plan-rules space-y-1 text-xs" aria-label="의뢰 지시">
      <p className="flex flex-wrap items-center gap-1.5">
        <b className="text-ink-900">의뢰 지시</b>
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
    </div>
  )
}
