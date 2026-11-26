#!/usr/bin/env python3
"""Single source of truth for the pipeline (GitHub Actions, Jenkins and laptops all run these).

    python ci.py lint      ruff lint + format check
    python ci.py unit      tests with coverage >= 90%
    python ci.py build     container image
    python ci.py scan      Trivy: fail on fixable HIGH/CRITICAL CVEs
    python ci.py smoke     run the image, seed demo data, exercise the sense engine over HTTP
    python ci.py k8s       deploy deploy/kubernetes.yaml to a k3d cluster and repeat the smoke test there
    python ci.py all       everything, in order

Environment: K3D_CLUSTER (default "dev").
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
IMAGE = "cartsense:local"
TRIVY = "aquasec/trivy@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb"
CLUSTER = os.environ.get("K3D_CLUSTER", "dev")
CONTEXT, NS = f"k3d-{CLUSTER}", "cartsense"


def run(*cmd: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    res = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=capture)
    if check and res.returncode != 0:
        if capture:
            print(res.stdout, res.stderr, sep="\n")
        raise SystemExit(f"command failed ({res.returncode}): {' '.join(cmd)}")
    return res


def lint() -> None:
    run(PY, "-m", "ruff", "check", "src", "tests", "ci.py")
    run(PY, "-m", "ruff", "format", "--check", "src", "tests", "ci.py")


def unit() -> None:
    run(
        PY,
        "-m",
        "pytest",
        "-q",
        "--cov=cartsense",
        "--cov-report=term-missing",
        "--cov-fail-under=90",
        "--junitxml=reports/unit.xml",
    )


def build() -> None:
    run("docker", "build", "-t", IMAGE, ".")


def scan() -> None:
    cache = ROOT / ".trivy-cache"
    cache.mkdir(exist_ok=True)
    run(
        "docker",
        "run",
        "--rm",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-v",
        f"{cache}:/root/.cache/trivy",
        TRIVY,
        "image",
        "--quiet",
        "--exit-code",
        "1",
        "--severity",
        "HIGH,CRITICAL",
        "--ignore-unfixed",
        "--scanners",
        "vuln",
        IMAGE,
    )


def _get(base: str, path: str) -> dict | list:
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.load(r)


def _post(base: str, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        base + path, json.dumps(payload).encode(), {"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _http_check(base: str) -> None:
    """Exercise the storefront and every sense signal through real HTTP."""
    deadline = time.monotonic() + 90
    while True:
        try:
            if urllib.request.urlopen(base + "/healthz", timeout=5).read() == b"ok":
                break
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise SystemExit(f"{base} never became healthy")
        time.sleep(2)
    assert len(_get(base, "/api/products")) == 48, "catalog incomplete"
    fy = _get(base, "/api/foryou?user_id=1")
    assert fy["items"] and fy["items"][0]["reasons"], "sense rail empty or unexplained"
    _post(base, "/api/cart/add", {"user_id": 3, "product_id": 17, "qty": 1})
    sug = _get(base, "/api/cart/suggest?user_id=3")
    assert sug and all("reason" in s for s in sug), "no basket-sense suggestions for chips"
    order = _post(base, "/api/checkout", {"user_id": 3})
    assert order["order_id"] and order["eta_minutes"] >= 8, order
    html = urllib.request.urlopen(base + "/", timeout=10).read().decode()
    assert "cartsense" in html, "storefront did not render"
    print(
        f"smoke: 48 products, {len(fy['items'])} sensed suggestions "
        f"(top reason: {fy['items'][0]['reasons'][0]!r}), order #{order['order_id']} "
        f"ETA {order['eta_minutes']} min"
    )


def smoke() -> None:
    name, vol = "cartsense-smoke", "cartsense-smoke-data"
    run("docker", "rm", "-f", name, check=False, capture=True)
    run("docker", "volume", "rm", "-f", vol, check=False, capture=True)
    try:
        run("docker", "run", "--rm", "-v", f"{vol}:/data", IMAGE, "seed-demo")
        run("docker", "run", "-d", "--name", name, "--memory", "256m", "-p", "18010:8000", "-v", f"{vol}:/data", IMAGE)
        _http_check("http://127.0.0.1:18010")
    finally:
        run("docker", "rm", "-f", name, check=False, capture=True)
        run("docker", "volume", "rm", "-f", vol, check=False, capture=True)


def k8s() -> None:
    kc = ["kubectl", "--context", CONTEXT, "-n", NS]
    clusters = json.loads(run("k3d", "cluster", "list", "-o", "json", capture=True).stdout or "[]")
    if CLUSTER not in {c["name"] for c in clusters}:
        run("k3d", "cluster", "create", CLUSTER, "--agents", "0", "--wait")
    run("k3d", "image", "import", "-c", CLUSTER, IMAGE)
    run("kubectl", "--context", CONTEXT, "delete", "namespace", NS, "--ignore-not-found", "--wait=true")
    run("kubectl", "--context", CONTEXT, "create", "namespace", NS)
    run(*kc, "apply", "-f", "deploy/kubernetes.yaml")
    run(*kc, "rollout", "status", "deployment/cartsense", "--timeout=180s")
    run(*kc, "exec", "deploy/cartsense", "--", "python", "-m", "cartsense", "seed-demo")
    pf = subprocess.Popen(
        [*kc, "port-forward", "svc/cartsense", "18011:80"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _http_check("http://127.0.0.1:18011")
        print("k8s: deployment healthy, sense engine serving through the Service")
    finally:
        pf.terminate()
        run("kubectl", "--context", CONTEXT, "delete", "namespace", NS, "--wait=false", check=False)


STAGES = {"lint": lint, "unit": unit, "build": build, "scan": scan, "smoke": smoke, "k8s": k8s}


def main(argv: list[str]) -> int:
    names = list(STAGES) if argv in ([], ["all"]) else argv
    unknown = [n for n in names if n not in STAGES]
    if unknown:
        print(f"unknown stage(s) {unknown}; choose from {list(STAGES)} or all")
        return 2
    (ROOT / "reports").mkdir(exist_ok=True)
    for n in names:
        t0 = time.monotonic()
        print(f"\n===== stage: {n} =====", flush=True)
        STAGES[n]()
        print(f"===== {n} passed in {time.monotonic() - t0:.1f}s =====", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
