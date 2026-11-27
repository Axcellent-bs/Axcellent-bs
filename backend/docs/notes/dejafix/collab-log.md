# dejafix 협업 메모

- 2026-10-23 support · DejaFix로 나가는 고객 데이터 필드 암호화 (#316) · 승인·머지. DejaFix 쪽 복호화는 실제 송신 skill이 생기면 to:dejafix로 받기로 함
- 2026-11-11 scripts · `run_all.sh`가 DejaFix 계정이 있을 때 dejafix-proxy(8030)를 같이 띄움 (#968) · 머지
- 2026-11-17 tool-connections·frontend · DejaFix 연결을 도구 연결 보관함에서 읽고, 시스템 관리 › 도구 연결에 DejaFix 연결 카드 (#1196, #1199) · 머지
- 2026-11-19 dejafix-proxy · 부르는 쪽 토큰 확인, 정해 둔 조회 세 가지만 넘김 (#1412), 회사 주소는 검사한 IP로만 연결 (#1420), 같이 설치된 DejaFix를 "연결 전"으로 잘못 띄우던 것 고침 (#1440) · 머지
- 2026-11-19 frontend · DejaFix 연결 탭에 "연결한 PC" 칸, 화면에서 다른 DejaFix 주소 넣기는 뺌 (#1441) · 머지
- 2026-11-25 회사 분리(로드맵 6단계) · 장애(DejaFix)를 요청 회사 것만 (#1776), 연결한 PC를 DejaFix 호스트 목록으로 (#1815), 회원·서버 관리에서 회사 Zabbix 주소·읽기 토큰으로 DejaFix 잇기 (#1835) · 머지. 서버 nginx에 `/dejafix-api/settings/zabbix` 열기는 11/26 적용
- 2026-11-26 dejafix-proxy · 같이 설치된 DejaFix를 이 설치의 모든 회사가 씀 (#1880), 요청에 부르는 회사를 싣고 회사별을 모르는 DejaFix의 장애는 캣북스에만 (#1884), 옛 DejaFix의 405도 501 새 버전 안내로 (#1892), 다른 회사 Zabbix 설정은 409 하나만 (#1950) · 머지
- 2026-11-26 frontend · 회사 연결 지도의 사내 PC를 DejaFix 호스트 목록으로 (#1871), 옛 DejaFix면 Zabbix 저장 폼 숨김 (#1948), 에이전트 없이 감시하는 호스트를 따로 표시 (#1953) · 머지
- 2026-11-27 DejaFix(서버) · 운영 서버 DejaFix를 dev `566de75`로 재배포. 회사별 분리(DejaFix #019)는 `DEFAULT_COMPANY_ID` 미설정으로 꺼 둠, Agent 이관이 들어올 때 `catbooks`로 켜기로 합의 (`docs/ops/dejafix-server.md`)
