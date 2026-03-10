"""Tests for CloudDispatcher - Retry, Backoff, Fallback, Circuit Breaker"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from clawgate.backends.cloud.dispatcher import (
    BackendHealth,
    CircuitState,
    CloudDispatcher,
    _is_retryable,
)
from clawgate.engines.base import GenerationRequest, GenerationResponse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_BACKOFF_MAX = 8.0
FAILURE_THRESHOLD = 3
RECOVERY_TIMEOUT = 60.0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_request():
    """Create a minimal GenerationRequest."""
    return GenerationRequest(messages=[{"role": "user", "content": "hello"}])


@pytest.fixture
def mock_response():
    """Create a minimal GenerationResponse."""
    return GenerationResponse(
        content="ok",
        model="test-model",
        input_tokens=5,
        output_tokens=3,
        total_time=0.1,
    )


def _make_http_error(status_code: int) -> httpx.HTTPStatusError:
    """Helper: build an httpx.HTTPStatusError with a given status code."""
    resp = httpx.Response(status_code=status_code, request=httpx.Request("POST", "http://test"))
    return httpx.HTTPStatusError(str(status_code), request=resp.request, response=resp)


# ---------------------------------------------------------------------------
# 1. Model resolution
# ---------------------------------------------------------------------------
class TestModelResolution:
    def test_deepseek_v3(self):
        """deepseek-v3 should resolve to (deepseek, deepseek-chat)."""
        d = CloudDispatcher(backends={})
        assert d._resolve_model("deepseek-v3") == ("deepseek", "deepseek-chat")

    def test_glm_5(self):
        """glm-5 should resolve to (glm, glm-5)."""
        d = CloudDispatcher(backends={})
        assert d._resolve_model("glm-5") == ("glm", "glm-5")

    def test_unknown_model_defaults_to_openai(self):
        """An unrecognised model name should default to the openai backend."""
        d = CloudDispatcher(backends={})
        assert d._resolve_model("foo-bar") == ("openai", "foo-bar")


# ---------------------------------------------------------------------------
# 2. Retry with back-off
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_retry_with_backoff(mock_request, mock_response):
    """Backend fails twice (TimeoutException) then succeeds on 3rd try."""
    backend = MagicMock()
    backend.generate = AsyncMock(
        side_effect=[
            httpx.TimeoutException("t1"),
            httpx.TimeoutException("t2"),
            mock_response,
        ]
    )

    dispatcher = CloudDispatcher(
        backends={"glm": backend},
        max_retries=DEFAULT_MAX_RETRIES,
        backoff_base=DEFAULT_BACKOFF_BASE,
    )

    with patch("clawgate.backends.cloud.dispatcher.asyncio.sleep", new_callable=AsyncMock) as sleep:
        response, name = await dispatcher.dispatch(mock_request, "glm-5")

    assert response is mock_response
    assert name == "glm"
    assert backend.generate.call_count == 3
    # Exponential back-off: 1*2^0=1.0, 1*2^1=2.0
    assert sleep.call_count == 2
    sleep.assert_any_call(1.0)
    sleep.assert_any_call(2.0)


# ---------------------------------------------------------------------------
# 3. Non-retryable error (4xx) raises immediately
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_non_retryable_error_no_retry(mock_request):
    """A 400 HTTPStatusError must NOT trigger retry."""
    backend = MagicMock()
    backend.generate = AsyncMock(side_effect=_make_http_error(400))

    dispatcher = CloudDispatcher(backends={"glm": backend}, max_retries=DEFAULT_MAX_RETRIES)

    with pytest.raises(RuntimeError):
        await dispatcher.dispatch(mock_request, "glm-5")

    # Only one call -- no retry for 4xx
    assert backend.generate.call_count == 1


# ---------------------------------------------------------------------------
# 4. Fallback chain
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fallback_chain(mock_request, mock_response):
    """When glm fails permanently, deepseek should serve the request for a glm model."""
    glm = MagicMock()
    glm.generate = AsyncMock(side_effect=httpx.TimeoutException("glm down"))

    ds = MagicMock()
    ds.generate = AsyncMock(return_value=mock_response)

    dispatcher = CloudDispatcher(
        backends={"glm": glm, "deepseek": ds},
        max_retries=1,  # fast fail per backend
    )

    with patch("clawgate.backends.cloud.dispatcher.asyncio.sleep", new_callable=AsyncMock):
        response, name = await dispatcher.dispatch(mock_request, "glm-5")

    assert response is mock_response
    assert name == "deepseek"
    glm.generate.assert_called_once()
    ds.generate.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Circuit breaker opens after N consecutive failures
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_circuit_breaker_opens(mock_request):
    """After 3 consecutive failures the circuit state must be OPEN."""
    backend = MagicMock()
    backend.generate = AsyncMock(side_effect=_make_http_error(500))

    dispatcher = CloudDispatcher(backends={"glm": backend}, max_retries=1)

    with patch("clawgate.backends.cloud.dispatcher.asyncio.sleep", new_callable=AsyncMock):
        for _ in range(FAILURE_THRESHOLD):
            with pytest.raises(RuntimeError):
                await dispatcher.dispatch(mock_request, "glm-5")

    health = dispatcher._health["glm"]
    assert health.state == CircuitState.OPEN
    assert health.consecutive_failures == FAILURE_THRESHOLD


# ---------------------------------------------------------------------------
# 6. Circuit breaker transitions to HALF_OPEN after recovery timeout
# ---------------------------------------------------------------------------
def test_circuit_breaker_half_open():
    """After recovery_timeout seconds the OPEN circuit should become HALF_OPEN."""
    health = BackendHealth(
        state=CircuitState.OPEN,
        last_failure_time=1000.0,
        consecutive_failures=FAILURE_THRESHOLD,
    )

    # Before timeout: still OPEN
    with patch("clawgate.backends.cloud.dispatcher.time.time", return_value=1000.0 + RECOVERY_TIMEOUT - 1):
        assert health.is_available() is False
        assert health.state == CircuitState.OPEN

    # After timeout: transitions to HALF_OPEN
    with patch("clawgate.backends.cloud.dispatcher.time.time", return_value=1000.0 + RECOVERY_TIMEOUT + 1):
        assert health.is_available() is True
        assert health.state == CircuitState.HALF_OPEN


# ---------------------------------------------------------------------------
# 7. Streaming dispatch
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dispatch_stream(mock_request):
    """dispatch_stream should return the stream object and backend name."""
    sentinel_stream = MagicMock(name="stream")
    backend = MagicMock()
    # generate_stream is called synchronously in the dispatcher (no await)
    backend.generate_stream = MagicMock(return_value=sentinel_stream)

    dispatcher = CloudDispatcher(backends={"deepseek": backend})

    stream, name = await dispatcher.dispatch_stream(mock_request, "deepseek-v3")

    assert stream is sentinel_stream
    assert name == "deepseek"
    backend.generate_stream.assert_called_once()


# ---------------------------------------------------------------------------
# 8. Health reporting
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_health_reporting(mock_request, mock_response):
    """get_health should reflect accurate counters after mixed outcomes."""
    backend = MagicMock()
    backend.generate = AsyncMock(
        side_effect=[mock_response, mock_response, _make_http_error(500)]
    )

    dispatcher = CloudDispatcher(backends={"glm": backend}, max_retries=1)

    with patch("clawgate.backends.cloud.dispatcher.asyncio.sleep", new_callable=AsyncMock):
        await dispatcher.dispatch(mock_request, "glm-5")
        await dispatcher.dispatch(mock_request, "glm-5")
        with pytest.raises(RuntimeError):
            await dispatcher.dispatch(mock_request, "glm-5")

    report = dispatcher.get_health()["glm"]
    assert report["total_requests"] == 3
    assert report["total_successes"] == 2
    assert report["total_failures"] == 1
    assert report["success_rate"] == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# 9. All backends exhausted raises RuntimeError
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_all_backends_exhausted(mock_request):
    """RuntimeError must be raised when every backend in the chain fails."""
    glm = MagicMock()
    glm.generate = AsyncMock(side_effect=httpx.TimeoutException("glm"))

    ds = MagicMock()
    ds.generate = AsyncMock(side_effect=httpx.TimeoutException("ds"))

    dispatcher = CloudDispatcher(
        backends={"glm": glm, "deepseek": ds},
        max_retries=1,
    )

    with patch("clawgate.backends.cloud.dispatcher.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match="All backends exhausted"):
            await dispatcher.dispatch(mock_request, "glm-5")
