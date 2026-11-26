// 영상 제작안 스토리보드 (기획 제안·기획서 화면, 보기만). 값은 기획 state proposals[].video_plan 그대로 (lib/videoPlan.ts)
// 11/23 재현 님 시안 ④: 콘셉트 한 줄 → 길이·비율·장면·대상·근거 → 장면 시간표 막대(폭 = 시간 비율) → 장면 카드
// (번호·역할·시간·화면 구성·자막) → 대본·영상 생성 프롬프트는 접기(인쇄 때는 펼침). 장면 그림 칸은 지금은 색 칸이고,
// 이미지 시안이 생기면 그 자리에 들어간다.
// RequestDecideBar: 대화방 의뢰 카드에서 ?request=wr_…로 들어왔고 그 의뢰의 사람 확인이 대기 중이면 화면 아래 고정 줄에
// [다음 단계 진행]·[여기서 멈추기]. 대화 카드와 같은 동작(orchestrator.decideRequestStep)이다
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { Tag } from '@/components/planning-common/parts'
import { CheckLog, RequestRules } from '@/components/planning/PlanChecks'
import { usePrinting } from '@/lib/print'
import { svgDataUri } from '@/lib/storyboardSvg'
import { characterLine, formatChip, versionChip, sceneKeyframeLine, sceneTime, videoFallbackLine, videoPlanChip, videoPurposeChip, videoSvgLine, type VideoPlanView } from '@/lib/videoPlan'
import { sceneRole, sceneShares } from '@/lib/videoStoryboard'
import { orchestrator } from '@/lib/orchestrator'
import { proposalOfRequest, requestActionError, stepRows } from '@/lib/requestCards'
import type { WorkRequestRow } from '@/lib/workRequests'

/** 접는 칸 (시안의 "제목 · 회색 설명 ›" 줄). 인쇄 때는 펼친다 */
export function Fold({ title, note, children, defaultOpen = false }: { title: string; note?: string; children: ReactNode; defaultOpen?: boolean }) {
  const printing = usePrinting()
  const [open, setOpen] = useState(defaultOpen)
  return (
    <details className="sb-fold" open={open || printing} onToggle={(e) => { if (!printing) setOpen(e.currentTarget.open) }}>
      <summary><b>{title}</b>{note && <span className="sb-fold-note">{note}</span>}</summary>
      <div className="sb-fold-body">{children}</div>
    </details>
  )
}

export interface StoryBasis { report: string | null; points: string[] }

