from __future__ import annotations

import inspect
from typing import get_type_hints

from app.candidate_research import CandidateResearchPort, CandidateResearchService
from app.baidu_map import BaiduMapAdapter
from app.llm_runtime import DeepSeekRuntime
from app.workflow_contracts import ArtifactRepository, ExecutionBudgetManager


def test_service_implements_the_public_port_shape() -> None:
    for name in ("research_initial", "research_supplemental", "close_run", "aclose"):
        assert callable(getattr(CandidateResearchService, name, None))
    assert inspect.iscoroutinefunction(CandidateResearchService.research_initial)
    assert inspect.iscoroutinefunction(CandidateResearchService.research_supplemental)
    assert inspect.iscoroutinefunction(CandidateResearchService.close_run)
    assert inspect.iscoroutinefunction(CandidateResearchService.aclose)
    assert CandidateResearchPort is not None


def test_service_constructor_exposes_only_public_dependency_hints() -> None:
    hints = get_type_hints(CandidateResearchService.__init__)
    assert hints["runtime"] is DeepSeekRuntime
    assert hints["map_adapter"] is BaiduMapAdapter
    assert hints["artifact_repository"] is ArtifactRepository
    assert hints["budget_manager"] is ExecutionBudgetManager
