// AI 백엔드 주소. 화면은 항상 실제 백엔드(backend/, scripts/run_all.sh)를 부른다. 녹화본 데모는 없다.
// 포트는 backend/infra/README.md 포트 표 기준이고, 바꿀 때는 frontend/.env.example 을 보고 .env 에 적는다.
// 토큰은 backend/scripts/run_web.sh가 켤 때마다 만든 값(backend/.data/web.env)을 넣는다. 기본값은 비어 있고,
// 비어 있으면 그 토큰이 필요한 버튼·조회를 끄고 안내한다.

/** 고객지원·인사·AI 대화방 공통 로그: dashboard-api(8020) */
export const DASHBOARD_API_URL = import.meta.env.VITE_DASHBOARD_API_URL ?? 'http://localhost:8020'
/** 재무 AI: 안전 레이어(safety, 8001)와 재무 에이전트(finance, 8105) 조회 API */
export const SAFETY_API_URL = import.meta.env.VITE_SAFETY_API_URL ?? 'http://localhost:8001'
export const FINANCE_AGENT_URL = import.meta.env.VITE_FINANCE_AGENT_URL ?? 'http://localhost:8105'
/** 회사 쇼핑몰 화면 주소. 재무 판매자 정산의 상품을 쇼핑몰 상품 화면(`/products/<번호>`)으로 잇는다. 비어 있으면 링크 없이 번호만 */
export const SHOP_WEB_URL = (import.meta.env.VITE_SHOP_WEB_URL ?? '').trim().replace(/\/+$/, '')
/** C 보류 결정·할 일함 완료 등 쓰기에 쓰는 사람 토큰. 비어 있으면 쓰기 버튼을 끈다 */
export const SAFETY_HUMAN_TOKEN = import.meta.env.VITE_SAFETY_HUMAN_TOKEN ?? ''
/** 총괄(orchestrator, 8000). 총괄은 CORS가 없어 기본값은 Vite 개발 프록시(/orch → vite.config.ts) */
export const ORCHESTRATOR_URL = import.meta.env.VITE_ORCHESTRATOR_URL ?? '/orch'
/**
 * 관리자(admin) 토큰. 총괄 시연 시작·기획 형상관리 연결에만 쓴다(run_web.sh가 넣는다).
 * 판매 서버(shop-api) 관리 읽기는 화면이 부르지 않고 dashboard-api(`/api/dashboard/shop/*`)가
 * 회사 연결 주소로 서버 쪽 열람 토큰(`SHOP_VIEWER_TOKEN`)을 붙여 대신 읽는다. 판매 서버 주소·토큰은 번들에 없다.
 */
export const RELAY_TOKEN = import.meta.env.VITE_SHOP_ADMIN_TOKEN || ''
/** 데이터분석 AI: 데이터 AI(data, 8106). 기본값 /data-a2a는 Vite 개발 프록시(vite.config.ts, DATA_PROXY_TARGET) */
export const DATA_A2A_URL = import.meta.env.VITE_DATA_A2A_URL ?? '/data-a2a'
/** 데이터 AI 직접 조회 토큰. 비어 있으면 KPI 실시간 조회를 하지 않는다. 브라우저 저장소에 두지 않는다 */
export const DATA_A2A_TOKEN = (import.meta.env.VITE_DATA_A2A_TOKEN ?? '').trim()
/**
 * 공유 서버(251 등)의 자리표시자. 번들에 실제 토큰을 넣지 않고 이 값을 본문 meta.token에 그대로 보낸다.
 * 실제 토큰은 nginx가 `Authorization: Bearer <토큰>` 헤더로 넣고, 데이터 서버가 그 헤더로 검증한다.
 */
export const DATA_A2A_PLACEHOLDER = 'data-via-nginx'
/** 사람 토큰이 설정돼 있는지. 없으면 쓰기 버튼을 끄고 안내한다 */
export const HUMAN_TOKEN_SET = SAFETY_HUMAN_TOKEN.length > 0


/** 장애 대응: DejaFix 백엔드. 기본값 /dejafix-api는 개발 서버가 백엔드 dejafix-proxy(8030)로 넘기는 길(dejafix-proxy.ts) */
export const DEJAFIX_API_URL = import.meta.env.VITE_DEJAFIX_API_URL ?? '/dejafix-api'