export function VideoStoryboard({ v, audience, basis, children }: { v: VideoPlanView; audience?: string | null; basis?: StoryBasis; children?: ReactNode }) {
  const shares = sceneShares(v.scenes)
  const purpose = videoPurposeChip(v)
  const why = videoFallbackLine(v)
  const svgWhy = videoSvgLine(v)
  const lead = characterLine(v)
  return (
    <section className="pp-part sb" aria-label="영상 제작안">
      <h3 className="pp-part-h">영상 제작안<Tag>{videoPlanChip(v)}</Tag>{versionChip(v) && <Tag>{versionChip(v)}</Tag>}{why && <span className="sb-fallback">{why}</span>}{purpose && <span className="chip sb-purpose" title="광고 목적">{purpose}</span>}</h3>
      <p className="sb-concept">{v.concept}</p>
      <dl className="sb-meta">
        <div><dt>길이</dt><dd>{v.durationS}초</dd></div>
        <div><dt>비율</dt><dd>{v.aspect}</dd></div>
        <div><dt>장면</dt><dd>{v.scenes.length}</dd></div>
        {lead && <div className="sb-lead"><dt>주인공</dt><dd>{lead}</dd></div>}
        {formatChip(v) && <div className="sb-format"><dt>형식</dt><dd><span className="chip sb-format-chip">{formatChip(v)}</span></dd></div>}
        {v.formatId && v.situationKo && <div className="sb-situation"><dt>상황</dt><dd>{v.situationKo}</dd></div>}
        {audience && <div><dt>대상</dt><dd>{audience}</dd></div>}
        {v.offerKo && <div><dt>혜택</dt><dd>{v.offerKo}</dd></div>}
        {v.ctaKo && <div><dt>행동 문구</dt><dd>{v.ctaKo}</dd></div>}
        {basis && (basis.report || basis.points.length > 0) && (
          <div className="sb-basis"><dt>근거</dt><dd>{[basis.report ? `보고서 ${basis.report}` : null, ...basis.points].filter(Boolean).join(' · ')}</dd></div>
        )}
      </dl>
      <ol className="sb-bar" aria-label="장면 시간표">
        {v.scenes.map((s, i) => (
          <li key={s.n} className={`sb-c${i % 4}`} style={{ flexBasis: `${shares[i]}%` }} title={`장면 ${s.n} · ${sceneTime(s)}`}>{s.n} · {sceneTime(s)}</li>
        ))}
      </ol>
      <RequestRules v={v} />
      <CheckLog v={v} />
      {svgWhy && <p className="sb-fallback sb-svg-why">{svgWhy}</p>}
      <ol className="sb-cards" style={{ '--n': v.scenes.length } as CSSProperties}>
        {v.scenes.map((s, i) => {
          const keyframe = sceneKeyframeLine(s)
          return (
            <li key={s.n} className="sb-card">
              {s.startSvg || s.endSvg ? (
                <div className="sb-svgs" aria-label={`장면 ${s.n} 콘티`}>
                  {([['첫 화면', s.startSvg], ['끝 화면', s.endSvg]] as const).map(([label, svg]) => svg ? (
                    <figure key={label}><img src={svgDataUri(svg)} alt={`장면 ${s.n} ${label} 콘티`} /><figcaption>{label}</figcaption></figure>
                  ) : (
                    <figure key={label} className="sb-svg-empty"><figcaption>{label} 없음</figcaption></figure>
                  ))}
                </div>
              ) : (
                <div className={`sb-shot sb-c${i % 4}`} aria-hidden="true"><span>장면 그림 자리</span></div>
              )}
              <div className="sb-card-body">
                <div className="sb-card-top">{s.n} · {sceneRole(i, v.scenes.length)} · {sceneTime(s)}</div>
                {s.beatKo && <span className={`sb-beat${s.beatPunch ? ' sb-beat-punch' : ''}`} title={s.beatPunch ? '펀치라인 비트' : '형식 비트'}>{s.beatKo}</span>}
                <p className="sb-visual">{s.visual}</p>
                {s.placeKo && <span className="sb-place" title="장면 장소">장소 · {s.placeKo}</span>}
                {keyframe && <p className="sb-keyframe" title="첫 화면 → 끝 화면 · 동작 · 카메라">{keyframe}</p>}
                {s.caption && <span className="sb-caption">자막: {s.caption}</span>}
              </div>
            </li>
          )
        })}
      </ol>
      {v.script && (
        <Fold title="대본 전체" note={`${v.script.length}자`}>
          <p className="vp-script">{v.script}</p>
        </Fold>
      )}
      <Fold title="영상 생성 프롬프트" note="장면별 영문 · 공통 화풍 · 금지">
        <ol className="vp-prompt-list">
          {v.scenes.map((s) => <li key={s.n}><b>장면 {s.n}</b> <span className="mono">{s.prompt}</span></li>)}
        </ol>
        <dl className="pp-fields">
          <dt>공통 화풍</dt><dd className="mono">{v.style || '–'}</dd>
          <dt>공통 금지</dt><dd className="mono">{v.negative || '–'}</dd>
        </dl>
      </Fold>
      {children}
    </section>
  )
}

/** 의뢰 사람 확인 줄. 의뢰를 못 읽거나(사람 토큰 없음 등), 이 제안의 의뢰가 아니거나, 확인할 단계가 없으면 안 그린다 */
export function RequestDecideBar({ request, proposal }: { request: string | null; proposal: string }) {
  const [row, setRow] = useState<WorkRequestRow | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!request) return
    let live = true
    orchestrator.workRequest(request).then((r) => { if (live) setRow(r) }).catch(() => { if (live) setRow(null) })
    return () => { live = false }
  }, [request])
  if (!request || !row) return null
  if (proposalOfRequest(row, [{ id: proposal }]) !== proposal) return null
  const step = stepRows(row).find((s) => s.decide)
  if (!step?.decide && !done) return null
  const decide = (approved: boolean) => {
    if (!step) return
    setBusy(true)
    setError(null)
    orchestrator.decideRequestStep(row.request_id, step.stepId, approved)
      .then(() => setDone(approved ? `${step.decide?.approve ?? '진행'}했어요. 결과는 대화방 의뢰 카드에서 이어서 보여요.` : '여기서 멈췄어요.'))
      .catch((e: unknown) => setError(requestActionError(e).text))
      .finally(() => setBusy(false))
  }
  return (
    <div className="sb-decide print-hide" role="region" aria-label={`의뢰 ${row.request_id} 사람 확인`}>
      {done ? <span>{done}</span> : (
        <>
          <span className="sb-decide-text">확인 후 버튼을 눌러 주세요. 진행하면 다음 단계로 넘어가요.{error && <span role="alert" className="sb-decide-err"> {error}</span>}</span>
          <button type="button" className="btn-primary" disabled={busy} onClick={() => decide(true)}>{step?.decide?.approve}</button>
          <button type="button" className="btn-ghost" disabled={busy} onClick={() => decide(false)}>{step?.decide?.stop}</button>
        </>
      )}
    </div>
  )
}
