import inspect
import subprocess
import sys
import app.web_application as web

BASE_EXPORTS = [
    "WebApplicationError", "WebApplicationConfigurationError", "WebApplicationDependencyError",
    "WebApplicationUsageError", "WebApplicationConflictError", "WebApplicationCapacityError",
    "WebApplicationSettings",
    "WebStartRequest", "WebClarifyRequest", "WebConfirmRequest", "WebHotelRequest", "WebControlRequest",
    "WebVisitorContext", "WebSessionView", "WebAdmissionView", "WebCommandReceipt", "WebFact",
    "WebHotelChoice", "WebClarificationOption", "WebClarificationQuestion", "WebInteractionCard", "WebRunView", "WebHistoryPage", "WebResultDay", "WebResultView",
    "WebSummaryView", "WebSampleView", "WebSamplesView", "WebApiError", "WebInputIssue",
    "WebMetadataStore", "WebApplicationService", "WebApplicationDependencies", "RunBoundExtractionInvocationSink",
    "create_web_application",
]
def test_exact_public_contract_and_safe_markers():
    assert web.__all__ == BASE_EXPORTS
    for name in BASE_EXPORTS[:6]:
        error=getattr(web,name)()
        assert str(error)==name and error.args==()
        assert not inspect.signature(getattr(web,name)).parameters
    for name in ["AsyncConnectionPool","AESGCM","load_pause_key","DecisionError","SummaryReceipt","production_service"]:
        assert not hasattr(web,name)

def test_import_has_no_environment_or_resource_requirement():
    result=subprocess.run([sys.executable,"-c","import app.web_application; print('import-ok')"],
        env={"PATH":"/usr/local/bin:/usr/bin:/bin","PYTHONPATH":"."},capture_output=True,text=True,check=True)
    assert result.stdout.strip()=="import-ok"
