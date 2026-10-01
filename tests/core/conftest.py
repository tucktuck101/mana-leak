"""Shared fixtures for `tests/core` (M2 WP3, plan F7).

`span_capture` gives a test a *real* Langfuse client whose spans go to an
in-memory OpenTelemetry exporter instead of the network, monkeypatched over
`tracing._get_client`, so parent/child linkage, trace IDs, and span
attributes can be asserted without a Langfuse server.

Each client gets a unique `public_key`: the SDK keys its internal resource
manager (client, tracer provider, exporter) by `public_key` and silently
hands a second `Langfuse(public_key="pk-test", ...)` the *first* client's
exporter -- verified: the second test's exporter then sees 0 spans, which
makes span assertions pass vacuously.

WP4/WP5 import these fixtures rather than inventing a second way to capture
spans; nothing here overlaps the root `tests/conftest.py` (database).
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from uuid import uuid4

import pytest
from langfuse import Langfuse
from mana_leak_core import tracing
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

#: `Settings.langfuse_host`'s own default. Hardcoded rather than read from
#: `get_settings()` so an ambient `LANGFUSE_HOST` cannot reach these tests;
#: nothing is ever sent there anyway, since spans go to the in-memory
#: exporter.
DEFAULT_BASE_URL = "http://localhost:3001"


@dataclass
class SpanCapture:
    """The client `tracing.py` will use, plus the spans it has finished."""

    client: Langfuse
    exporter: InMemorySpanExporter

    def finished_spans(self) -> list[ReadableSpan]:
        self.client.flush()
        return list(self.exporter.get_finished_spans())

    def span_named(self, name: str) -> ReadableSpan:
        matches = [span for span in self.finished_spans() if span.name == name]
        assert len(matches) == 1, f"expected exactly one span named {name!r}, got {len(matches)}"
        return matches[0]


@pytest.fixture(autouse=True)
def _reset_tracing_cache() -> Iterator[None]:
    """`tracing`'s client is `@lru_cache`d and its health state is module
    level, exactly like `get_settings()`; reset both around every test so
    one test's Langfuse configuration never leaks into the next."""
    tracing._get_client.cache_clear()
    tracing._health = None
    yield
    tracing._get_client.cache_clear()
    tracing._health = None


@pytest.fixture
def span_capture_factory(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., SpanCapture]]:
    clients: list[Langfuse] = []

    def _build(base_url: str | None = None) -> SpanCapture:
        exporter = InMemorySpanExporter()
        client = Langfuse(
            public_key=f"pk-test-{uuid4().hex}",
            secret_key=f"sk-test-{uuid4().hex}",
            base_url=base_url or DEFAULT_BASE_URL,
            span_exporter=exporter,
        )
        clients.append(client)
        monkeypatch.setattr(tracing, "_get_client", lambda: client)
        return SpanCapture(client=client, exporter=exporter)

    yield _build
    for client in clients:
        client.shutdown()


@pytest.fixture
def span_capture(span_capture_factory: Callable[..., SpanCapture]) -> SpanCapture:
    """Tracing enabled, spans captured in memory, nothing on the network."""
    return span_capture_factory()
