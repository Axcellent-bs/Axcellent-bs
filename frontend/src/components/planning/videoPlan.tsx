// 기획 제안 "3 영상 제작안" (기획 → 마케팅 영상 제작안, 보기만). 값은 기획 state proposals[].video_plan 그대로 (lib/videoPlan.ts)
// 콘셉트·대본 · 장면 표(장면·시간·화면 구성·자막) · 접기(장면별 영상 생성 프롬프트·공통 화풍·공통 금지, 인쇄 때는 펼침)
import { useState } from 'react'
import { Tag } from '@/components/planning-common/parts'
import { usePrinting } from '@/lib/print'
import { sceneTime, videoPlanChip, type VideoPlanView } from '@/lib/videoPlan'

export function VideoPlanSection({ v, no = 3 }: { v: VideoPlanView; no?: number }) {
  const printing = usePrinting()
  const [open, setOpen] = useState(false)
  return (
    <section className="pp-part" aria-label="영상 제작안">
      <h3 className="pp-part-h"><span className="pp-no">{no}</span>영상 제작안<Tag>{videoPlanChip(v)}</Tag></h3>
      <dl className="pp-fields">
        <dt>콘셉트</dt><dd>{v.concept}</dd>
        {v.script && <><dt>대본</dt><dd className="vp-script">{v.script}</dd></>}
      </dl>
      <div className="vp-scenes">
        <table className="vp-table">
          <thead><tr><th scope="col">장면</th><th scope="col">시간</th><th scope="col">화면 구성</th><th scope="col">자막</th></tr></thead>
          <tbody>
            {v.scenes.map((s) => (
              <tr key={s.n}>
                <td>{s.n}</td>
                <td className="vp-nowrap">{sceneTime(s)}</td>
                <td>{s.visual}</td>
                <td>{s.caption || '–'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details className="pp-body vp-prompts" open={open || printing} onToggle={(e) => { if (!printing) setOpen(e.currentTarget.open) }}>
        <summary>장면별 영상 생성 프롬프트 · 공통 화풍 · 공통 금지</summary>
        <ol className="vp-prompt-list">
          {v.scenes.map((s) => <li key={s.n}><b>장면 {s.n}</b> <span className="mono">{s.prompt}</span></li>)}
        </ol>
        <dl className="pp-fields">
          <dt>공통 화풍</dt><dd className="mono">{v.style || '–'}</dd>
          <dt>공통 금지</dt><dd className="mono">{v.negative || '–'}</dd>
        </dl>
      </details>
    </section>
  )
}
