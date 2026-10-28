# services/hosts — PC·서버 연결

포트 8117 (예정) · 아직 백엔드 skill 없음

화면(`/hosts`, `/system/hosts`)은 Zabbix 에이전트 설치 → 호스트 등록 → DejaFix에서 보이는지 확인 흐름을 시연값으로 보여 준다.
ax-backend에는 이 일을 하는 skill이 없어서 폴더만 두었다.

만들 것:
- 호스트 목록 읽기: DejaFix 읽기 API(Zabbix `host.get`을 DejaFix가 읽기 전용 계정으로 부름)
- 호스트 등록: Zabbix 쓰기는 DejaFix 읽기 계정으로 못 한다. 쓰기 권한이 제한된 별도 계정이나 자동 등록(autoregistration)을 써야 한다
- 카드·포트를 `shared/agent-template/src/ax_agent_template/services.py`의 `hosts`에 채우고 `scripts/split_services.py` 대상에 넣는다
