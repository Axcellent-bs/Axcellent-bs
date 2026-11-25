import type { Plugin } from 'vite'

/** 읽기 API 직접 호스트(VITE_API_DIRECT_BASE, 예: https://api-app.axcellent.cloud)가 있으면 index.html
 * 머리에 preconnect를 넣는다. 세션 확인 요청(CORS 사전 요청 포함)이 연결을 새로 맺지 않게 한다 (11/25 성능).
 * 값이 없거나 http(s) 주소가 아니면 아무것도 넣지 않는다 */
export function apiPreconnect(base: string | undefined): Plugin {
  let origin: string | null = null
  try {
    const url = new URL((base || '').trim())
    if (url.protocol === 'https:' || url.protocol === 'http:') origin = url.origin
  } catch {
    origin = null
  }
  return {
    name: 'ax-api-preconnect',
    transformIndexHtml() {
      if (!origin) return []
      return [{ tag: 'link', attrs: { rel: 'preconnect', href: origin, crossorigin: '' }, injectTo: 'head-prepend' }]
    },
  }
}
