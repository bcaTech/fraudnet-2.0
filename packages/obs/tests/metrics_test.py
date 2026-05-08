from __future__ import annotations

from fraudnet.obs.metrics import metrics_endpoint, request_duration, requests_total


def test_request_metrics_render() -> None:
    requests_total.labels(
        service="ingest-momo",
        route="/health",
        method="GET",
        status="200",
    ).inc()
    request_duration.labels(
        service="ingest-momo",
        route="/health",
        method="GET",
        status="200",
    ).observe(0.012)

    body, content_type = metrics_endpoint()()
    assert b"fraudnet_requests_total" in body
    assert b"fraudnet_request_duration_seconds" in body
    assert "text/plain" in content_type
