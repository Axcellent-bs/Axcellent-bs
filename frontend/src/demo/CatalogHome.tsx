import { Link } from 'react-router'
import { IconAgents, IconOffice } from '@/components/icons'
import { homeOf, useSession } from './session'
import { FAMILIES, usePublishedServices, type Service } from './services'

// 로그인 전에 여는 메인 페이지 = 서비스 제품군 카탈로그. 어떤 서비스가 있는지 보고, 로그인 버튼으로 로그인 화면에 간다.
// 회사가 실제로 켤 서비스는 로그인 뒤 시스템 관리자가 서비스 고르기(/system/services)에서 정한다.
const WORDMARK = `${import.meta.env.BASE_URL}axcellent-wordmark.png`

function Mark({ s }: { s: Service }) {
  return (
    <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ${s.by === 'DejaFix' ? 'bg-[#1d1d3a]' : 'bg-brand-50 text-brand-600'}`}>
      {s.logo ? <img src={s.logo} alt="" width={24} height={24} /> : <s.Icon size={22} />}
    </span>
  )
}

function ServiceCard({ s }: { s: Service }) {
  const screens = s.children?.filter((c) => !c.soon) ?? []
  return (
    <li className="flex flex-col gap-3 rounded-2xl border border-ink-100 bg-surface p-5">
      <div className="flex items-center gap-3">
        <Mark s={s} />
        <div className="min-w-0">
          <p className="font-semibold text-ink-900">{s.name}</p>
          <p className="text-xs text-ink-500">{s.by}</p>
        </div>
      </div>
      <p className="text-sm leading-relaxed text-ink-700">{s.desc}</p>
      {screens.length > 0 && (
        <div className="mt-auto flex flex-wrap gap-1">
          {screens.map((c) => <span key={c.href} className="chip bg-ink-100 text-ink-700">{c.label}</span>)}
        </div>
      )}
    </li>
  )
}

export default function CatalogHome() {
  const { account } = useSession()
  const SERVICES = usePublishedServices()

  return (
    <div className="min-h-screen bg-app text-ink-900">
      <section className="bg-[#1d1d3a] text-white">
        <header className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-5">
          <img src={WORDMARK} alt="AXcellent" className="h-8 w-auto" />
          <nav className="ml-4 hidden gap-5 text-sm text-white/70 md:flex" aria-label="메인 메뉴">
            <a href="#families" className="hover:text-white">제품군</a>
            <a href="#install" className="hover:text-white">설치 방식</a>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            {account && (
              <Link to={homeOf(account)} className="rounded-full px-4 py-2 text-sm text-white/80 hover:text-white">
                내 화면으로
              </Link>
            )}
            <Link to="/login" className="rounded-full bg-white px-5 py-2 text-sm font-semibold text-[#1d1d3a] hover:bg-white/90">
              로그인
            </Link>
          </div>
        </header>

        <div className="mx-auto grid max-w-6xl gap-10 px-6 pb-16 pt-10 lg:grid-cols-[1.1fr_1fr] lg:items-center">
          <div className="space-y-6">
            <h1 className="text-4xl font-bold leading-tight tracking-tight sm:text-5xl" style={{ textWrap: 'balance', wordBreak: 'keep-all' }}>
              필요한 AI 서비스만 골라 회사 안에서 쓰세요
            </h1>
            <p className="max-w-xl text-base leading-relaxed text-white/75">
              AXcellent는 회사 서버에 설치하는 AI 업무 서비스 모음이에요. 쓰고 싶은 서비스만 켜고, 직원마다 쓸 서비스를 배정해요.
            </p>
            <div className="flex flex-wrap gap-3">
              <Link to="/login" className="rounded-full bg-[#e0a72c] px-6 py-3 text-sm font-semibold text-[#1d1d3a] hover:brightness-105">로그인</Link>
              <a href="#families" className="rounded-full border border-white/30 px-6 py-3 text-sm font-semibold text-white hover:bg-white/10">서비스 둘러보기</a>
            </div>
          </div>
          <ul className="grid grid-cols-4 gap-3 sm:grid-cols-5" aria-label={`서비스 ${SERVICES.length}개`}>
            {SERVICES.map((s) => (
              <li key={s.id} title={s.name} className="flex aspect-square flex-col items-center justify-center gap-1.5 rounded-2xl bg-white/[0.07] p-2 text-center">
                {s.logo ? <img src={s.logo} alt="" width={26} height={26} /> : <s.Icon size={24} />}
                <span className="text-[11px] leading-tight text-white/80">{s.name}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <main className="mx-auto max-w-6xl space-y-14 px-6 py-14">
        <section id="families" className="scroll-mt-6 space-y-10">
          <div className="space-y-2">
            <h2 className="text-2xl font-bold tracking-tight">서비스 제품군</h2>
            <p className="text-sm text-ink-500">서비스 {SERVICES.length}개를 하는 일에 따라 묶었어요. 회사는 이 중 필요한 것만 켜서 써요.</p>
            <nav className="flex flex-wrap gap-2 pt-2" aria-label="제품군 바로가기">
              {FAMILIES.map((f) => (
                <a key={f.id} href={`#fam-${f.id}`} className="chip bg-surface px-3 py-1 text-xs text-ink-700 ring-1 ring-ink-100 hover:ring-brand-500">{f.name}</a>
              ))}
            </nav>
          </div>

          {FAMILIES.map((f) => {
            const list = SERVICES.filter((s) => s.family === f.id)
            if (!list.length) return null
            return (
              <div key={f.id} id={`fam-${f.id}`} className="scroll-mt-6 space-y-4">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-ink-100 pb-2">
                  <h3 className="text-lg font-semibold">{f.name}</h3>
                  <p className="text-sm text-ink-500">{f.desc}</p>
                  <span className="ml-auto text-xs text-ink-500">{list.length}개</span>
                </div>
                <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {list.map((s) => <ServiceCard key={s.id} s={s} />)}
                </ul>
              </div>
            )
          })}

          <div className="flex flex-wrap items-center gap-4 rounded-2xl border border-dashed border-ink-300 px-5 py-4">
            <span className="text-sm font-semibold">기본 포함</span>
            <span className="flex items-center gap-2 text-sm text-ink-700"><IconAgents size={18} /> AI 대화방</span>
            <span className="flex items-center gap-2 text-sm text-ink-700"><IconOffice size={18} /> AI 오피스</span>
            <span className="text-xs text-ink-500">어떤 서비스를 골라도 모든 직원이 써요.</span>
          </div>
        </section>

        <section id="install" className="scroll-mt-6 space-y-5">
          <h2 className="text-2xl font-bold tracking-tight">설치 방식</h2>
          <ul className="grid gap-4 md:grid-cols-3">
            <li className="rounded-2xl bg-surface p-5 ring-1 ring-ink-100">
              <p className="font-semibold">회사 서버에 설치</p>
              <p className="mt-1 text-sm text-ink-700">AXcellent는 프로그램만 드려요. 서버도 데이터도 회사 것이에요.</p>
            </li>
            <li className="rounded-2xl bg-surface p-5 ring-1 ring-ink-100">
              <p className="font-semibold">사내망에서만 열림</p>
              <p className="mt-1 text-sm text-ink-700">외부 접근은 막혀 있어요. PC와 서버는 사내 IP로만 연결해요.</p>
            </li>
            <li className="rounded-2xl bg-surface p-5 ring-1 ring-ink-100">
              <p className="font-semibold">회사 서비스와 연결</p>
              <p className="mt-1 text-sm text-ink-700">쇼핑몰·앱의 주소와 API 키를 넣으면 주문·문의 같은 데이터를 받아와요.</p>
            </li>
          </ul>
        </section>

        <section className="flex flex-wrap items-center justify-between gap-4 rounded-3xl bg-[#1d1d3a] px-8 py-8 text-white">
          <div>
            <p className="text-xl font-semibold">이미 회사에 설치돼 있나요?</p>
            <p className="mt-1 text-sm text-white/70">회사 이메일로 로그인하면 내게 배정된 서비스가 열려요.</p>
          </div>
          <Link to="/login" className="rounded-full bg-white px-6 py-3 text-sm font-semibold text-[#1d1d3a] hover:bg-white/90">로그인</Link>
        </section>
      </main>

      <footer className="border-t border-ink-100 py-6 text-center text-xs text-ink-500">AXcellent · DejaFix · 데모 프로토타입</footer>
    </div>
  )
}
