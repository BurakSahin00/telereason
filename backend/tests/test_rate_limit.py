"""Rate limits on POST /api/ask and POST /api/night-runs, read from env, with a fake clock."""

import pytest
from fastapi.testclient import TestClient

from telereason.api import fake
from telereason.api.main import create_app
from telereason.settings import load_settings
from tests.test_contract import assert_error_shape

ASK = ("/api/ask", {"question": "What did last night's report find?"})
NIGHT = ("/api/night-runs", {"max_windows": 10})
ENV = ("ENGINE", "RATE_LIMIT_ASK_PER_MIN", "RATE_LIMIT_NIGHT_PER_MIN", "TRUST_PROXY_HEADERS")


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


def start(monkeypatch: pytest.MonkeyPatch, **env: str) -> tuple[TestClient, Clock]:
    monkeypatch.setattr(fake, "EVENT_DELAY_S", (0.0, 0.0))
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    app = create_app(load_settings())
    clock = Clock()
    for limiter in app.state.rate_limiters.values():
        limiter.clock = clock
    return TestClient(app), clock


def statuses(
    client: TestClient, route: tuple[str, object], n: int, headers: dict[str, str] | None = None
) -> list[int]:
    return [client.post(route[0], json=route[1], headers=headers).status_code for _ in range(n)]


def test_defaults_are_10_asks_and_2_night_runs_per_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = start(monkeypatch)

    assert statuses(client, ASK, 11) == [200] * 10 + [429]
    assert statuses(client, NIGHT, 2) == [202] * 2
    response = client.post(NIGHT[0], json=NIGHT[1])

    assert response.status_code == 429
    assert_error_shape(response.json(), "rate_limited")
    assert response.headers["Retry-After"] == "30"  # one token every 60 / 2 s


def test_tokens_refill_over_the_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    client, clock = start(monkeypatch, RATE_LIMIT_ASK_PER_MIN="3")
    assert statuses(client, ASK, 4) == [200] * 3 + [429]

    clock.now += 19
    assert statuses(client, ASK, 1) == [429]
    clock.now += 1
    assert statuses(client, ASK, 2) == [200, 429]
    clock.now += 60
    assert statuses(client, ASK, 4) == [200] * 3 + [429]


def test_zero_turns_a_limit_off(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = start(monkeypatch, RATE_LIMIT_ASK_PER_MIN="0")

    assert statuses(client, ASK, 30) == [200] * 30
    assert statuses(client, NIGHT, 3) == [202, 202, 429]


@pytest.mark.parametrize("trusted", [False, True])
def test_clients_by_proxy_header_only_when_trusted(
    monkeypatch: pytest.MonkeyPatch, trusted: bool
) -> None:
    flag = "1" if trusted else "0"
    client, _ = start(monkeypatch, RATE_LIMIT_NIGHT_PER_MIN="1", TRUST_PROXY_HEADERS=flag)

    assert statuses(client, NIGHT, 2, {"CF-Connecting-IP": "203.0.113.1"}) == [202, 429]
    # Trusted, another client has its own bucket; untrusted, the headers change nothing.
    other = [202 if trusted else 429]
    assert statuses(client, NIGHT, 1, {"CF-Connecting-IP": "203.0.113.2"}) == other
    # Without CF-Connecting-IP the last X-Forwarded-For value is the client.
    assert statuses(client, NIGHT, 1, {"X-Forwarded-For": "10.0.0.1, 198.51.100.7"}) == other
    assert statuses(client, NIGHT, 1, {"X-Forwarded-For": "10.0.0.2, 198.51.100.7"}) == [429]


def test_spoofed_first_forwarded_value_does_not_change_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = start(monkeypatch, RATE_LIMIT_NIGHT_PER_MIN="1", TRUST_PROXY_HEADERS="1")

    assert statuses(client, NIGHT, 1, {"X-Forwarded-For": "198.51.100.7"}) == [202]
    spoofed = {"X-Forwarded-For": "203.0.113.99, 198.51.100.7"}
    assert statuses(client, NIGHT, 1, spoofed) == [429]
