# 다른 부서용 hr 호출 안내서

hr에게 "이 일이 있었다"고 알리는 가장 흔한 방법은 `record_case`다. 이 문서는 그 요청을 보내는 방법과 흔한 실수를 정리한다.
(전체 skill 표는 `agents/hr/README.md`, 형식 원본은 `contracts/src/ax_contracts/schemas/record.py`의 `RecordCase`.)

## 1. 언제 부르나
- 결정이 내려졌을 때: 기획 확정, 롤백 실행, PR 머지, 요청 종료, **기각한 안**
- 장애·사례를 남길 때: `incident`, `case`, `notice`
- 부르는 쪽이 판단하지 않는다. 결정인지 아닌지, 확신이 높은지는 hr이 정한다. 사실(요약·이유·대안·근거)만 정확히 보낸다.

## 2. 요청 예시 (결정 + 기각된 대안)
`POST http://hr:8104/a2a`

> 이 주소는 인사 서버를 따로 띄울 때(포트 8104) 기준이다. `bash scripts/run_all.sh`로 서비스 전체를 띄우면 인사 결정 기록 서비스(`decisions`)는 **8114**이고 8104는 인수인계(`handover`) 서비스다. 어느 쪽인지 먼저 확인하고 부른다.

```json
{
  "meta": {
    "context_id": "ctx_inc_0001",
    "token": "<에이전트 토큰>",
    "path": ["orchestrator"],
    "deadline": "2026-10-23T01:00:30Z",
    "idempotency_key": "ctx_inc_0001:record_case:pr_412"
  },
  "skill": "record_case",
  "input": {
    "context_id": "ctx_inc_0001",
    "kind": "spec_confirmed",
    "summary": "결제 재시도를 최대 2회로 확정했다",
    "decision_reason": "중복 결제 위험을 줄이기 위해",
    "alternatives_considered": ["재시도 없음", "무제한 재시도"],
    "source_refs": ["pr:412", "area:payment"],
    "author": "planning"
  }
}
```

응답(요약):

```json
{
  "status": "completed",
  "outcome": "card_created",
  "output": {"outcome": "card_created", "card_id": "card_ab12cd34ef56", "confidence": "high"},
  "decision_reason": "중복 결제 위험을 줄이기 위해",
  "alternatives_considered": ["재시도 없음", "무제한 재시도"],
  "evidence": {"ref_ids": ["pr:412", "area:payment", "card_ab12cd34ef56"]}
}
```

`outcome` 값:

| 값 | 뜻 |
|---|---|
| `card_created` | 확신이 높아 카드가 바로 확정됨 |
| `candidate` | 근거가 부족해 후보 카드로 보류됨 (이유·대안·근거를 더 채워 다시 보내면 좋다) |
| `record_only` | 결정이 없는 기록이라 카드 없이 기록만 남김 |
| `duplicate` | 같은 기록이 이미 처리됨 (정상, 다시 처리하지 않는다) |
| `gate_held` 등 | 안전 레이어가 막음. 재시도하지 말고 사람 승인을 기다린다. 보류는 Task 상태가 `input-required`(`human_input`)로 온다 |
| `failed` / `UPSTREAM_UNAVAILABLE` | memory 같은 다른 서비스가 응답하지 않음. 상태는 바뀌지 않았으니 같은 요청을 다시 보내도 된다 |

## 3. 꼭 지킬 것
1. **`idempotency_key`는 필수**다. `context_id + 액션 + 대상` 형태로, 같은 이벤트면 항상 같은 값을 쓴다. 다르면 같은 기록이 카드 두 장이 된다. 반대로 **다른 이벤트에 같은 키를 쓰지 않는다**(안전 레이어가 거부한다).
2. **영역은 `source_refs`에 `area:<영역>`으로** 넣는다(`payment`, `search`, `catalog`, `support-desk`, `infra-ops`, `inventory`). 없으면 영역별 검색·인수인계·계획 비교에서 이 카드를 찾지 못한다.
3. **이유와 대안을 비우지 않는다.** 이유·대안·근거가 모두 있어야 확신도가 high다. 모르면 비워 두면 되고(지어내지 않는다) 그 카드는 후보가 된다.
4. **외부 문구(고객 메시지, 메일 본문 등)는 `untrusted`에** 넣는다. `summary`나 `decision_reason`에 그대로 붙이지 않는다. hr은 `untrusted`를 지시로 읽지 않는다.
5. **`path`를 이어 붙인다.** 받은 `path`에 자기 이름을 더해 보낸다. hr 이름이 이미 있으면 루프로 거부된다.
6. **`deadline`은 30초 안**으로 둔다. 넘기면 처리하지 않고 `timeout`을 돌려준다.

