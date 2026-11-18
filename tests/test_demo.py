import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from resilient_http.demo import Scenario, simulate
from resilient_http.demo.server import DashboardHandler


def test_simulation_is_deterministic():
    a = simulate(Scenario(seed=3))
    b = simulate(Scenario(seed=3))
    assert a == b


def test_outage_trips_breaker_and_rejects():
    r = simulate(Scenario(failure_rate=0, outage_start=2, outage_end=10,
                          failure_threshold=3, recovery_timeout=2))
    assert r["summary"]["breaker_trips"] >= 1
    assert r["summary"]["outcomes"].get("rejected", 0) > 0
    assert r["requests"][-1]["outcome"] == "ok"  # recuperou após a queda


def test_without_breaker_nothing_rejected():
    r = simulate(Scenario(failure_threshold=0))
    assert r["summary"]["rejected_by_breaker"] == 0


def test_scenario_from_dict_coerces_and_clamps():
    s = Scenario.from_dict({"requests": "9999", "rate": 2, "jitter": "none", "x": 1})
    assert s.requests == 500 and s.rate == 2.0 and s.jitter == "none"


def test_http_server_end_to_end():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        html = urllib.request.urlopen(base + "/").read().decode()
        assert "Resilience Lab" in html
        with_query = urllib.request.urlopen(base + "/?theme=dark&preset=Sem%20breaker").read()
        assert b"Resilience Lab" in with_query
        req = urllib.request.Request(base + "/api/simulate", method="POST",
                                     data=json.dumps({"requests": 5}).encode(),
                                     headers={"Content-Type": "application/json"})
        data = json.loads(urllib.request.urlopen(req).read())
        assert data["summary"]["requests"] == 5
    finally:
        httpd.shutdown()
        httpd.server_close()
