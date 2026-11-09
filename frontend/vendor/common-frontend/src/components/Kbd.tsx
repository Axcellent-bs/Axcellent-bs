import type { ReactNode } from 'react'

/** 키보드 단축키 표시. 예: <Kbd>Ctrl K</Kbd> */
export function Kbd({ children }: { children: ReactNode }) {
  return <span className="kbd">{children}</span>
}
