// AI 백엔드 주소. 포트는 ax-backend/CLAUDE.md 포트 표 기준이고, 바꿀 때는 frontend/.env.example 을 보고 .env 에 적는다.
// 기본은 데모 모드(VITE_MOCK이 off가 아님): 모든 요청을 src/mock이 녹화본으로 가로챈다. VITE_MOCK=off면 아래 주소를 실제로 부른다.
// 토큰 기본값은 백엔드 목 모드 전용 이름(mock:*, human:*)이고 실제 비밀값이 아니다.

/** 녹화본으로 도는 데모인지 */
export const MOCK = import.meta.env.VITE_MOCK !== 'off'

/** 고객지원·인사·AI 대화방 공통 로그: dashboard-api(8020) */
export const DASHBOARD_API_URL = import.meta.env.VITE_DASHBOARD_API_URL ?? 'http://localhost:8020'
/** 재무 AI: 안전 레이어(safety, 8001)와 재무 에이전트(finance, 8105) 조회 API */
export const SAFETY_API_URL = import.meta.env.VITE_SAFETY_API_URL ?? 'http://localhost:8001'
export const FINANCE_AGENT_URL = import.meta.env.VITE_FINANCE_AGENT_URL ?? 'http://localhost:8105'
/** C 보류 결정에 쓰는 사람 토큰. 목 모드 데모는 `human:<이름>`. 비어 있으면 결정 버튼을 끈다 */
export const SAFETY_HUMAN_TOKEN = import.meta.env.VITE_SAFETY_HUMAN_TOKEN ?? 'human:demo'
/** 총괄(orchestrator, 8000). 총괄은 CORS가 없어 기본값은 Vite 개발 프록시(/orch → vite.config.ts) */
export const ORCHESTRATOR_URL = import.meta.env.VITE_ORCHESTRATOR_URL ?? '/orch'
/**
 * 총괄 사건·부서 사용량을 읽는 shop-api 관리 읽기 중계(개발용 임시). 토큰은 목 모드 전용 `mock:admin`이다.
 * 진짜 로그인과 역할은 사람 인증(#265) 뒤에 이 자리를 대신한다.
 */
export const RELAY_API_URL = import.meta.env.VITE_SHOP_ADMIN_API_URL || 'http://localhost:8010'
export const RELAY_TOKEN = import.meta.env.VITE_SHOP_ADMIN_TOKEN || 'mock:admin'
/** 데이터분석 AI: 데이터 AI(data, 8106). 기본값 /data-a2a는 Vite 개발 프록시(vite.config.ts, DATA_PROXY_TARGET) */
export const DATA_A2A_URL = import.meta.env.VITE_DATA_A2A_URL ?? '/data-a2a'
/** 데이터 AI 직접 조회 토큰. 목 모드 전용(예: mock:dashboard-api). 비어 있으면 KPI 실시간 조회를 하지 않는다. 브라우저 저장소에 두지 않는다 */
export const DATA_A2A_TOKEN = import.meta.env.VITE_DATA_A2A_TOKEN ?? 'mock:dashboard-api'
/** 사람 토큰이 설정돼 있는지. 없으면 쓰기 버튼을 끄고 안내한다 */
export const HUMAN_TOKEN_SET = SAFETY_HUMAN_TOKEN.length > 0

/** 장애 대응: DejaFix 백엔드. 기본값 /dejafix-api는 개발 서버가 로그인까지 대신 해 주는 프록시(dejafix-proxy.ts) */
export const DEJAFIX_API_URL = import.meta.env.VITE_DEJAFIX_API_URL ?? '/dejafix-api'
/** DejaFix만 실제 서버로 부를지. 다른 AI는 녹화본으로 두고 장애 대응 탭만 실데이터로 볼 때 on */
export const DEJAFIX_LIVE = !MOCK || import.meta.env.VITE_DEJAFIX_LIVE === 'on'
