# qa_findings 기준값 시험 절차 (실제 문의 100~200건)

> 상태: 절차 메모. 기준값(겹침 10%, 수정 정도 0.5, 정밀도 80%, 규칙당 10건)은 모두 **시드·제안**이고
> 이 시험으로 정한다. 도구는 `python -m support_agent.qa_eval` (`backend/domains/support`).
> 설계: `2026-10-28-knowledge-gap-qa-design.md` 9장.

## 1. 서버에서 내보내기 (한 번, 읽기만 한다)
```bash
cd backend && SUPPORT_PG_DSN=... uv run python -m support_agent.qa_eval export --out ../qa-export.json --days 14
```
- 서비스가 쓰는 DB를 가리켜야 한다(`SUPPORT_PG_DSN`). 메모리 저장소면 서비스를 재시작하는 순간 문의와 초안 기록이 사라지므로 내보낼 것이 없다.
- `[시험]` 제목 문의와 원문을 지운 문의는 뺀다(`--include-test`로 넣을 수 있다).
- 고객 이름·제목·초안 본문은 빼고 글은 개인정보 마스킹을 거친다. 그래도 고객 글이 남아 있으니 **레포 밖에 두고 커밋하지 않으며**, 보는 사람은 상담 담당으로 제한한다.

## 2. 표 보기
```bash
uv run python -m support_agent.qa_eval report --in ../qa-export.json --out ../qa-report.md
```
- 규칙별 적중 건수와 `weak_grounding`·`heavy_edit` 값의 분포가 나온다. 기준값 바로 위아래 구간에 건수가 몰려 있으면 값이 조금만 바뀌어도 결과가 달라진다.
- 문의가 100건보다 적으면 표 머리에 경고가 붙는다. 기간을 늘리거나 시드 대화와 합치고, 어느 쪽인지 결과 메모에 적는다.

## 3. 시트 만들어 사람이 판정하기
```bash
uv run python -m support_agent.qa_eval sheet --in ../qa-export.json --out-dir ../qa-sheet
```
- `sheet.csv`(문의 번호와 빈 `verdict` 칸)와 `key.csv`(정답표)가 나온다. 시트에는 규칙이 걸린 문의와 대조군이 **어느 쪽인지 알 수 없게 섞여** 있고 고객 글은 없다.
- 담당자가 상담 콘솔에서 그 문의(와 메시지 번호)를 열어 **답변이 진짜 문제인지** `y`, 아니면 `n`을 적는다. 판정은 사람이 하고 도구는 지어내지 않는다. `key.csv`는 판정이 끝날 때까지 보지 않는다.

## 4. 점수 보기
```bash
uv run python -m support_agent.qa_eval score --sheet ../qa-sheet/sheet.csv --key ../qa-sheet/key.csv
```
- 규칙별 정밀도(걸린 것 중 진짜 문제 비율)와 대조군 누락률(규칙이 놓친 정도)이 나온다.
- 권고: 정밀도 80% 이상이고 규칙당 10건 이상 봤으면 `info` 규칙을 `flag`로 올릴 수 있다. 낮으면 기준값을 조정하거나 `info`로 둔다. 이 선은 제안이며 팀이 정한다.

## 5. 결과 반영
- 표와 점수 결과를 이 폴더에 날짜 메모로 남기고(고객 글 없이), 기준값·심각도를 바꾸는 PR을 따로 올린다.
