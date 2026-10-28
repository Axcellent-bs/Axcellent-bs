// 녹화된 목 응답으로 fetch·EventSource를 대신한다. 다른 모듈보다 먼저 불러야 한다
import './mock/install'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'

import 'pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css'
import './styles.css'
import { App } from './App'
import { SessionProvider } from './demo/session'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* 기획개발 화면이 #/ 주소를 자체 탭 이동에 쓰므로 라우터는 일반 경로를 쓴다. GitHub Pages 새로고침은 404.html이 받는다 */}
    <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, '')}>
      <SessionProvider>
        <App />
      </SessionProvider>
    </BrowserRouter>
  </StrictMode>,
)
