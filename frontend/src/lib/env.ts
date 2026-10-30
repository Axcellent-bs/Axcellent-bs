// AI 백엔드 주소. 화면은 항상 실제 백엔드(backend/, scripts/run_all.sh)를 부른다. 녹화본 데모는 없다.
// 포트는 backend/infra/README.md 포트 표 기준이고, 바꿀 때는 frontend/.env.example 을 보고 .env 에 적는다.
// 토큰은 backend/scripts/run_web.sh가 켤 때마다 만든 값(backend/.data/web.env)을 넣는다. 기본값은 비어 있고,
// 비어 있으면 그 토큰이 필요한 버튼·조회를 끄고 안내한다.

/** 고객지원·인사·AI 대화방 공통 로그: dashboard-api(8020) */
export const DASHBOARD_API_URL = import.meta.env.VITE_DASHBOARD_API_URL ?? 'http://localhost:8020'
/** 재무 AI: 안전 레이어(safety, 8001)와 재무 에이전트(finance, 8105) 조회 API */
export const SAFETY_API_URL = import.meta.env.VITE_SAFETY_API_URL ?? 'http://localhost:8001'
export const FINANCE_AGENT_URL = import.meta.env.VITE_FINANCE_AGENT_URL ?? 'http://localhost:8105'
/** C 보류 결정·할 일함 완료 등 쓰기에 쓰는 사람 토큰. 비어 있으면 쓰기 버튼을 끈다 */
export const SAFETY_HUMAN_TOKEN = import.meta.env.VITE_SAFETY_HUMAN_TOKEN ?? ''
/** 총괄(orchestrator, 8000). 총괄은 CORS가 없어 기본값은 Vite 개발 프록시(/orch → vite.config.ts) */
export const ORCHESTRATOR_URL = import.meta.env.VITE_ORCHESTRATOR_URL ?? '/orch'
/**
 * 총괄 사건·부서 사용량을 읽는 shop-api 관리 읽기 중계(개발용 임시). 토큰은 run_web.sh가 넣는 관리자 토큰이다.
 * 진짜 로그인과 역할은 사람 인증(#265) 뒤에 이 자리를 대신한다.
 */
export const RELAY_API_URL = import.meta.env.VITE_SHOP_ADMIN_API_URL || 'http://localhost:8010'
export const RELAY_TOKEN = import.meta.env.VITE_SHOP_ADMIN_TOKEN || ''
/** 데이터분석 AI: 데이터 AI(data, 8106). 기본값 /data-a2a는 Vite 개발 프록시(vite.config.ts, DATA_PROXY_TARGET) */
export const DATA_A2A_URL = import.meta.env.VITE_DATA_A2A_URL ?? '/data-a2a'
/** 데이터 AI 직접 조회 토큰. 비어 있으면 KPI 실시간 조회를 하지 않는다. 브라우저 저장소에 두지 않는다 */
export const DATA_A2A_TOKEN = import.meta.env.VITE_DATA_A2A_TOKEN ?? ''
/** 사람 토큰이 설정돼 있는지. 없으면 쓰기 버튼을 끄고 안내한다 */
export const HUMAN_TOKEN_SET = SAFETY_HUMAN_TOKEN.length > 0


/** 장애 대응: DejaFix 백엔드. 기본값 /dejafix-api는 개발 서버가 로그인까지 대신 해 주는 프록시(dejafix-proxy.ts) */
export const DEJAFIX_API_URL = import.meta.env.VITE_DEJAFIX_API_URL ?? '/dejafix-api'
