import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'

import 'pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css'
import './styles.css'
import { App } from './App'
import { SessionProvider } from './demo/session'
import { installApiDirect, readDirectConfig } from './lib/apiDirect'

// 읽기 API 를 외부 직접 호스트로 보내는 길. 환경값(VITE_API_DIRECT_BASE·VITE_API_DIRECT_SERVICES)이 비면 아무것도 하지 않는다
installApiDirect(readDirectConfig(import.meta.env))

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* 기획·개발 화면이 #/ 주소를 자체 탭 이동에 쓰므로 라우터는 일반 경로를 쓴다 */}
    <BrowserRouter>
      <SessionProvider>
        <App />
      </SessionProvider>
    </BrowserRouter>
  </StrictMode>,
)
