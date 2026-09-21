from httpx import ASGITransport, AsyncClient

from apps.api.main import create_app


async def test_liveness_and_request_id_contract() -> None:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health/live", headers={"X-Request-ID": "test-request"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "test-request"


def test_resource_finance_and_class_size_paths_are_exposed() -> None:
    paths = create_app().openapi()["paths"]
    assert "/api/v1/revisions/{revision_id}:export" in paths
    assert "/api/v1/audit-archives/{audit_archive_id}" in paths
    assert "/api/v1/audit-archives/{audit_archive_id}/download" in paths
    assert "/api/v1/revisions/{revision_id}/targets:replace-and-lock" in paths
    assert "/api/v1/target-set-versions/{target_set_version_id}" in paths
    assert "/api/v1/revisions/{revision_id}/resource-finance:replace-draft" in paths
    assert "/api/v1/revisions/{revision_id}/resource-finance:confirm" in paths
    assert "/api/v1/resource-finance-parameter-versions/{resource_finance_parameter_version_id}" in paths
    assert "/api/v1/revisions/{revision_id}/class-sizes:validate" in paths
    assert "/api/v1/class-size-result-versions/{class_size_result_version_id}" in paths
    assert "/api/v1/revisions/{revision_id}/discount-parameters:replace-draft" in paths
    assert "/api/v1/revisions/{revision_id}/discount-parameters:confirm" in paths
    assert "/api/v1/discount-parameter-versions/{discount_parameter_version_id}" in paths
    assert "/api/v1/revisions/{revision_id}/revenues:calculate" in paths
    assert "/api/v1/revenue-result-versions/{revenue_result_version_id}" in paths
    assert "/api/v1/revisions/{revision_id}/target-closures:validate" in paths
    assert "/api/v1/target-closure-result-versions/{target_closure_result_version_id}" in paths
    assert "/api/v1/revisions/{revision_id}/optimization-runs" in paths
    assert "/api/v1/optimization-runs/{optimization_run_id}" in paths
