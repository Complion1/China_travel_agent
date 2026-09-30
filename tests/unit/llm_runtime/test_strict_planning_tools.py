import asyncio
import copy
import pytest
from langchain_openai.chat_models.base import _convert_dict_to_message
from pydantic import ValidationError
from app.llm_runtime.profiles import ITINERARY_PLANNING_PROFILE, REQUIREMENT_EXTRACTION_PROFILE
from app.llm_runtime.protocols import ProviderFailure
from app.itinerary_planning.tooling import PLANNING_TOOL_CONTRACTS
from tests.unit.llm_runtime.test_provider_adapter import make_adapter


def definitions():
    return tuple({'type':'function','function':{'name':t.name,'description':t.description,'parameters':t.args_schema.model_json_schema()}} for t in PLANNING_TOOL_CONTRACTS)


def test_actual_sdk_payload_has_strict_schema_and_beta_endpoint():
    adapter=make_adapter()
    before=definitions()
    saved=copy.deepcopy(before)
    bound=adapter._bound_tools(ITINERARY_PLANNING_PROFILE,before)
    assert '/beta' in str(bound.bound.root_async_client.base_url)
    assert all(t['function']['strict'] is True for t in bound.kwargs['tools'])
    def inspect_schema(n):
        if isinstance(n,dict):
            assert not {'$ref','$defs','minLength','maxLength','minItems','maxItems','oneOf','discriminator','default'} & n.keys()
            if n.get('type')=='object':
                assert set(n['required'])==set(n.get('properties',{}))
                assert n['additionalProperties'] is False
                assert 'schema_version' not in n.get('properties', {})
            if 'anyOf' in n:
                assert all('type' in branch for branch in n['anyOf'])
            for k,v in n.items():
                if k in ('properties','$defs'):
                    for sub in v.values():inspect_schema(sub)
                elif isinstance(v,(dict,list)):inspect_schema(v)
        elif isinstance(n,list):
            for v in n:inspect_schema(v)
    for t in bound.kwargs['tools']:inspect_schema(t['function']['parameters'])
    assert before==saved
    assert '/beta' not in str(adapter._build_model(REQUIREMENT_EXTRACTION_PROFILE).root_async_client.base_url)
    asyncio.run(adapter.aclose())


@pytest.mark.parametrize('mixed',[False,True])
def test_invalid_tool_calls_reject_entire_batch(mixed):
    tools=[{'id':'bad','type':'function','function':{'name':'submit_itinerary','arguments':'{} {}'}}]
    if mixed:tools.insert(0,{'id':'good','type':'function','function':{'name':'submit_itinerary','arguments':'{}'}})
    msg=_convert_dict_to_message({'role':'assistant','content':'','tool_calls':tools})
    adapter=make_adapter()
    assert isinstance(adapter._unwrap_ai_message(msg),ProviderFailure)


def test_wire_schema_does_not_remove_local_limits():
    from app.itinerary_planning.models import ProposedRouteLeg
    from datetime import datetime
    with pytest.raises(ValidationError):
        ProposedRouteLeg(leg_id='x'*300,origin_handle='a',destination_handle='b',start_at=datetime.fromisoformat('2026-10-23T10:00:00+08:00'),end_at=None,transport_semantic='walking',route_mode='walking',directions_handle='c',alternative_index=9)


def test_unsupported_limits_remain_visible_and_oversized_evidence_is_rejected():
    import json
    from app.llm_runtime.strict_tools import strict_tool_definitions
    from app.itinerary_planning.models import GetCandidateEvidenceArgs

    tools = strict_tool_definitions(definitions())
    evidence = next(t['function'] for t in tools if t['function']['name'] == 'get_candidate_evidence')
    handles = evidence['parameters']['properties']['candidate_handles']
    assert 'Maximum 16 items.' in handles['description']
    assert 'maxItems' not in handles
    values = [f'candidate-handle-{i:04d}' for i in range(20)]
    with pytest.raises(ValidationError) as caught:
        GetCandidateEvidenceArgs.model_validate_json(json.dumps({'candidate_handles': values}), strict=True)
    assert caught.value.errors()[0]['type'] == 'too_long'
    # Splitting preserves every requested handle without relaxing the local limit.
    batches = [GetCandidateEvidenceArgs.model_validate_json(json.dumps({'candidate_handles': values[i:i+16]}), strict=True) for i in range(0, len(values), 16)]
    assert [h for batch in batches for h in batch.candidate_handles] == values
