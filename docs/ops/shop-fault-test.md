# 쇼핑몰 장애 주입 시험 안내

캣북스 쇼핑몰(shop-api)에 결제 장애를 일부러 넣고, AI들이 사건을 열어 처리하는 흐름을 시험할 때 읽는 문서입니다.
숫자는 모두 코드에서 확인한 값이고, 근거(파일:줄)는 맨 끝에 모았습니다. [1]처럼 번호로 가리킵니다.

## 1. 준비

- **가상 주문 켜기:** `backend/.env`에 `SHOP_TRAFFIC=on`을 넣고 `run_all.sh`로 띄웁니다. [1]
  - 기본값은 꺼짐입니다. 켜면 가상 주문이 분당 20건(3초마다 1건) 들어갑니다. [2]
  - 감시는 최근 60초 안에 결제 표본이 5건 이상 있어야 판단합니다. [3] 주문이 없으면 장애를 넣어도 감지되지 않습니다.
- **`run_all.sh`를 띄운 PC에서만 넣을 수 있습니다.**
  - 장애를 넣는 데는 쇼핑몰 admin 토큰이 필요합니다.
  - 이 토큰은 `run_all.sh`가 `backend/.data/web.env`의 `VITE_SHOP_ADMIN_TOKEN`에 씁니다. [4]
  - `shop_fault.sh`는 그 파일에서 토큰을 읽고, 파일이 없으면 멈춥니다. [5]
  - 251 공용 서버에서는 251에서 `run_all.sh`를 띄운 재현님이 넣어야 합니다.
- **화면에는 장애 버튼이 없습니다.** 명령으로만 넣습니다. [6]

## 2. 장애 넣기

```bash
bash backend/scripts/shop_fault.sh failure   # 결제가 서버 오류(5xx)로 실패
bash backend/scripts/shop_fault.sh latency   # 결제 응답 2초 지연
bash backend/scripts/shop_fault.sh           # 지금 상태 보기
```

| 종류 | 무엇이 일어나나 | 감지 기준 |
|---|---|---|
| `failure` | 결제가 서버 오류(5xx)로 실패 | 최근 60초 서버 오류 비율이 2%를 넘음 [7] |
| `latency` | 결제 응답이 2초(2000ms) 늦어짐 [8] | 최근 60초 응답 시간 p95가 1000ms를 넘음 [7]. p95는 느린 쪽 5%의 경계값 |

**사건이 열리기까지 약 25초입니다.** [6]

코드로 나눠 보면 아래 세 시간이 더해져 20~28초쯤 걸리고, 이벤트를 총괄에 전달하는 시간이 조금 붙습니다.

| 구간 | 시간 | 근거 |
|---|---|---|
| 첫 실패 결제가 생기기까지 | 최대 3초 (주문이 3초마다 들어옴) | [2] |
| 기준 초과가 이어져야 하는 시간 | 20초 (코드 기본값 60초를 `run_all.sh`가 20초로 줄임) | [9] [10] |
| 감시가 판단하는 간격 | 5초마다 | [9] [11] |

## 3. 자동 흐름

사건이 열리면 아래 순서로 이어집니다. 번호는 흐름 순서이고, 실제로는 몇 단계가 동시에 돕니다.
- 진단과 영향 분석은 함께 시작합니다.
- 되돌리기는 진단과 영향 분석이 둘 다 끝난 뒤 돕니다. [12]

1. **원인 진단** (인프라 AI) [13]
2. **원인 후보 추정** (데이터 AI) [14]
3. **되돌리기** (인프라 AI) [15]
   - 안전 관문에서 사람 승인 대기에 걸릴 수 있습니다.
   - 최대 10분(600초) 기다리고, 마감이 지나면 자동으로 거절됩니다. [16] [17]
   - **승인 화면:** 재무 관리(`/finance`)의 **"안전 장치 보류 큐"**에서 승인·거절합니다. [18]
   - 총괄·사건 화면(`/orchestrator`)은 승인 대기를 보여 주기만 하고, 여기서는 결정할 수 없습니다. [17]
   - 원인 배포를 못 찾았을 때는 되돌리기 대신 "과거 같은 장애의 처리 방식 다시 하기"가 돌 수 있습니다(5장).
