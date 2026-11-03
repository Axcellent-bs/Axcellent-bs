// 목록 페이지 나누기 (개발 작업 현황 board.tsx와 같은 모양): 한 번에 PAGE_SIZE개, ‹ 번호 › · "N개 중 a–b"
import { useState } from 'react'

export const PAGE_SIZE = 20

/** 목록을 페이지로 자른다. 목록이 줄어 지금 페이지가 없어지면 마지막 페이지로 */
export function usePage<T>(all: T[], size = PAGE_SIZE) {
  const [page, setPage] = useState(0)
  const pages = Math.max(1, Math.ceil(all.length / size))
  const cur = Math.min(page, pages - 1)
  return { rows: all.slice(cur * size, (cur + 1) * size), cur, pages, setPage, total: all.length, size }
}

export function Pager({ cur, pages, setPage, total, size }: { cur: number; pages: number; setPage: (n: number) => void; total: number; size: number }) {
  if (pages <= 1) return null
  return (
    <nav className="pager" aria-label="페이지">
      <button type="button" className="btn-quiet px-2 py-1" disabled={cur === 0} onClick={() => setPage(cur - 1)} aria-label="이전 페이지">‹</button>
      {Array.from({ length: pages }, (_, n) => (
        <button key={n} type="button" className={n === cur ? 'btn-primary px-2.5 py-1 !text-white' : 'btn-quiet px-2.5 py-1'} aria-current={n === cur ? 'page' : undefined} onClick={() => setPage(n)}>{n + 1}</button>
      ))}
      <button type="button" className="btn-quiet px-2 py-1" disabled={cur === pages - 1} onClick={() => setPage(cur + 1)} aria-label="다음 페이지">›</button>
      <span className="ml-2 text-xs text-ink-500">{total}개 중 {cur * size + 1}–{Math.min(total, (cur + 1) * size)}</span>
    </nav>
  )
}
