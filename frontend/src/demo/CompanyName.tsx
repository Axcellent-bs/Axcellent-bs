import { useCompany } from './company'

/** 화면 문구 안의 회사 이름. AXcellent에 연결된 고객사를 따라가고, 연결 전이면 "회사"로 쓴다 */
export function CompanyName() {
  return <>{useCompany()?.name ?? '회사'}</>
}
