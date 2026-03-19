import pytest
from src.security.interceptor import SecurityInterceptor, InterceptResult


@pytest.mark.asyncio
async def test_high_risk_tool_intercepted():
    interceptor = SecurityInterceptor()
    result = await interceptor.check_tool_call("execute_code", {"code": "print('hi')"}, "test-agent")

    assert result.intercepted is True
    assert result.severity == "HIGH"
    assert "high-risk" in result.reason


@pytest.mark.asyncio
async def test_high_risk_write_file_intercepted():
    interceptor = SecurityInterceptor()
    result = await interceptor.check_tool_call("write_file", {"path": "/tmp/x"}, "test-agent")

    assert result.intercepted is True
    assert result.severity == "HIGH"


@pytest.mark.asyncio
async def test_low_risk_tool_passes():
    interceptor = SecurityInterceptor()
    result = await interceptor.check_tool_call("search_knowledge", {"query": "hello"}, "test-agent")

    assert result.intercepted is False
    assert result.severity == "LOW"


@pytest.mark.asyncio
async def test_sensitive_keywords_detected():
    interceptor = SecurityInterceptor()
    result = await interceptor.check_tool_call(
        "call_api",
        {"url": "https://example.com", "headers": {"Authorization": "api_key=abc123"}},
        "test-agent",
    )

    assert result.intercepted is True
    assert result.severity == "CRITICAL"
    assert "api_key" in result.reason


@pytest.mark.asyncio
async def test_external_tool_without_sensitive_data():
    interceptor = SecurityInterceptor()
    result = await interceptor.check_tool_call(
        "send_notification",
        {"message": "Build succeeded", "channel": "general"},
        "test-agent",
    )

    assert result.intercepted is True
    assert result.severity == "MEDIUM"
    assert "external" in result.reason


@pytest.mark.asyncio
async def test_sensitive_pattern_in_regular_tool():
    interceptor = SecurityInterceptor()
    result = await interceptor.check_tool_call(
        "search_knowledge",
        {"query": "find my password reset"},
        "test-agent",
    )

    assert result.intercepted is True
    assert result.severity == "HIGH"
    assert "password" in result.reason


@pytest.mark.asyncio
async def test_openai_key_pattern_detected():
    interceptor = SecurityInterceptor()
    result = await interceptor.check_tool_call(
        "call_api",
        {"body": "key is sk-abc123def"},
        "test-agent",
    )

    assert result.intercepted is True
    assert result.severity == "CRITICAL"


@pytest.mark.asyncio
async def test_github_token_pattern_detected():
    interceptor = SecurityInterceptor()
    result = await interceptor.check_tool_call(
        "search_knowledge",
        {"query": "ghp_abc123tokenvalue"},
        "test-agent",
    )

    assert result.intercepted is True
    assert result.severity == "HIGH"


@pytest.mark.asyncio
async def test_disabled_interceptor_passes_everything():
    interceptor = SecurityInterceptor(enabled=False)
    result = await interceptor.check_tool_call("execute_code", {"code": "rm -rf /"}, "test-agent")

    assert result.intercepted is False
