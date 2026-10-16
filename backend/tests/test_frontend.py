from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from telereason.api.main import create_app
from telereason.settings import DEFAULT_MODEL, Settings, load_settings
from tests.test_contract import assert_error_shape

INDEX_HTML = '<!doctype html><div id="root"></div>'
ASSET_JS = "console.log('telereason')"


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text(ASSET_JS, encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside dist", encoding="utf-8")
    return root


def client_for(frontend_dist: str) -> TestClient:
    settings = Settings(engine="fake", model=DEFAULT_MODEL, frontend_dist=frontend_dist)
    return TestClient(create_app(settings))


def test_root_serves_index_html(dist: Path) -> None:
    response = client_for(str(dist)).get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == INDEX_HTML


def test_existing_asset_is_served_as_a_file(dist: Path) -> None:
    response = client_for(str(dist)).get("/assets/index-abc123.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert response.text == ASSET_JS


def test_deep_link_serves_index_html(dist: Path) -> None:
    response = client_for(str(dist)).get("/windows/W-823610")

    assert response.status_code == 200
    assert response.text == INDEX_HTML


def test_api_routes_still_answer(dist: Path) -> None:
    client = client_for(str(dist))

    assert client.get("/api/health").json()["engine"] == "fake"
    assert client.get("/openapi.json").json()["info"]["title"] == "TeleReason"
    assert client.get("/docs").status_code == 200


@pytest.mark.parametrize("path", ["/api", "/api/unknown", "/api/windows/W-1/unknown"])
def test_unknown_api_path_keeps_the_json_404(dist: Path, path: str) -> None:
    response = client_for(str(dist)).get(path)

    assert response.status_code == 404
    assert_error_shape(response.json(), "not_found")


@pytest.mark.parametrize("path", ["/..%2fsecret.txt", "/assets/..%2f..%2fsecret.txt"])
def test_path_outside_dist_returns_404(dist: Path, path: str) -> None:
    response = client_for(str(dist)).get(path)

    assert response.status_code == 404
    assert "outside dist" not in response.text
    assert_error_shape(response.json(), "not_found")


def test_nothing_is_mounted_without_frontend_dist() -> None:
    client = client_for("")

    response = client.get("/")
    assert response.status_code == 404
    assert_error_shape(response.json(), "not_found")
    assert client.get("/windows/W-823610").status_code == 404


def test_frontend_dist_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRONTEND_DIST", raising=False)
    assert load_settings().frontend_dist == ""

    monkeypatch.setenv("FRONTEND_DIST", " /app/frontend/dist ")
    assert load_settings().frontend_dist == "/app/frontend/dist"


def test_missing_index_html_stops_startup(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="FRONTEND_DIST"):
        client_for(str(tmp_path))
