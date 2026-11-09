import { useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ToastContext, ToastMessageContext, type ToastApi } from './toastContext'

/** 앱 맨 바깥에 한 번 둔다. 안쪽에서 useToast().show('저장했습니다') */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const show = useCallback<ToastApi['show']>((text, ms = 3000) => {
    clearTimeout(timer.current)
    setMessage(text)
    timer.current = setTimeout(() => setMessage(''), ms)
  }, [])

  useEffect(() => () => clearTimeout(timer.current), [])

  const api = useMemo(() => ({ show }), [show])
  return (
    <ToastContext.Provider value={api}>
      <ToastMessageContext.Provider value={message}>{children}</ToastMessageContext.Provider>
    </ToastContext.Provider>
  )
}

/** 알림이 보일 자리. 상단바 안 원하는 위치에 둔다 (DejaFix: 시간대 전환 옆의 작은 앰버 알약 .toast). */
export function ToastOutlet() {
  const message = useContext(ToastMessageContext)
  return (
    <span role="status" aria-live="polite" style={{ display: 'contents' }}>
      {message && <span className="toast">{message}</span>}
    </span>
  )
}
