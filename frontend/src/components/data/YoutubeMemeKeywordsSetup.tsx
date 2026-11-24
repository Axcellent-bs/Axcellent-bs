import { useEffect, useState } from 'react'
import { More, Notice, SectionHead, StatusChip } from '@/components/connect/parts'
import { ApiError } from '@/lib/apiError'
import { HUMAN_TOKEN_SET } from '@/lib/env'
import { splitKeywords, youtubeMemeKeywords, type MemeKeywordsState } from '@/lib/youtubeMemeKeywords'

// 유튜브 밈 키워드: 하루 1번 이 키워드로 최근 7일 쇼츠를 조회수 순으로 찾아 "요즘 유행·밈"의 쇼츠 검색에 보여 준다.
// 저장하면 회사 설정 파일 목록 대신 이 목록을 쓴다. "회사 설정 파일로 되돌리기"로 저장 목록을 지운다.
const SOURCE: Record<MemeKeywordsState['source'], string> = { screen: '화면에서 저장', tenant: '회사 설정 파일', none: '없음' }
const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : '요청에 실패했습니다')

export default function YoutubeMemeKeywordsSetup() {
  const [state, setState] = useState<MemeKeywordsState | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const take = (s: MemeKeywordsState) => { setState(s); setText(s.keywords.join('\n')) }
  useEffect(() => {
    let live = true
    youtubeMemeKeywords.get().then((s) => { if (live) take(s) }).catch((e) => { if (live) setError(message(e)) })
    return () => { live = false }
  }, [])

  const run = async (work: () => Promise<MemeKeywordsState>, ok: string) => {
    setBusy(true); setError(null); setDone(null)
    try { take(await work()); setDone(ok) } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  const typed = splitKeywords(text)
  const max = state?.max ?? 10
  const canWrite = HUMAN_TOKEN_SET && (state?.editable ?? false)
  const status = !state ? null : state.keywords.length
    ? <StatusChip tone="ok">{state.keywords.length}개 · {SOURCE[state.source]}</StatusChip>
    : <StatusChip tone="wait">키워드 없음</StatusChip>

  return (
    <section className="card space-y-4 p-6" aria-label="유튜브 밈 키워드">
      <SectionHead as="h3" title="유튜브 밈 키워드" right={status}
        desc="위 요즘 유행·밈의 쇼츠 검색이 찾을 키워드예요. 한 줄에 하나씩 넣어요." />
      {!HUMAN_TOKEN_SET && <Notice>로그인이 필요해요. 로그인하면 저장할 수 있어요.</Notice>}
      {state && !state.editable && <Notice>서버에 회사 연결 파일 설정이 없어 저장할 수 없어요.</Notice>}
      <label className="block space-y-1 text-sm">
        <span className="text-ink-700">키워드 (최대 {max}개, 하나에 30자까지)</span>
        <textarea className="field h-32 w-full text-xs" value={text} onChange={(e) => { setText(e.target.value); setDone(null) }}
          placeholder={'고양이 밈\n출근길 브이로그'} />
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn-primary" disabled={busy || !canWrite}
          onClick={() => void run(() => youtubeMemeKeywords.save(typed), `${typed.length}개를 저장했어요. 다음 날부터 이 키워드로 찾아요.`)}>
          {busy ? '저장 중…' : '저장'}
        </button>
        {state?.source === 'screen' && state.tenant_count > 0 && (
          <button type="button" className="btn-ghost" disabled={busy || !canWrite}
            onClick={() => void run(() => youtubeMemeKeywords.clear(), `회사 설정 파일 목록(${state.tenant_count}개)으로 되돌렸어요.`)}>
            회사 설정 파일로 되돌리기 ({state.tenant_count}개)
          </button>
        )}
        <span className="text-xs text-ink-500">입력 {typed.length}개 / {max}</span>
      </div>
      <More summary="어떻게 쓰이나요">
        <p>키워드 하나가 하루 YouTube 검색 100단위예요. 하루 1번, 최근 7일 쇼츠를 조회수 순으로 찾아요.</p>
        <p>구독자 10만 이상 채널은 따로 보여 주고, 실존 인물·사건 영상은 빼요.</p>
      </More>
      {done && <p role="status" className="text-sm text-emerald-800">{done}</p>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </section>
  )
}