4. **영향 분석:** 실패 주문, 영향 고객, 피해 금액 (데이터 AI) [19]
5. **점검 공지:** 초안 → 게시 (마케팅 AI) [20]
6. **피해 고객 보상:** 영향 고객에게 일괄 보상 (고객지원 AI) [21]
7. **복구 확인** (인프라 AI) [22]
   - 30초 간격으로 최대 5번 확인합니다. [23]
   - 인프라 지표와 업무 지표가 둘 다 정상이어야 복구로 봅니다.
8. **정상화 공지, 장애 공지 배너 내리기** (마케팅 AI) [24]
9. **재무 기록, 처리 시간 요약, 사례 기록** (재무·데이터·인사 AI) [25]

## 4. 끝나면 반드시 해제

```bash
bash backend/scripts/shop_fault.sh clear   # 해제
bash backend/scripts/shop_fault.sh         # 풀렸는지 확인
```

- 되돌리기가 승인되지 않으면 장애가 그대로 남습니다.
- 해제하지 않으면 복구 확인이 실패하고, **정상화 공지 없이 사람에게 넘어갑니다.** [26]
- 장애가 걸려 있는 동안은 그 쇼핑몰의 결제가 모두 실패합니다.
- **해제는 복구가 아닙니다.** 해제한 순간에는 "해제됨"만 남고, 해제 뒤 결제가 정상으로 지속 기준(`SHOP_DEGRADED_SUSTAIN_S`, 기본 60초)만큼 이어져야 쇼핑몰이 복구 신호를 보냅니다. 해제 뒤 결제가 없으면 표본이 모이지 않아 복구로 판정되지 않습니다.

## 5. 1차·2차 시험이 다르게 흘러가는 이유

해결된 사건은 사례 카드로 남습니다.

- **카드에 남는 처리 방식:** 1차에서 사람이 `shop_fault.sh clear`로 해제했다면, 카드에 처리 방식 "장애 해제(`release_fault`)"가 남습니다. [27]
- **2차가 스스로 해제하는 조건:** 같은 장애를 다시 넣으면, 아래 조건이 맞을 때 인프라 AI가 **"과거 같은 장애의 처리 방식 다시 하기"로 스스로 해제**합니다. [28] [29] 이것은 의도된 동작입니다.
  - 원인 배포를 못 찾았다. 원인을 찾았으면 되돌리기로 처리합니다.
  - 과거 사례와 85% 이상 비슷하다.
  - 지금 결제 서버가 실제로 이상하고, **장애 스위치가 켜져 있다.** 스위치가 꺼진 채 결제가 이상하면(결제사 지연 등) 다른 원인이라 해제하지 않고 사람에게 넘깁니다.
  - 이 해제도 안전 관문을 거치므로 승인 대기가 될 수 있습니다. [29]
