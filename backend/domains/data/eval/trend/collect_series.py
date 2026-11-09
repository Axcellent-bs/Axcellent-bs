"""trend_scan 평가용 실제 시계열 받기 (사람이 한 번만 돌린다. 평가·케이스 생성은 부르지 않는다).

    cd backend
    uv run python domains/data/eval/trend/collect_series.py

- 네이버 검색어 트렌드(`connectors.naver_datalab`)를 부른다. 키는 `backend/.env`의
  `NAVER_HUB_CLIENT_ID`·`NAVER_HUB_CLIENT_SECRET`이고 출력하지 않는다.
- 원본 응답은 저장하지 않는다(약관상 재배포 문제). **날짜와 값만** `series/<이름>.json`에 남긴다.
- 키워드마다 따로 부른다(그 키워드 최댓값 = 100). `joint_*`만 여러 그룹을 한 요청으로 받는다
  (그룹 사이 배수가 실제 응답 그대로 남게).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from ax_contracts.schemas.data import TrendScanInput
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_datalab import SOURCE, NaverDatalabConnector

HERE = Path(__file__).resolve().parent
SERIES = HERE / "series"
ENV_FILE = HERE.parents[3] / ".env"  # backend/.env
START = date(2025, 8, 1)
END = date(2026, 11, 1)

# 파일 이름 → 키워드. 후보만 적고, 잔잔한지는 make_cases.py가 정해진 기준으로 고른다
SINGLE: dict[str, str] = {
    # 잔잔한 키워드 후보 (생활용품·일상어)
    "calm_toothpaste": "치약",
    "calm_toothbrush": "칫솔",
    "calm_ballpen": "볼펜",
    "calm_wetwipes": "물티슈",
    "calm_towel": "수건",
    "calm_shampoo": "샴푸",
    "calm_detergent": "세제",
    "calm_notebook": "공책",
    "calm_library": "도서관",
    "calm_catfood": "고양이 사료",
    "calm_bookmark": "책갈피",
    "calm_tissue": "휴지",
    # 계절로만 움직이는 키워드
    "seasonal_padding": "패딩",
    "seasonal_aircon": "에어컨",
    "seasonal_sunscreen": "선크림",
    # 실제 사례 (날짜를 외부에서 아는 사건)
    "event_suneung": "수능",
    "event_songpyeon": "송편",
    "event_tteokguk": "떡국",
    # 검색량이 적은 키워드 후보
    "sparse_catbooks": "캣북스",
    "sparse_cat_bookend": "고양이 북엔드",
    "sparse_cat_bookstand": "고양이 독서대",
}
JOINT: dict[str, list[str]] = {"joint_toothpaste_ballpen_towel": ["치약", "볼펜", "수건"]}


def _env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


async def _fetch(connector: NaverDatalabConnector, names: list[str]) -> dict:
    query = TrendScanInput.model_validate(
        {
            "keyword_groups": [{"group_name": n, "keywords": [n]} for n in names],
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
            "time_unit": "date",
        }
    )
    response = await connector.search_trend(query)
    return {
        "source": SOURCE,
        "fetched": date.today().isoformat(),
        "time_unit": "date",
        "start_date": START.isoformat(),
        "end_date": END.isoformat(),
        "groups": {
            s.group_name: {
                "keywords": list(s.keywords),
                "points": [[d.isoformat(), v] for d, v in s.points],
            }
            for s in response.series
        },
    }


async def main() -> None:
    connector = NaverDatalabConnector(_env())
    SERIES.mkdir(exist_ok=True)
    jobs = {name: [keyword] for name, keyword in SINGLE.items()} | JOINT
    for name, names in jobs.items():
        try:
            data = await _fetch(connector, names)
        except ConnectorUnavailable as error:
            print(f"{name}: 실패 {error.code} {error}")
            sys.exit(2)
        path = SERIES / f"{name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        sizes = {g: len(v["points"]) for g, v in data["groups"].items()}
        print(f"{name}: {sizes}")


if __name__ == "__main__":
    asyncio.run(main())
