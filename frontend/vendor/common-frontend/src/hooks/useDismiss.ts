import { useEffect, useRef, type RefObject } from 'react'

/** 열려 있는 동안 바깥 클릭이나 Esc로 닫는다. 드롭다운·팝오버용. */
export function useDismiss(ref: RefObject<HTMLElement | null>, open: boolean, onDismiss: () => void): void {
  const cb = useRef(onDismiss)
  useEffect(() => {
    cb.current = onDismiss
  })
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) cb.current()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') cb.current()
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [ref, open])
}
