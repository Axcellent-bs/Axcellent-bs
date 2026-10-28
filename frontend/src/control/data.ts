// 관제 센터 목 데이터. 고객사에 설치된 AXcellent가 보내는 "상태 신호"만 있다고 가정한다.
// 업무 데이터(주문·문의·인사 기록)는 받지 않는다. 숫자는 모두 데모용 예시다.

export type Health = 'ok' | 'warn' | 'down'
export type Company = {
  id: string
  name: string
  industry: string
  network: string
  version: string
  /** 마지막 상태 신호를 받은 뒤 지난 분 */
  lastSeenMin: number
  health: Health
  pcs: number
  services: number
  incidentsToday: number
  /** AI가 사람 없이 끝낸 비율(%) */
  autoRate: number
  /** 사람 승인을 거친 건수(오늘) */
  approvals: number
  /** 안전 관문에서 막힌 건수(오늘) */
  blocked: number
  rollbacks: number
  /** 오늘 LLM 호출 수와 비용(원) */
  llmCalls: number
  costKrw: number
}

export const LATEST_VERSION = '0.9.3'

export const COMPANIES: Company[] = [
  { id: 'catbooks', name: '캣북스', industry: '온라인 서점', network: '192.168.10.0/24', version: '0.9.3', lastSeenMin: 0, health: 'warn', pcs: 9, services: 13, incidentsToday: 3, autoRate: 81, approvals: 14, blocked: 2, rollbacks: 1, llmCalls: 4820, costKrw: 38400 },
  { id: 'greenmart', name: '그린마트', industry: '식품 유통', network: '10.20.0.0/16', version: '0.9.3', lastSeenMin: 1, health: 'ok', pcs: 6, services: 8, incidentsToday: 0, autoRate: 88, approvals: 6, blocked: 0, rollbacks: 0, llmCalls: 2310, costKrw: 15900 },
  { id: 'haneul', name: '하늘여행', industry: '여행사', network: '172.16.5.0/24', version: '0.9.1', lastSeenMin: 2, health: 'warn', pcs: 5, services: 7, incidentsToday: 2, autoRate: 74, approvals: 11, blocked: 1, rollbacks: 0, llmCalls: 1980, costKrw: 14200 },
  { id: 'bareun', name: '바른의원', industry: '병원', network: '192.168.50.0/24', version: '0.9.2', lastSeenMin: 47, health: 'down', pcs: 3, services: 4, incidentsToday: 1, autoRate: 69, approvals: 3, blocked: 0, rollbacks: 0, llmCalls: 410, costKrw: 2600 },
  { id: 'hanbit', name: '한빛물산', industry: '제조', network: '10.8.0.0/16', version: '0.9.3', lastSeenMin: 0, health: 'ok', pcs: 7, services: 6, incidentsToday: 0, autoRate: 85, approvals: 4, blocked: 0, rollbacks: 0, llmCalls: 1270, costKrw: 8800 },
]

export type Origin = '우리' | '고객사' | '외부'
export type IncidentRow = {
  id: string
  company: string
  title: string
  service: string
  origin: Origin
  severity: '높음' | '보통' | '낮음'
  status: '대응 중' | '안내 완료' | '해결'
  startedAt: string
  note: string
}

export const INCIDENTS: IncidentRow[] = [
  { id: 'INC-2041', company: '캣북스', title: 'PG 결제 승인 지연', service: '고객 문의', origin: '외부', severity: '높음', status: '대응 중', startedAt: '10:24', note: '결제사 장애. 고객 안내문 자동 게시, 보상 기준표 대기' },
  { id: 'INC-2040', company: '캣북스', title: '쿠폰 발급 키 비정상 호출 급증', service: '공지·쿠폰', origin: '고객사', severity: '높음', status: '대응 중', startedAt: '09:58', note: '발급 자동 정지, 키 교체 요청 보냄' },
  { id: 'INC-2039', company: '하늘여행', title: '항공 예약 API 응답 없음', service: '클레임·보상', origin: '외부', severity: '보통', status: '안내 완료', startedAt: '09:12', note: '공급사 점검 시간. 일정 변경 문의는 사람 승인으로 전환' },
  { id: 'INC-2038', company: '바른의원', title: '상태 신호 끊김 47분', service: 'PC·서버 연결', origin: '고객사', severity: '보통', status: '대응 중', startedAt: '09:01', note: '사내망 점검 공지 확인 전. 담당자에게 연락' },
  { id: 'INC-2037', company: '하늘여행', title: '0.9.1 버전 답변 초안 길이 오류', service: '고객 문의', origin: '우리', severity: '낮음', status: '해결', startedAt: '08:30', note: '0.9.3에서 수정. 업데이트 안내 보냄' },
  { id: 'INC-2036', company: '캣북스', title: '재무 결산 배치 지연', service: '재무 관리', origin: '우리', severity: '낮음', status: '해결', startedAt: '07:45', note: '재시도로 완료' },
]

export type ServiceQuality = { name: string; autoRate: number; approvals: number; blocked: number; rollbacks: number; reported: number }
export const QUALITY: ServiceQuality[] = [
  { name: '고객 문의', autoRate: 86, approvals: 9, blocked: 1, rollbacks: 0, reported: 2 },
  { name: '클레임·보상', autoRate: 62, approvals: 12, blocked: 2, rollbacks: 0, reported: 1 },
  { name: '리뷰·지식', autoRate: 91, approvals: 2, blocked: 0, rollbacks: 0, reported: 0 },
  { name: '공지·쿠폰', autoRate: 73, approvals: 5, blocked: 0, rollbacks: 1, reported: 0 },
  { name: '재무 관리', autoRate: 58, approvals: 7, blocked: 0, rollbacks: 0, reported: 0 },
  { name: '장애 대응', autoRate: 77, approvals: 3, blocked: 0, rollbacks: 0, reported: 1 },
  { name: '개발·배포', autoRate: 64, approvals: 4, blocked: 0, rollbacks: 0, reported: 0 },
]

export type ServiceCost = { name: string; calls: number; costKrw: number; model: string }
export const COST_BY_SERVICE: ServiceCost[] = [
  { name: '고객 문의', calls: 4210, costKrw: 21800, model: '작은 모델 + 어려운 건만 큰 모델' },
  { name: '장애 대응', calls: 1320, costKrw: 17400, model: '큰 모델' },
  { name: '클레임·보상', calls: 1580, costKrw: 11900, model: '작은 모델' },
  { name: '개발·배포', calls: 960, costKrw: 10600, model: '큰 모델' },
  { name: '공지·쿠폰', calls: 1240, costKrw: 6100, model: '작은 모델' },
  { name: '재무 관리', calls: 820, costKrw: 5200, model: '작은 모델' },
  { name: '리뷰·지식', calls: 2450, costKrw: 6900, model: '작은 모델' },
]

/** 최근 7일 전체 비용(원). 10/25에 고객 문의를 작은 모델로 바꿨다 */
export const COST_TREND: { day: string; costKrw: number }[] = [
  { day: '10/22', costKrw: 118000 },
  { day: '10/23', costKrw: 121500 },
  { day: '10/24', costKrw: 119200 },
  { day: '10/25', costKrw: 96300 },
  { day: '10/26', costKrw: 82100 },
  { day: '10/27', costKrw: 80700 },
  { day: '10/28', costKrw: 79900 },
]

export const won = (n: number) => `${n.toLocaleString('ko-KR')}원`