## 4. 흔한 실수

| 증상 | 원인 | 고치기 |
|---|---|---|
| `rejected` / `FORBIDDEN_SKILL` | 요청자가 Agent Card의 `allowed_requesters`에 없음 | 토큰의 에이전트 이름 확인. `record_case`는 orchestrator·planning·support·marketing·finance·data·infra만 가능. 인수인계·발령·퇴직 같은 사람 변화 skill은 orchestrator만 부른다 |
| `rejected` / `INVALID_INPUT` | 입력이 `RecordCase` 형식과 다름, `idempotency_key` 없음 | 필드 이름과 `kind` 값(`spec_confirmed` 등 10종) 확인 |
| 카드가 계속 `candidate` | 이유·대안·근거 중 둘 이상이 비어 있음 | 사실만큼은 채워서 보낸다 |
| 같은 결정이 카드 두 장 | `idempotency_key`가 매번 달라짐 | 대상(PR 번호 등)으로 키를 고정 |
| 기각한 안을 보냈는데 계획 비교에 안 걸림 | `kind`가 `decision_rejected`가 아니거나 `area:` 누락 | 둘 다 확인 |
| 보내고 아무 변화 없음 | `outcome`이 `record_only` | 결정 문장(`decision_reason` 또는 `summary`)이 비어 있지 않은지 확인 |

## 5. 조회용 skill (상태를 바꾸지 않는다)
- `get_roster`: 영역별 담당·교대와 공백 영역. 입력 `{"area": "payment"}`(생략하면 전체). 개인 정보는 없고 영역·역할·담당자 ID만 나온다.
- `check_plan`: 새 계획 초안이 과거 기각·확정 결정과 충돌하는지. 입력 `{"draft": {"title", "summary", "area", "kind": "spec|ticket|deploy_plan"}}`.
  결과의 `verdict`가 `warn`이면 `findings[].card_ids`가 근거 카드다. 관련 카드가 없으면 `no_related_cards: true`(= "기록에 없음")이며 판정하지 않는다.

