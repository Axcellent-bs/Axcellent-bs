// 인프라 AI = DejaFix 서비스 화면. scripts/build-dejafix.mjs가 DejaFix 레포 frontend를 목 데이터 버전으로 빌드해
// public/dejafix/에 넣고, 이 화면이 그걸 본문 전체에 띄운다. DejaFix 자체 사이드바는 빌드에서 숨기고(바깥 메뉴로 대신),
// 바깥 주소(/infra/<메뉴>)와 DejaFix 안 주소(#/<메뉴>)를 서로 맞춘다.
import { useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router'

const SRC = `${import.meta.env.BASE_URL}dejafix/index.html`

/** DejaFix 안 주소의 메뉴 이름. 장애 상세(#/incidents/..)는 현황 메뉴에 속한다 */
const sectionOf = (hash: string) => {
  const top = hash.replace(/^#\/?/, '').split(/[/?]/)[0]
  return top === 'incidents' ? '' : top
}

export default function DejaFixFrame() {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const frame = useRef<HTMLIFrameElement>(null)
  const section = pathname.replace(/^\/infra\/?/, '').split('/')[0]
  const first = useRef(`${SRC}#/${section}`)
  const current = useRef(section)
  current.current = section

  // 바깥 메뉴를 누르면 DejaFix 안 화면을 그 메뉴로
  useEffect(() => {
    const w = frame.current?.contentWindow
    if (w && w.location.href !== 'about:blank' && sectionOf(w.location.hash) !== section) w.location.hash = `#/${section}`
  }, [section])

  // DejaFix 안에서 이동하면(알림 종, 장애 상세 등) 바깥 메뉴 표시를 맞춘다. 해시 라우터가 hashchange를 안 내서 주기적으로 본다
  useEffect(() => {
    const t = setInterval(() => {
      const w = frame.current?.contentWindow
      if (!w) return
      let inner: string
      try {
        inner = sectionOf(w.location.hash)
      } catch {
        return
      }
      if (inner !== current.current) navigate(inner ? `/infra/${inner}` : '/infra', { replace: true })
    }, 400)
    return () => clearInterval(t)
  }, [navigate])

  return <iframe ref={frame} data-dejafix title="DejaFix (목 데이터)" src={first.current} className="absolute inset-0 block h-full w-full border-0 bg-surface" />
}
