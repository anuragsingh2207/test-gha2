import asyncio
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app, create_github_client, get_github_client


def test_github_client_reads_optional_environment_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_API_BASE_URL", "https://github.example")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    github_client = create_github_client()
    try:
        assert str(github_client.base_url) == "https://github.example"
        assert github_client.headers["Authorization"] == "Bearer test-token"
    finally:
        asyncio.run(github_client.aclose())


@pytest.fixture
def client_factory() -> Iterator[Callable[[httpx.MockTransport], TestClient]]:
    clients: list[httpx.AsyncClient] = []

    def create(transport: httpx.MockTransport) -> TestClient:
        github_client = create_github_client(transport=transport)
        clients.append(github_client)
        app.dependency_overrides[get_github_client] = lambda: github_client
        return TestClient(app)

    yield create

    app.dependency_overrides.clear()
    for github_client in clients:
        asyncio.run(github_client.aclose())


def test_returns_public_gists_for_user(
    client_factory: Callable[[httpx.MockTransport], TestClient],
) -> None:
    expected_gists = [
        {"id": "gist-1", "description": "Example public gist", "public": True}
    ]
    requests: list[httpx.Request] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=expected_gists)

    with client_factory(httpx.MockTransport(handle_request)) as client:
        response = client.get("/octocat")

    assert response.status_code == 200
    assert response.json() == expected_gists
    assert len(requests) == 1
    assert requests[0].url.path == "/users/octocat/gists"
    assert requests[0].url.params["per_page"] == "100"
    assert requests[0].url.params["page"] == "1"
    assert requests[0].headers["X-GitHub-Api-Version"] == "2022-11-28"


def test_returns_empty_list_when_user_has_no_gists(
    client_factory: Callable[[httpx.MockTransport], TestClient],
) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=[]))

    with client_factory(transport) as client:
        response = client.get("/octocat")

    assert response.status_code == 200
    assert response.json() == []


def test_fetches_every_gist_page(
    client_factory: Callable[[httpx.MockTransport], TestClient],
) -> None:
    requested_pages: list[str] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        page = request.url.params["page"]
        requested_pages.append(page)
        if page == "1":
            return httpx.Response(
                200,
                json=[{"id": f"gist-{index}"} for index in range(100)],
            )
        return httpx.Response(200, json=[{"id": "gist-100"}])

    with client_factory(httpx.MockTransport(handle_request)) as client:
        response = client.get("/octocat")

    assert response.status_code == 200
    assert len(response.json()) == 101
    assert requested_pages == ["1", "2"]


def test_returns_404_when_github_user_does_not_exist(
    client_factory: Callable[[httpx.MockTransport], TestClient],
) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(404, json={"message": "Not Found"})
    )

    with client_factory(transport) as client:
        response = client.get("/not-a-user")

    assert response.status_code == 404
    assert response.json()["detail"] == "GitHub user not found"


def test_returns_gateway_error_for_github_server_error(
    client_factory: Callable[[httpx.MockTransport], TestClient],
) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(500, json={"message": "Server Error"})
    )

    with client_factory(transport) as client:
        response = client.get("/octocat")

    assert response.status_code == 502
    assert response.json()["detail"] == "GitHub API returned an error"


def test_returns_gateway_timeout_when_github_request_times_out(
    client_factory: Callable[[httpx.MockTransport], TestClient],
) -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Request timed out", request=request)

    with client_factory(httpx.MockTransport(handle_request)) as client:
        response = client.get("/octocat")

    assert response.status_code == 504
    assert response.json()["detail"] == "GitHub API request timed out"


def test_returns_gateway_error_when_github_cannot_be_reached(
    client_factory: Callable[[httpx.MockTransport], TestClient],
) -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection failed", request=request)

    with client_factory(httpx.MockTransport(handle_request)) as client:
        response = client.get("/octocat")

    assert response.status_code == 502
    assert response.json()["detail"] == "Unable to reach GitHub API"


@pytest.mark.parametrize("status_code", [403, 429])
def test_returns_service_unavailable_when_github_rate_limits(
    client_factory: Callable[[httpx.MockTransport], TestClient],
    status_code: int,
) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(status_code, json={"message": "Rate limited"})
    )

    with client_factory(transport) as client:
        response = client.get("/octocat")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "GitHub API request was forbidden or rate limited"
    )


def test_rejects_invalid_github_usernames(
    client_factory: Callable[[httpx.MockTransport], TestClient],
) -> None:
    transport = httpx.MockTransport(
        lambda _: pytest.fail("GitHub API must not be called")
    )

    with client_factory(transport) as client:
        response = client.get("/not--valid")

    assert response.status_code == 422


def test_returns_gateway_error_for_invalid_github_json(
    client_factory: Callable[[httpx.MockTransport], TestClient],
) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"not-json")
    )

    with client_factory(transport) as client:
        response = client.get("/octocat")

    assert response.status_code == 502
    assert response.json()["detail"] == "GitHub API returned invalid JSON"


@pytest.mark.parametrize("payload", [{"unexpected": "object"}, [1]])
def test_returns_gateway_error_for_unexpected_github_payload(
    client_factory: Callable[[httpx.MockTransport], TestClient],
    payload: object,
) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))

    with client_factory(transport) as client:
        response = client.get("/octocat")

    assert response.status_code == 502
    assert response.json()["detail"] == "GitHub API returned an unexpected response"


def test_health_endpoint(
    client_factory: Callable[[httpx.MockTransport], TestClient],
) -> None:
    transport = httpx.MockTransport(
        lambda _: pytest.fail("Health checks must not call GitHub")
    )

    with client_factory(transport) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