## 6. 직접 불러 보기
```bash
uv run ax-test-call --agent http://localhost:8104 --skill record_case --input input.json --as planning
```
눈으로 확인하려면 데모(`uv run --package ax-agent-hr python -m hr_agent.demo` → http://localhost:8114)의 **카드 덱** 탭에서 같은 입력을 보낼 수 있다.

## 7. 기획 AI 카드와 시연 카드 구분
- 기획 AI가 보낸 카드는 작성자(`author`)가 `planning`이다. 화면에 **기획 AI** 표식이 붙는다.
- 시연 주입 카드도 기획 AI 신원으로 들어가므로 작성자로는 구분되지 않는다. 이유 칸에 `시연 주입`이 들어가 있어 그 표식으로 구분하고, 화면에 **시연 주입** 표식이 붙는다.
- 실제 결정을 보낼 때는 이유 칸에 `시연 주입`이라는 말을 쓰지 않는다. 그 말이 있으면 시연 카드로 보인다.
- 시연 기록은 실제 결정이 아니므로 시연이 끝나면 화면에서 구분해 보고, 실제 결정 집계에서 빼서 본다.

## 8. 실제 모드 시연 순서 (기획 AI ↔ 인사 AI)
실제 모드(`bash scripts/run_all.sh`, 화면은 `bash scripts/run_web.sh` → http://localhost:3090)에서 아래 순서로 보여 준다. 목 모드는 쓰지 않는다. **사람**이라고 적은 단계는 사람이 화면에서 직접 누른다.

| 장면 | 하는 일 | 어디서 무엇을 보나 |
|---|---|---|
| A 확정 | 결제 영역 요청 1건을 넣는다. 기획서가 자동 확정되면 인사에 후보 카드가 생긴다. **사람**이 "기록으로 확정"을 누른다 | `/ops` 맨 위 "지금 사람이 할 일"에 후보 카드가 보이고, 누르면 "업무 결정 카드"에 칩 확정·기획 AI로 올라온다. 자동 머지가 같이 일어나면 `[PR 머지]` 후보도 생긴다 |
| B 재전송 | 같은 `record_case`를 같은 키로 다시 보낸다 | 응답이 `duplicate`이고 카드 수는 그대로다 |
| C 기록에 없음 | 기각 카드와 겹치지 않는 초안으로 `check_plan`을 부른다 | `ok`, `no_related_cards: true`. `/ops` 맨 아래 "계획 비교"로도 볼 수 있다 |
| D 기각 | 안 하나를 **이유와 대안을 적어** 기각한다 | 기각 카드가 확신 높음으로 바로 확정되고, 제목에 "[기각한 안]"이 한 번만 있다. 이유가 비면 인사로 보내지 않는다 |
| E 겹침 경고 | D에서 기각한 안과 겹치는 요청을 넣는다. **사람**이 기획서 화면에서 "승인"을 누른다 | 기획서 화면에 주황 경고("이미 기각된 안과 비슷하다")와 노란 띠(승인·거절)가 뜨고 자동 확정이 멈춘다. 승인하면 `[기획 확정] … (warn 무시 진행 …)`, `[PR 머지]`, `[요청 종료]` 후보 카드가 생긴다 |
| F 거절 | 겹치는 요청을 하나 더 넣고 **사람**이 "거절"을 누른다. **이유는 필수**다 | 인사에 `[기각한 안]` 카드가 생기고, 기획서 화면 결정 줄에 "인사 기록됨" 칩이 붙는다 |

- **요청 종료 카드**는 요청의 작업(PR)이 **모두** 머지돼야 생긴다. 작업이 둘이면 남은 PR을 **사람**이 개발 화면(`/dev/work/prs`)에서 머지한다.
- PR이 보호 경로를 건드리면 PR 열기가 보류된다. 이때는 개발 화면에서 "승인하고 PR 열기"를 누른다.
- **영역 태그**(`area:payment` 등)는 카드 화면에 나오지 않는다. 로컬 실제 모드에서는 `backend/.data/hr.db`의 `mock_cards`에서 카드 `tags`로 확인한다. `check_plan`은 영역이 같은 카드끼리만 비교하므로 기각 카드와 `pr_merged`·`request_closed` 카드에도 영역이 있어야 한다.
- 인사 명부에는 영역 6개(payment, search, catalog, support-desk, infra-ops, inventory)가 조직도에서 들어가 있고 담당자는 넣지 않는다. 그래서 담당자 칸은 비어 보인다.

### 알려진 점 (2026-11-02 기준)
- 기획서 거절(`reject_spec`) 뒤에도 요청 상태가 "기획 중"으로 남고 "기각"으로 바뀌지 않는다(기획 쪽에서 고칠 예정).
- 사람이 확정한 기획 카드의 이유가 "확인할 수 없음"으로 들어간다(기획 쪽에서 고칠 예정).
- 기획 카드의 결정 문장에 붙는 고정 문구(`SPEC-n …확정`, `자동 확정 (사후 감사 대상)`) 때문에 같은 결정을 LLM이 "내용이 바뀜"으로 판정할 수 있다. 문구를 뺀 문장으로 다시 판정하면 12쌍 모두 "같은 결정"이었다(합성 시험, 처리 방식은 협의 중).
- 임베딩 설정(`EMBED_MODEL`)이 없으면 인사의 비교와 병합은 글자 겹침(Dice)으로 대체된다.
- 초기화는 인사 카드(결정·사례)만 지운다. 시연 전에 `backend/.data/hr.db`를 복사해 두면 되돌릴 수 있다.
