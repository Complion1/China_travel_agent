"""Reviewable public imports, command construction and resource ownership contract."""
import ast
from pathlib import Path
from inspect import signature
from app.web_application import WebMetadataStore,WebApplicationService,WebApplicationDependencies,RunBoundExtractionInvocationSink
from app.web_application import assembly, service


def test_public_store_and_service_signatures_do_not_accept_arbitrary_state_sql_or_token():
    assert tuple(signature(WebMetadataStore).parameters) == ("settings","pool","key","clock")
    expected={"bootstrap_session":("cookie",),"get_admission":("owner","client_ip"),
        "get_request":("owner","request_id"),"start":("owner","request","client_ip"),
        "resume":("owner","run_id","request"),"cancel":("owner","run_id","request"),
        "recover":("owner","run_id","request"),"get_run":("owner","run_id"),
        "list_runs":("owner","cursor"),"read_result":("owner","run_id"),"read_summary":("owner","run_id"),
        "get_samples":(),"close":()}
    for name,parameters in expected.items():
        sig=signature(getattr(WebApplicationService,name))
        assert tuple(sig.parameters)[1:] == parameters
        assert all(parameter.kind is parameter.KEYWORD_ONLY for key,parameter in sig.parameters.items() if key != "self")


def test_cross_module_production_imports_use_package_roots_with_only_approved_provider_exception():
    for filename in ("assembly.py","service.py","presentation.py","summary.py","store.py"):
        parsed=ast.parse(Path(service.__file__).with_name(filename).read_text(encoding="utf-8"))
        for node in ast.walk(parsed):
            if isinstance(node,ast.ImportFrom) and node.module and node.module.startswith("app."):
                assert len(node.module.split(".")) == 2 or (filename == "assembly.py" and node.module == "app.llm_runtime.provider" and [alias.name for alias in node.names] == ["LangChainDeepSeekAdapter"])
            if isinstance(node,ast.Attribute) and node.attr in {"_histories","_interaction_scopes","_pool","_coordination_pool","_checkpoint_pool","_gateway","_state"}:
                assert filename == "store.py" and node.attr == "_pool"
    source=Path(assembly.__file__).read_text(encoding="utf-8")
    assert "enter_async_context(create_baidu_map_runtime" in source
    assert "interaction_fallback_dir=None" in source and "TEST_MODE" not in source
    assert "create_demo_workflow_definition" in source and "StateGraph(" not in source
