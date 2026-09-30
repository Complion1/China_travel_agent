import asyncio
import pytest
from mcp.types import CallToolResult, TextContent
from app.baidu_map import DirectionsNoResult, DirectionsRequest, TravelMode, RouteEndpoint, BaiduMapProtocolError, BaiduMapSessionError
from app.baidu_map._response_parsing import parse_transit_no_path_error
from tests.unit.baidu_map.test_gateway_lifecycle import create_gateway, install_success_handlers, POINT, FixedClock
from tests.support.baidu_map_fake_mcp import FakeMcpEnvironment

TEXT = 'API response error: no bus path'
def error(text=TEXT, **kwargs):
    return CallToolResult(isError=True, content=[TextContent(type='text', text=text)], **kwargs)
def request(mode=TravelMode.TRANSIT):
    return DirectionsRequest(mode=mode, origin=RouteEndpoint(coordinate=POINT), destination=RouteEndpoint(coordinate=POINT))

@pytest.mark.asyncio
async def test_no_path_does_not_break_session_or_other_concurrent_calls():
    environment = FakeMcpEnvironment()
    install_success_handlers(environment)
    original = environment.handlers['map_directions']
    async def directions(arguments):
        if arguments['model']=='transit':
            return error()
        return original(arguments)
    environment.set_handler('map_directions', directions)
    gateway = await create_gateway(environment)
    try:
        first, second = await asyncio.gather(gateway.plan_directions(request()), gateway.plan_directions(request(TravelMode.WALKING)))
        assert isinstance(first, DirectionsNoResult)
        assert second.status.value == 'success'
        assert (await gateway.plan_directions(request(TravelMode.WALKING))).status.value == 'success'
        assert environment.call_count == 3
    finally:
        await gateway.aclose()

@pytest.mark.parametrize('response,mode', [
    (error('API response error: no bus path '), TravelMode.TRANSIT),
    (error('API response error: quota exceeded'), TravelMode.TRANSIT),
    (error(), TravelMode.WALKING),
    (error(structuredContent={'status': 0}), TravelMode.TRANSIT),
    (CallToolResult(isError=True,content=[TextContent(type='text',text=TEXT)]*2),TravelMode.TRANSIT),
    (CallToolResult(isError=False,content=[TextContent(type='text',text=TEXT)]),TravelMode.TRANSIT),
])
def test_only_exact_qualified_envelope_is_accepted(response,mode):
    assert parse_transit_no_path_error(response,request(mode),FixedClock().now()) is None

@pytest.mark.asyncio
async def test_unknown_error_still_breaks_session():
    environment=FakeMcpEnvironment()
    environment.set_result('map_directions',error('unknown failure'))
    gateway=await create_gateway(environment)
    try:
        with pytest.raises(BaiduMapProtocolError): await gateway.plan_directions(request())
        with pytest.raises(BaiduMapSessionError): await gateway.plan_directions(request())
        assert environment.call_count==1
    finally: await gateway.aclose()