- **결과 보는 곳:** 1차와 2차의 복구 시간은 **데이터 분석 → 운영 지표(`/data/ops`) → 복구 속도 → "사건별 복구 시간 비교"**에서 "1차", "2차"로 나란히 보입니다. 2건 이상이면 몇 % 빨라졌는지 나옵니다. [30]
  - 이 비교에는 각 사건의 복구 시각 기록이 필요합니다. 총괄이 그 기록을 남기게 하는 변경(PR #1015)이 들어간 서버에서 해결된 사건부터 보입니다.

## 6. 남는 데이터

시험해도 데이터는 실제로 바뀌고, 지워지지 않습니다.

| 남는 것 | 만드는 단계 |
|---|---|
| 실패 주문 | 장애 중 결제 실패 |
| 보상 쿠폰 | 피해 고객 보상 [21] |
| 공지 배너 | 점검 공지 게시 [20]. 해결되면 내려갑니다 [24] |
| 사례 카드 | 사례 기록 [25]. 다음 시험에 영향을 줍니다(5장) |

## 7. 251 공용 서버 규칙

- **넣기 전과 끝난 뒤 #소통 채널에 알립니다.** 시작 알림과 종료 알림 둘 다 필수입니다.
- **다른 사람의 시연 시간과 겹치지 않게 합니다.** 장애 중에는 그 쇼핑몰의 결제가 모두 실패해서 다른 팀원의 시연이 망가집니다.

## 8. 장애 시나리오 세 가지

| 시나리오 | 넣기 | AI가 하는 일 | 사람이 하는 일 |
|---|---|---|---|
| A. 장애 스위치 | `shop_fault.sh failure` | 같은 유형 과거 사례가 해제로 풀렸으면 장애 상태 해제(`release_fault`) | 끝나면 `shop_fault.sh`로 꺼졌는지 확인 |
| B. 결제사 쪽 지연 | `shop_fault.sh pg-slow` | 해제하지 않음(`not_needed`) → 원인 확인·사람 이관 | `shop_fault.sh pg-ok` |
| C. 잘못된 배포 | `demo_deploy.sh --bad` | 원인 배포 진단 → 결제 설정을 이전 버전으로 실제 되돌리기(`rollback_deployment`) | 등급이 C면 승인. 끝나면 `demo_deploy.sh --reset` |

세 시나리오 모두 **복구는 지표로 판정합니다.** 조치(해제·되돌리기)를 한 순간이 아니라, 그 뒤 정상 결제가 지속 기준(로컬 20초)만큼 이어져야 쇼핑몰이 복구 신호를 보내고, 복구 검증이 그 뒤에 확인합니다.

### 시나리오 C: 잘못된 배포 (장애 스위치 없음)

```bash
bash backend/scripts/demo_deploy.sh --bad     # 결제 설정 v1 → v2, 배포·PR 기록
bash backend/scripts/demo_deploy.sh --reset   # 끝나면 (AI가 되돌렸어도 기록 정리를 위해) 한 번
```

- **`--bad`가 하는 일:**
  1. 쇼핑몰 결제 설정을 v2로 바꿉니다. v2는 결제사 승인이 500대 오류(`FAILED_INTERNAL_SYSTEM_PROCESSING`)로 실패합니다. 토스 테스트 키면 승인 요청에 `TossPayments-Test-Code`로 붙이고, 가짜 결제사면 같은 코드로 실패시킵니다. **장애 스위치(`active`)는 꺼진 그대로**라 장애 해제로는 안 풀립니다.
  2. 인프라 AI에 배포 기록을 남깁니다: 이전 v1(쇼핑몰이 v1이 된 시각, 기준이라 원인 후보가 아님) → 새 v2(지금).
  3. 개발 AI에 v2로 나간 머지 PR을 남깁니다. 되돌리기 교차 검증이 "배포 직전 30분 안 머지"로 이 PR을 찾습니다.
- **흐름:** 결제 실패가 서버 오류 비율로 잡혀 약 25초 뒤 사건이 열립니다 → 진단이 v2 배포를 원인 후보(확신도 high: 후보 하나, 배포 뒤 15분 안)로 찾습니다 → 되돌리기는 안전 관문 B(개발 AI 교차 검증)를 거쳐 실행됩니다. 위험하면(확신도 낮음 등) C로 사람 승인을 기다립니다 → 인프라가 쇼핑몰 결제 설정을 v1로 되돌립니다(되돌리기 전 v2 → 후 v1이어야 성공) → 정상 결제가 이어지면 복구 신호 → 복구 검증 → 정상화 공지.
- **인프라 토큰은 결제 설정을 "이전 버전으로 되돌리기"만 할 수 있습니다.** 아무 버전으로 바꾸기(배포)는 사람(admin)만 합니다.
- **라이브 결제 키가 있으면 v2를 거절합니다.**
- 해결 기록에는 "쇼핑몰 서버 이전 버전으로 되돌림 (v2 → v1)", 처리 방식 `rollback_deployment`가 남습니다. 다음에 같은 유형 장애에서 과거 사례가 되돌리기였으면 장애 상태 해제를 하지 않습니다.
- 배포·PR 기록은 인프라·개발 서버 메모리에만 남아 다시 켜면 사라집니다. 쇼핑몰 결제 설정도 다시 켜면 v1입니다.

## 부록. 같이 쓸 수 있는 도구

### `slow_agent.ps1`: 부서 AI 응답 끊김 만들기
- 부서 AI 서버 하나를 잠깐 멈췄다가 풀어서, 총괄이 다시 시도하는 장면을 만듭니다.
- 에이전트 평가 화면에 "확인 필요"가 뜹니다. [31]
- **Windows 로컬 전용**입니다. [31]
- `-HoldSeconds`는 **30초보다 길어야** 다시 시도가 생깁니다. 총괄이 부서를 한 번 부를 때 마감이 30초입니다. [32]

```powershell
bash backend/scripts/shop_fault.sh failure
powershell -File backend/scripts/slow_agent.ps1 -Port 8106 -DelaySeconds 20 -HoldSeconds 38
```

### `shop_fault.sh pg-slow`: AI가 고치지 못하는 장애 시연 (결제사 쪽 지연)
- **시험용 장애입니다.** 결제마다 실제로 지정한 시간(기본 1500ms, 최대 10000ms)만큼 기다리고, 그 지연이 모니터 표본에 그대로 남습니다. 응답이 느려져 p95 기준을 넘으면 사건이 열립니다.
- **쇼핑몰 장애 스위치가 아닙니다.** 상태의 `active`는 false로 남습니다. 그래서 인프라 AI는 "과거 같은 장애의 처리 방식 다시 하기"(장애 해제)를 하지 않고(`not_needed`), 사건은 원인 확인·사람 이관으로 갑니다. 인프라 AI가 해제 API를 불러도 이 지연은 꺼지지 않습니다.
- **켜기·끄기는 사람(admin 토큰)만 합니다.** `pg-ok`로 끈 뒤에도 바로 복구가 아닙니다. 느린 표본이 1분 창에서 빠지고 정상 결제가 지속 기준(로컬 20초)만큼 이어져야 쇼핑몰이 복구 신호(`recovery=metrics_normal`, 해제한 쪽 없음)를 보냅니다. 끈 뒤 대략 1분 20초 걸립니다.
- 결제가 있어야 판정됩니다. 로컬에서는 `SHOP_TRAFFIC=on`으로 가상 주문을 켜 둡니다. 가상 주문의 요청 마감이 10초라 지연은 10초보다 짧게 둡니다.
- 상태 보기(`shop_fault.sh`)에 "결제사 쪽 지연: 켜짐/꺼짐"이 장애 스위치와 따로 나옵니다.

```bash
bash backend/scripts/shop_fault.sh pg-slow 1500   # 결제사 쪽 지연 켜기
bash backend/scripts/shop_fault.sh                # 상태 (결제사 쪽 지연 줄이 따로 보임)
bash backend/scripts/shop_fault.sh pg-ok          # 끄기 (사람만)
```

### `pg_demo_file.sh`: 재무 대사 예외 시연
- **장애 주입이 아닙니다.** PG 정산 파일에 PG 쪽 사건 4건을 일부러 넣어 재무에 올립니다. [33]
  1. 부분 취소가 PG에만 반영됨: −2,000원
  2. PG 정산 보류: 파일에 없음
  3. 계약과 다른 수수료 3.5%
  4. PG 금액 +1,000원
- **끝나면 `clear`로 뺍니다.** [34]

```bash
bash backend/scripts/pg_demo_file.sh          # 만들고 올리기
bash backend/scripts/pg_demo_file.sh clear    # 시연 끝나면 빼기
```

## 근거 (파일:줄)

| 번호 | 내용 | 파일:줄 |
|---|---|---|
| [1] | `SHOP_TRAFFIC=on`일 때만 가상 주문 (기본 꺼짐) | `backend/scripts/run_all.sh:418-424` |
| [2] | 가상 주문 분당 20건(3초마다 1건) | `backend/scripts/shop_traffic.py:61` |
| [3] | 판단 창 60초, 최소 표본 5건 | `backend/shared/shop-api/src/ax_shop_api/monitor.py:28`, `:30` |
| [4] | admin 토큰을 `web.env`에 씀 | `backend/scripts/run_all.sh:384` |
| [5] | `shop_fault.sh`가 `web.env`에서 토큰을 읽음, 없으면 멈춤 | `backend/scripts/shop_fault.sh:8`, `:12-14` |
| [6] | 장애 버튼 없음, failure 약 25초 뒤 사건 열림 | `backend/scripts/shop_fault.sh:3-4` |
| [7] | 기준 p95 1000ms, 5xx 비율 2% | `backend/shared/shop-api/src/ax_shop_api/monitor.py:26-27`, `:73-76` |
| [8] | latency는 2000ms 지연 | `backend/scripts/shop_fault.sh:20` |
| [9] | 지속 20초, 판단 간격 5초 (`run_all.sh` 값) | `backend/scripts/run_all.sh:330` |
| [10] | 지속 시간은 첫 기준 초과 표본부터 셈 (코드 기본값 60초) | `backend/shared/shop-api/src/ax_shop_api/monitor.py:29`, `:130-137` |
| [11] | 감시 루프 간격 | `backend/shared/shop-api/src/ax_shop_api/main.py:412` |
| [12] | 되돌리기는 진단·영향 분석 뒤 | `backend/services/orchestrator/src/ax_orchestrator/plans/scenario_a.py:546` |
| [13] | 원인 진단 단계 | `backend/services/orchestrator/src/ax_orchestrator/plans/scenario_a.py:506` |
| [14] | 원인 후보 추정 단계 (영향 분석 뒤) | `backend/services/orchestrator/src/ax_orchestrator/plans/scenario_a.py:525-528` |
| [15] | 되돌리기 단계 | `backend/services/orchestrator/src/ax_orchestrator/plans/scenario_a.py:543` |
| [16] | 승인 대기 600초 | `backend/services/orchestrator/src/ax_orchestrator/main.py:63` |
| [17] | 총괄 화면은 보기만, 마감이 지나면 자동 거절 | `frontend/src/pages/Orchestrator.tsx:138`, `:283` |
| [18] | 재무 화면의 안전 장치 보류 큐 (되돌리기는 다른 화면으로 빠지지 않음) | `frontend/src/pages/Finance.tsx:175`, `:381`, `frontend/src/lib/finance.ts:850`, `frontend/src/components/finance/SafetyQueue.tsx:106` |
| [19] | 영향 분석 단계 | `backend/services/orchestrator/src/ax_orchestrator/plans/scenario_a.py:523` |
| [20] | 점검 공지 초안·게시 | `backend/services/orchestrator/src/ax_orchestrator/plans/scenario_a.py:572-582` |
| [21] | 영향 고객 일괄 보상 | `backend/services/orchestrator/src/ax_orchestrator/plans/scenario_a.py:587-590` |
| [22] | 복구 확인 (되돌리기·과거 처리 다시 하기 뒤) | `backend/services/orchestrator/src/ax_orchestrator/plans/scenario_a.py:598-601` |
| [23] | 복구 확인 최대 5번, 30초 간격 | `backend/domains/infra-dejafix/src/infra_agent/handlers.py:419`, `:423` |
| [24] | 정상화 공지, 배너 내리기 | `backend/services/orchestrator/src/ax_orchestrator/plans/scenario_a.py:609-625` |
| [25] | 재무 기록, 처리 시간 요약, 사례 기록 | `backend/services/orchestrator/src/ax_orchestrator/plans/scenario_a.py:638-656` |
| [26] | 복구 확인 실패 시 정상화 공지 없이 이관 | `backend/services/orchestrator/src/ax_orchestrator/plans/scenario_a.py:1-14` (맨 위 설명) |
| [27] | 사람이 해제해 풀린 사건은 처리 방식 `release_fault` | `backend/services/orchestrator/src/ax_orchestrator/plans/scenario_a.py:440-447` |
| [28] | 과거 처리 다시 하기 조건 (원인 배포 없음, 유사도 0.85 이상, `release_fault`) | `backend/services/orchestrator/src/ax_orchestrator/plans/scenario_a.py:155-178` |
| [29] | 결제 서버가 이상할 때만 해제, 안전 관문 거침 | `backend/domains/infra-dejafix/agent-card.json:33-34` |
| [30] | 운영 지표 > 복구 속도, 1차·2차 비교 | `frontend/src/demo/services.ts:126`, `frontend/src/components/data/LearningKpi.tsx:57`, `:73-74`, `:83` |
| [31] | `slow_agent.ps1` Windows 로컬 전용, "확인 필요" | `backend/scripts/slow_agent.ps1:1-2` |
| [32] | `-HoldSeconds`는 30초보다 길게 | `backend/scripts/slow_agent.ps1:11` |
| [33] | PG 사건 4건 | `backend/scripts/pg_demo_file.py:10-16` |
| [34] | `clear`로 빼기 | `backend/scripts/pg_demo_file.py:17`, `:21` |

줄 번호는 이 문서를 올린 때의 dev 기준입니다.
