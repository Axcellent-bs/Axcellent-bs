import { createContext, useContext } from 'react'

export interface ToastApi {
  /** 짧은 알림 한 줄. 새 알림이 오면 이전 것을 바꾼다. ms 기본 3000 */
  show: (message: string, ms?: number) => void
}

export const ToastContext = createContext<ToastApi | null>(null)
/** 지금 보이는 알림 글. ToastOutlet이 읽는다 */
export const ToastMessageContext = createContext<string>('')

export function useToast(): ToastApi {
  const api = useContext(ToastContext)
  if (!api) throw new Error('ToastProvider 안에서만 useToast를 쓸 수 있습니다')
  return api
}
