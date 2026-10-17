import pytest
from fastapi.testclient import TestClient

from telereason.api.main import create_app


def test_health_defaults_to_fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENGINE", raising=False)
    monkeypatch.delenv("MODEL", raising=False)
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "engine": "fake",
        "model": "openai/gpt-oss-120b",
        "ready": True,
    }


def test_cors_allows_configured_frontend_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://demo.vercel.app")
    client = TestClient(create_app())

    response = client.get(
        "/api/health",
        headers={"Origin": "https://demo.vercel.app"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://demo.vercel.app"
