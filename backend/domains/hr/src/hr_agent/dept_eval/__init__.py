"""부서 평가: 장애 대응 단계의 일한 시간을 기준값과 비교해 판정한다 (시간 계산만, LLM 없음)."""

from hr_agent.dept_eval.criteria import Criteria, CriteriaError, load_criteria
from hr_agent.dept_eval.judge import judge_step, judge_timings

__all__ = ["Criteria", "CriteriaError", "judge_step", "judge_timings", "load_criteria"]
