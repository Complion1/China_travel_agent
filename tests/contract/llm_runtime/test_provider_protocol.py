"""Contract test: ProviderAdapter protocol conformance"""
from app.llm_runtime.protocols import ProviderAdapter, ProviderJSONRequest, ProviderToolRequest
from app.llm_runtime.provider import LangChainDeepSeekAdapter
from app.llm_runtime.settings import DeepSeekSettings
from pydantic import SecretStr


def test_provider_has_required_methods():
    """Protocol compliance test — verify LangChainDeepSeekAdapter has all required methods."""
    methods = [
        "invoke_json", "ainvoke_json", "invoke_tools", "ainvoke_tools",
        "invoke_tool_continuation", "ainvoke_tool_continuation",
        "list_models", "alist_models", "close", "aclose",
    ]
    for m in methods:
        assert hasattr(LangChainDeepSeekAdapter, m), f"Missing method: {m}"
