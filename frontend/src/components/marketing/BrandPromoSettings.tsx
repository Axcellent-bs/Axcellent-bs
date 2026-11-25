import { useEffect, useState } from 'react'
import { Notice, SectionHead, StatusChip } from '@/components/connect/parts'
import { useSession } from '@/demo/session'
import { promoStyleApi } from '@/lib/promoStyle'
import {
  EXPOSURE_ROW, LOOK_NOTICE, LOOK_ROWS, STYLE_ROW, canSave, exposureActive, hasScreenValue, lookDirty, lookDraft, lookError,
  errText, lookShown, saveBody, sourceLabel, sourceLine,
  type LookDraft, type PromoPick, type PromoState,
} from '@/lib/promoStyleView'

// 마케팅 › 브랜드 (/marketing/brand, 11/24 "재미 먼저, 브랜드는 끝에", 화면 시안). 예전 자리는 시스템 관리 › 회사 연결 › 브랜드.
// 홍보 영상의 목적은 "이 재밌는 영상 만든 채널 어디지? → 우리 회사"로 사람을 끌어오는 것이다.
// 두 줄: 홍보 영상 방향 · 브랜드 노출. 고르고 [저장]. 보기는 마케팅 화면을 여는 회원 모두,
// 저장·되돌리기는 회사 관리자만 한다(일반회원은 버튼이 꺼진다, 서버도 403).
// 회사별로 저장되고, 데이터 트렌드 보고서 추천과 기획 넘김이 같은 값을 쓴다.

export default function BrandPromoSettings() {
  const { account } = useSession()
  const isAdmin = !!account?.admin
  const [state, setState] = useState<PromoState | null>(null)
  const [pick, setPick] = useState<PromoPick | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const [look, setLook] = useState<LookDraft>({ character_ko: '', character_en: '', setting_ko: '', setting_en: '', style_en: '' })
  const take = (s: PromoState) => { setState(s); setPick({ promo_style: s.promo_style, brand_exposure: s.brand_exposure }); setLook(lookDraft(s)) }

  useEffect(() => {
    let live = true
    promoStyleApi.get().then((s) => live && take(s), (x: unknown) => live && setError(errText(x)))
    return () => { live = false }
  }, [])

  const run = (job: () => Promise<PromoState>, ok: string) => {
    setBusy(true)
    setError(null)
    setDone(null)
    job().then((s) => { take(s); setDone(ok) }, (x: unknown) => setError(errText(x))).finally(() => setBusy(false))
  }

  if (!state || !pick) {
    return error ? <Notice tone="bad">홍보 영상 방향을 불러오지 못했어요: {error}</Notice> : <p className="text-sm text-ink-500">불러오는 중…</p>
  }
  const showLook = lookShown(state)
  const lookMsg = showLook ? lookError(look) : null
  const changed = canSave(state, pick, busy, lookDirty(state, look))
  const exposureOn = exposureActive(pick.promo_style)
  const canEdit = isAdmin && state.editable

  return (
    <section className="space-y-4" aria-label="브랜드">
      <SectionHead title="브랜드" desc="홍보 영상을 어떻게 만들지 정해요. 재밌는 영상으로 사람을 끌어오고, 브랜드는 끝에 붙여요."
        right={<StatusChip tone="off">{sourceLine(state)}</StatusChip>} />
      <div className="card space-y-3 p-5">
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-ink-100 px-3 py-2.5 text-sm">
          <div className="min-w-[180px] flex-1">
            <b className="text-ink-900">{STYLE_ROW.label}</b>
            <small className="block text-xs text-ink-500">{STYLE_ROW.hint}</small>
          </div>
          {STYLE_ROW.options.map((o) => (
            <button key={o.value} type="button" aria-pressed={pick.promo_style === o.value} disabled={busy || !canEdit}
              className={pick.promo_style === o.value ? 'btn-primary px-3 py-1.5 text-xs' : 'btn-ghost px-3 py-1.5 text-xs'}
              onClick={() => setPick({ ...pick, promo_style: o.value })}>{o.label}</button>
          ))}
        </div>
        <div className={`flex flex-wrap items-center gap-2 rounded-xl border border-ink-100 px-3 py-2.5 text-sm ${exposureOn ? '' : 'opacity-60'}`}>
          <div className="min-w-[180px] flex-1">
            <b className="text-ink-900">{EXPOSURE_ROW.label}</b>
            <small className="block text-xs text-ink-500">{EXPOSURE_ROW.hint}</small>
          </div>
          {EXPOSURE_ROW.options.map((o) => (
            <button key={o.value} type="button" aria-pressed={pick.brand_exposure === o.value} disabled={busy || !canEdit || !exposureOn}
              className={pick.brand_exposure === o.value ? 'btn-primary px-3 py-1.5 text-xs' : 'btn-ghost px-3 py-1.5 text-xs'}
              onClick={() => setPick({ ...pick, brand_exposure: o.value })}>{o.label}</button>
          ))}
        </div>
        <p className="text-xs text-ink-500">실존 인물·상표, 참여 조건부 문구("좋아요 누르면…"), 숫자는 지금처럼 쓰지 않고, AI 표시와 사람 승인도 그대로예요.</p>
        {showLook && (
          <div className="space-y-3 border-t border-ink-100 pt-3" role="group" aria-label="영상 기본 모습">
            <b className="text-sm text-ink-900">영상 기본 모습</b>
            <div className="grid gap-3 sm:grid-cols-2">
              {LOOK_ROWS.map((r) => {
                const cur = state[r.key] ?? ''
                const src = sourceLabel(state.source[r.key])
                return (
                  <label key={r.key} className="block space-y-1 text-xs text-ink-500">
                    <span className="flex items-center gap-2">
                      <span>{r.label}</span>
                      {src && <span className="chip">{src}</span>}
                    </span>
                    <input className="field w-full" value={look[r.key]} placeholder={cur} disabled={busy || !canEdit}
                      onChange={(e) => setLook({ ...look, [r.key]: e.target.value })} />
                  </label>
                )
              })}
            </div>
            <p className="text-xs text-ink-500">{LOOK_NOTICE}</p>
            {lookMsg && <p role="alert" className="text-xs text-red-700">{lookMsg}</p>}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn-primary" disabled={!changed || !canEdit || !!lookMsg}
            onClick={() => run(() => promoStyleApi.save(saveBody(state, pick, look)),'저장했어요. 다음 트렌드 분석 의뢰부터 이 방향으로 만들어요.')}>
            {busy ? '저장하는 중…' : '저장'}
          </button>
          {canEdit && hasScreenValue(state) && (
            <button type="button" className="btn-ghost" disabled={busy}
              onClick={() => run(() => promoStyleApi.clear(), '회사 설정 파일 값으로 되돌렸어요.')}>회사 설정 파일 값으로 되돌리기</button>
          )}
        </div>
        {!isAdmin && <p className="text-xs text-ink-500">보기만 할 수 있어요. 바꾸는 건 회사 관리자가 해요.</p>}
        {isAdmin && !state.editable && <p className="text-xs text-amber-800">이 서버는 회사 연결 파일 설정이 없어 저장할 수 없어요.</p>}
        {done && <p role="status" className="text-xs text-ink-500">{done}</p>}
        {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
      </div>
    </section>
  )
}
