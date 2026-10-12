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
