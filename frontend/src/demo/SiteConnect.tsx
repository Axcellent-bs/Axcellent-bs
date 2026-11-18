import { useEffect, useState } from 'react'
import { companyConnection, type ConnectionState } from '@/lib/companyConnection'
import { SectionHead, StatusChip } from '@/components/connect/parts'
import { Link } from '@/lib/nav'
import Integrations from './Integrations'
import SiteAdapterConnect from './SiteAdapterConnect'

// 사이트 연결: 실제로 되는 연결 방법만 보여 준다(고를 수만 있고 동작하지 않는 "준비 중" 칸은 두지 않는다, 2026-11-18).
// - AXcellent 규격 API(shop-api): Integrations가 주소를 확인하고 저장한다
// - 다른 형식 API·플랫폼으로 만든 사이트: SiteAdapterConnect가 사이트 변환기(dashboard-api /site-adapter)로 잇는다
//   (docs/superpowers/specs/2026-11-18-site-adapter-design.md). 읽기 전용 DB 연결은 하지 않기로 했다.
// 처음 열면 지금 저장된 연결 종류를 고른 채로 보여 준다. 배포 도구 연결은 시스템 관리 › 도구 연결에서 한다.
type Way = 'shop_api' | 'adapter'
const WAYS: { id: Way; title: string; desc: string }[] = [
  { id: 'shop_api', title: 'AXcellent 규격 API', desc: '사이트가 shop-api 규격으로 주문·상품·구매자를 내줘요. 주소만 확인하면 돼요.' },
  { id: 'adapter', title: '다른 형식 API · 플랫폼 사이트', desc: '사이트 자기 API(예약·주문·상품 목록)나 플랫폼 API를 칸 짝을 정해 바꿔 읽어요.' },
]

export default function SiteConnect({ onShop }: { onShop: (s: ConnectionState | 'error') => void }) {
  const [way, setWay] = useState<Way | null>(null)

  useEffect(() => {
    let live = true
    companyConnection.get()
      .then((s) => { if (live) setWay(s.connection?.kind === 'adapter' ? 'adapter' : 'shop_api') })
      .catch(() => { if (live) setWay('shop_api') })
    return () => { live = false }
  }, [])

  return (
    <section className="space-y-4" aria-label="사이트 연결">
      <SectionHead title="사이트 연결" desc="회사 사이트의 데이터를 AI들이 읽을 수 있게 이어요. 사이트 코드나 서버는 건드리지 않아요." />

      <div className="card">
        <div className="space-y-4 p-6">
          <SectionHead as="h3" title="사이트 데이터 연결" desc="사이트가 데이터를 내주는 방식을 골라요. 둘 중 하나만 써요."
            right={<StatusChip tone="wait">필수</StatusChip>} />
          <div role="radiogroup" aria-label="연결 방식" className="grid gap-2 sm:grid-cols-2">
            {WAYS.map((w) => (
              <label key={w.id} className={`flex cursor-pointer gap-3 border px-4 py-3 text-sm ${way === w.id ? 'border-brand-700 bg-brand-50/40' : 'border-ink-100'}`}>
                <input type="radio" name="site-way" className="mt-1" checked={way === w.id} onChange={() => setWay(w.id)} />
                <span>
                  <span className="block font-medium text-ink-900">{w.title}</span>
                  <span className="mt-0.5 block text-xs leading-5 text-ink-500">{w.desc}</span>
                </span>
              </label>
            ))}
          </div>
        </div>
        <div className="border-t border-ink-100 p-4">
          {way === 'shop_api' && <Integrations onState={onShop} embedded />}
          {way === 'adapter' && <div className="p-2"><SiteAdapterConnect onState={onShop} /></div>}
        </div>
      </div>

      <div className="card px-5 py-3 text-sm text-ink-700">
        배포 도구(GitHub Actions·Jenkins)는 시스템 관리 › 도구 연결 › 배포에서 연결해요. <Link href="/system/tools" className="font-medium text-brand-700 underline">도구 연결 열기</Link>
      </div>
    </section>
  )
}
