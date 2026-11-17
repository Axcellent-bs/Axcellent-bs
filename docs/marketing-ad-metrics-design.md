# 마케팅: 광고 데이터 수동 입력(B)과 마케팅 화면 입력·추적 링크(C) 설계

상태: **초안(설계만, 코드 없음)**. 제안: 배병환(고객지원), 2026-11-18. 마케팅(재헌)·데이터분석(재현)·기획(예진) 담당과 아래 "정할 것"을 맞춘다.
배경: 유입경로 기록 #647 중 남은 3번(외부 광고 데이터)이 막혀 있고, 이것이 데이터분석 #648의 광고 결합(클릭 대비 주문, 광고비 대비 판매액)을 기다리게 한다.
(4번 게시 알림은 재헌 님 몫, 쇼핑몰 화면 #656은 재현 님 몫이다. 이 문서는 B·C만 다룬다.)

## 지금 있는 것 (코드로 확인)
- `CampaignResultInput`(재무 계약)에 `spend_krw`·`impressions`·`clicks`가 이미 있다. 그러나 마케팅 화면 "결과 저장"
  (`CampaignControl.tsx`)은 광고비·쿠폰·판매액·주문만 받고 **노출·클릭 칸이 없다**. 그리고 이 값은 캠페인이 끝난 뒤 한 번 재무에 기록하는 값이라
  데이터분석이 캠페인별로 읽어 갈 곳이 아니다.
- 데이터분석은 "광고 데이터"를 아직 못 읽는 것으로 표시해 둔다(`data_sources.py`의 `AD_DATA_PENDING = "#647 광고 부분 대기"`).
- 링크 꼬리표는 `build_campaign_link`(마케팅 skill, read)이 만든다. 요청자에 `dashboard-api`가 이미 들어 있다. **dashboard-api 경로와 화면은 아직 없다.**
- 광고 플랫폼 연결은 없다(실제 광고는 나가지 않고 광고비는 사람이 보고서를 보고 적는다).

## 원칙
- 숫자는 **사람이 적은 값을 그대로** 저장한다. 모르는 값은 비워 두고(`None`) 0으로 채우지 않는다. 계산(클릭률 등)은 코드가 한다.
- 계약은 `shared/contracts`에 **추가만**. 마케팅은 저장·조회만 하고 판단하지 않는다. 데이터분석이 이 값으로 전환율·광고비 대비 판매액을 계산한다.
- 나중에 광고 플랫폼을 연결해도 호출 모양이 바뀌지 않게 `source`(수동·보고서 파일 등)로 출처를 구분한다.
- 이 화면에도 데이터를 지어내지 않는다. 입력이 없으면 빈 상태와 "아직 입력 없음"을 보인다.

## B. 백엔드: 캠페인별 광고 숫자 저장·조회

### 저장 모양
캠페인(`campaign_id`, `cmp_PROP-n`) × 날짜(KST) × 출처(`source`) × 매체(`medium`)당 한 줄.

| 칸 | 뜻 |
| --- | --- |
| `campaign_id` | 받아 둔 기획안의 캠페인 번호. 모르는 번호는 거절(`unknown_campaign`) |
| `date` | 한국 날짜 `YYYY-MM-DD`. 오늘보다 뒤는 거절 |
| `source`·`medium` | `links.py`의 허용 목록·조합표를 그대로 쓴다(banner·notice·instagram·google·youtube × onsite·social·cpc). 어긋나면 거절 |
| `impressions`·`clicks` | 정수 ≥ 0, 모르면 `None`. `clicks ≤ impressions` |
| `spend_krw` | 정수 ≥ 0 원, 모르면 `None` |
| `entered_by`·`entered_at` | 적은 사람(사람 토큰에서만, 요청 본문에서 받지 않는다)과 시각 |
| `report_ref` | 광고 보고서 이름·번호 같은 근거(선택, 120자) |

- 같은 (캠페인, 날짜, source, medium)을 다시 적으면 **고쳐 쓴다**. 이전 값은 `revisions`에 남겨(시각·적은 사람) 누가 언제 바꿨는지 볼 수 있다.
- 저장은 SQLite 파일 `MARKETING_AD_METRICS_DB`(`run_all.sh`는 `.data/marketing_ad_metrics.db`). 비우면 메모리라 다시 시작하면 사라진다(브리프·미디어 장부와 같은 방식).

### skill (마케팅, 계약은 `schemas/ad_metrics.py` 새 파일)
- `record_campaign_ad_metrics` — 쓰기. 입력: 위 칸. 출력: `recorded`/`updated`/`rejected`+`reason_code`.
- `get_campaign_ad_metrics` — 읽기. 입력: `campaign_id`(또는 기간). 출력: 줄 목록과 합계(노출·클릭·광고비)와 클릭률.
  입력이 하나도 없으면 `status: unknown`과 이유("아직 입력 없음")를 돌려주고 0으로 채우지 않는다. 일부 칸만 있으면 합계에서 그 칸을 `None`으로 두고 어느 날짜가 비었는지 알린다.
  요청자: `data`, `planning`, `finance`, `orchestrator`, `dashboard-api`.

### 안전 관문
- 기록을 바꾸는 호출이라 `execute()`를 거치는 것이 규칙이다. 금액이 나가는 일이 아니므로 A 등급(자동 통과, 요청자 제한)으로 안전 장치 `registry.toml`에 한 줄을 더한다
  (`receive_campaign_brief`와 같은 짜임, 테스트 `test_registry_actions.py`에 한 건). 이 한 줄이 이 계획에서 **safety 쪽을 건드리는 유일한 곳**이다.
- 관문이 없으면 쓰지 않고(fail-closed) 입력이 사라지지 않게 화면에 오류를 보인다.

### 건드리는 파일 (재헌 님 작업과 겹침 최소화)
- 새 파일: `marketing_agent/ad_metrics.py`(저장·검증·합계), `contracts/schemas/ad_metrics.py`, 테스트 `test_ad_metrics.py`.
- 한 줄씩 고치는 곳(등록): 마케팅 `handlers.py`·`agent-card.json`, `services/coupon/agent-card.json`, `agent-template/services.py` — 카드는 세 곳을 맞춘다. 재헌 님 확인을 받는다.

### 데이터분석 연결 (재현 님, 이 PR 밖)
- `data_sources.py`의 광고 부분 대기 자리에서 `get_campaign_ad_metrics`를 `call_agent`로 읽는다. 없으면 지금처럼 그 칸만 `unknown`.
- 전환율·광고비 대비 판매액은 데이터가 코드로 계산한다(마케팅은 계산하지 않는다).

## C. 프론트: 마케팅 화면 입력 칸과 추적 링크 복사

### dashboard-api (새 경로)
- `GET /api/dashboard/marketing/campaigns/{campaign_id}/ad-metrics` → `get_campaign_ad_metrics`.
- `POST /api/dashboard/marketing/campaigns/{campaign_id}/ad-metrics` → `record_campaign_ad_metrics`. 사람 토큰만, `entered_by`는 토큰에서 채운다.
- `POST /api/dashboard/marketing/campaigns/{campaign_id}/link` → `build_campaign_link`(읽기). 어긋난 입력은 이유를 그대로 돌려준다.

### 화면 (`/marketing/campaign` 탭, 새 파일)
- `AdMetricsPanel.tsx`: 받은 캠페인마다 입력 줄(날짜·source·medium·노출·클릭·광고비·보고서 근거), 저장된 줄 목록, 합계와 클릭률.
  - source를 고르면 맞는 medium만 보이게 한다. 조합표는 서버 응답에서 받아 화면에 따로 복사해 두지 않는다.
  - 값을 비우면 "모름"으로 저장한다. 사람 토큰이 없으면 입력은 잠그고 읽기만 보인다(`CampaignControl`과 같은 규칙).
  - 입력이 하나도 없으면 빈 상태 + "준비 중"이 아니라 "아직 입력 없음"을 보인다(출처가 없어서가 아니라 사람이 아직 안 적었기 때문).
- `TrackedLinkBox.tsx`: 캠페인·source·medium을 고르고 기본 주소를 넣으면 꼬리표가 붙은 링크를 보이고 **복사** 버튼을 둔다(클립보드가 막히면 선택해서 복사하도록 안내, try/catch).
  기본 주소는 코드에 박지 않는다(설정값, 아래 "정할 것").
- 결과 저장 칸(`CampaignControl`)은 저장된 광고비 합계를 **미리 채워 주되 사람이 고칠 수 있게** 한다(이중 입력 방지). 고치면 출처가 사람이 적은 값으로 남는다(지금 규칙과 같다).
- 디자인은 AXcellent 공용 클래스(`card`·`field`·`btn-primary`·`btn-ghost`)만 쓴다. 타입 검사를 우회하지 않는다.

## 쪼개서 올리기
1. **PR-1 백엔드**: 계약 + 저장소 + skill 2개 + safety 한 줄 + 카드 3곳 + 테스트. (재헌 님 리뷰)
2. **PR-2 dashboard-api**: 경로 3개 + 테스트.
3. **PR-3 프론트**: `AdMetricsPanel`·`TrackedLinkBox` + 결과 저장 칸 미리 채우기.
4. **(재현 님)** 데이터분석 커넥터와 #648 3번.
PR-1이 머지돼야 2·3과 재현 님 작업이 실제 값을 볼 수 있다. 서로 다른 파일이라 2와 3은 같이 진행할 수 있다.

## 테스트
- 검증: 모르는 캠페인·미래 날짜·`clicks > impressions`·음수·어긋난 source/medium 거절.
- 고쳐 쓰기: 같은 키 재입력 시 값이 바뀌고 `revisions`가 남음, 멱등(같은 요청 두 번은 한 번과 같음).
- 조회: 입력 없음 → `unknown`+이유(0 아님), 일부 칸만 있음 → 그 칸 `None`, 합계·클릭률 계산.
- 다시 시작해도 파일 저장이 남는다. 관문 거절·무응답이면 저장하지 않는다.
- 프론트: 타입 검사·빌드, 조합표 연동, 비어 있는 입력은 "모름", 사람 토큰 없음 시 잠금, 링크 복사 실패 시 안내.

## 정할 것 (담당과 맞출 것)
1. **안전 관문 등급**: 광고 숫자 기록을 A 등급 한 줄로 등록해도 되는지(안전 규칙 담당 확인). 대안은 대화 기록처럼 `execute()` 없이 저장하는 것인데, 재무·데이터가 읽는 숫자라 권하지 않는다.
2. **기본 주소**: 추적 링크의 `base_url` 기본값을 어디서 받을지(마케팅 설정값 vs 화면 입력). 코드에 주소를 박지 않는다.
3. **입력 단위**: 날짜별(권장) vs 캠페인 합계 한 줄. 날짜별이어야 `campaign_effect`의 기간과 맞출 수 있다.
4. **누가 적나**: 마케팅 담당 사람 토큰만(권장)인지, 다른 부서도 되는지.
5. **재헌 님 확인**: 카드 3곳과 `handlers.py` 등록 줄을 같이 고쳐도 되는지, 게시 알림(4번) 작업과 같은 PR로 묶을지.
6. 이 값이 재무 손익(`record_campaign_result`)과 어긋날 때(예: 저장된 광고비 합계와 결과 저장 때 적은 광고비가 다름) 화면이 차이를 보여 줄지.

## 이 설계로 하지 않는 것
- 광고 플랫폼(구글 광고 등) 자동 연결 — 실제 광고를 집행할 때 `source`만 바꿔 붙인다.
- 클릭·전환 계산 — 데이터분석이 한다.
- 쇼핑몰 화면 꼬리표 전송(#656), 게시 알림(#647의 4번) — 각 담당.
