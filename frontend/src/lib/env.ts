// 데모: 주소는 원래 대시보드와 같은 로컬 기본값을 쓰고, 모든 요청은 src/mock이 녹화본으로 가로챈다.
// 토큰 값은 백엔드 목 모드 전용 이름(mock:*, human:*)이고 실제 비밀값이 아니다.
export const DASHBOARD_API_URL = import.meta.env.VITE_DASHBOARD_API_URL ?? 'http://localhost:8020'
/** 상단 '쇼핑몰 보기' 링크가 여는 shop-web 주소 */
export const SHOP_WEB_URL = import.meta.env.VITE_SHOP_WEB_URL ?? 'http://localhost:3000'
/** 재무·안전 화면: 안전 레이어(safety, 8001)와 재무 에이전트(finance, 8105) 조회 API */
export const SAFETY_API_URL = import.meta.env.VITE_SAFETY_API_URL ?? 'http://localhost:8001'
export const FINANCE_AGENT_URL = import.meta.env.VITE_FINANCE_AGENT_URL ?? 'http://localhost:8105'
/** C 보류 결정에 쓰는 사람 토큰. 목 모드 데모는 `human:<이름>`. 비어 있으면 결정 버튼을 끈다 */
export const SAFETY_HUMAN_TOKEN = import.meta.env.VITE_SAFETY_HUMAN_TOKEN ?? 'human:demo'
/** AI 대화방: 총괄(orchestrator, 8000) 주소. 총괄은 CORS가 없어 기본값은 Vite 개발 프록시(/orch → vite.config.ts) */
export const ORCHESTRATOR_URL = import.meta.env.VITE_ORCHESTRATOR_URL ?? '/orch'
/** 상단 '기획개발' 링크가 여는 planning-web 주소. 게이트웨이에서는 `/admin/planning/` */
export const PLANNING_WEB_URL = import.meta.env.VITE_PLANNING_WEB_URL ?? 'http://localhost:3002'
/** `on`이면 관리자 화면 앞에 데모 비밀번호 입력이 선다. 실제 인증이 아니다 (고객이 실수로 들어오지 않게 하는 정도) */
export const ADMIN_LOGIN = import.meta.env.VITE_ADMIN_LOGIN === 'on'
export const ADMIN_DEMO_PASSWORD = import.meta.env.VITE_ADMIN_DEMO_PASSWORD ?? 'catbooks'
/**
 * 총괄 사건·부서 사용량을 읽는 shop-api 관리 읽기 중계(개발용 임시). 토큰은 목 모드 전용 `mock:admin`이다.
 * 개발 서버에서만 기본값(`http://localhost:8010`, `mock:admin`)이 들어가고, 운영 빌드는 환경변수가 없으면
 * 꺼진다. 진짜 로그인과 역할은 사람 인증(#265) 뒤에 이 자리를 대신한다.
 */
export const RELAY_API_URL = import.meta.env.VITE_SHOP_ADMIN_API_URL || 'http://localhost:8010'
export const RELAY_TOKEN = import.meta.env.VITE_SHOP_ADMIN_TOKEN || 'mock:admin'
/** 데이터분석 AI 화면: 데이터 AI(data, 8106) 주소. 기본값 /data-a2a는 Vite 개발 프록시(vite.config.ts, DATA_PROXY_TARGET) */
export const DATA_A2A_URL = import.meta.env.VITE_DATA_A2A_URL ?? '/data-a2a'
/** 데이터 AI 직접 조회 토큰. 목 모드 전용(예: mock:dashboard-api). 비어 있으면 KPI 실시간 조회를 하지 않는다. 브라우저 저장소에 두지 않는다 */
export const DATA_A2A_TOKEN = import.meta.env.VITE_DATA_A2A_TOKEN ?? 'mock:dashboard-api'
/** 사람 토큰이 설정돼 있는지. 없으면 쓰기 버튼을 끄고 안내한다 */
export const HUMAN_TOKEN_SET = SAFETY_HUMAN_TOKEN.length > 0
