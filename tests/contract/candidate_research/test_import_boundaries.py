import ast
from pathlib import Path

import app.candidate_research as root


def test_boundary_does_not_export_private_runtime_modules():
    assert "CandidateResearchService" in root.__all__
    assert hasattr(root, "CandidateResearchService")
    for name in ("prompting", "fingerprints", "judgments", "CandidateResearchGap", "CandidateCore", "RoleAssessment"):
        assert name not in root.__all__
        if name not in {"prompting", "fingerprints", "judgments"}:
            assert not hasattr(root, name)
    for private_state in ("_Run", "_flights", "_locks", "_runtime", "_map_adapter", "_repository", "_budget"):
        assert private_state not in root.__all__
        assert not hasattr(root, private_state)


def test_protocol_and_fake_have_no_provider_or_type_escape():
    protocol = Path("app/candidate_research/protocols.py").read_text(encoding="utf-8")
    fake = Path("tests/support/candidate_research_fakes.py").read_text(encoding="utf-8")
    combined = protocol + fake
    assert "DeepSeekRuntime" not in combined
    assert "BaiduMapAdapter" not in combined
    assert "ArtifactRepository" not in combined
    assert "ExecutionBudgetManager" not in combined
    assert "CandidateResearchConstructor" not in combined
    tree = ast.parse(combined)
    annotations = [node.annotation for node in ast.walk(tree) if isinstance(node, ast.AnnAssign) and node.annotation is not None]
    annotations.extend(node.returns for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None)
    assert all(not (isinstance(node, ast.Name) and node.id in {"Any", "object"}) for node in annotations)


def test_candidate_research_source_is_whitelist_shaped():
    forbidden = ("import requests", "httpx", "aiohttp", "from app.baidu_map._", "from app.llm_runtime.runtime import")
    for path in Path("app/candidate_research").rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        assert not any(token in text for token in forbidden), path


def test_service_has_no_type_or_payload_escape():
    text = Path("app/candidate_research/service.py").read_text(encoding="utf-8")
    lowered = text.lower()
    for token in ("typing.any", "type: ignore", "# noqa", "cast(", "raw_response"):
        assert token not in lowered
