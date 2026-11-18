"""Setup/deploy otomatis backend Alpha-Trade ke Botkeep via Developer API.

Pemakaian:
  python botkeep_setup.py                 # buat server (retry sampai kapasitas ada) + env + alias + start
  python botkeep_setup.py --status       # lihat status server saja
  python botkeep_setup.py --skip-create  # langkahi create (server sudah ada), lanjut env/alias/start

API key diambil dari env BOTKEEP_API_KEY (fallback ke key yang tersimpan di botkeep.key).
Dokumentasi API: https://api.botkeep.cloud/api/v1/developer/openapi.json
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "https://api.botkeep.cloud/api/v1/developer"
REPO_URL = "https://github.com/fataakromulmuttaqin/Alpha-Trade"
ALIAS = "alpha-trade"

ENV_VARS = [
    ("AT_SYMBOL", "PAXGUSDT"),
    ("AT_LABEL", "XAUUSD"),
    ("AT_ENTRY_TF", "5m"),
    ("AT_HTF", "4h"),
    ("EXECUTION_MODE", "off"),
]

CREATE_BODY = {
    "name": "alpha-trade",
    "platform": "general",
    "runtime": "python",
    "runtimeVersion": "3.11",
    "sourceType": "github",
    "repository": REPO_URL,
    "branch": "main",
    "startCommand": "python main.py",
    "resources": {"memoryLimitMb": 1024, "cpuLimitPercent": 50, "storageLimitMb": 1024},
}


def api_key():
    key = os.getenv("BOTKEEP_API_KEY")
    if key:
        return key.strip()
    kf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "botkeep.key")
    if os.path.isfile(kf):
        return open(kf, encoding="utf-8").read().strip()
    sys.exit("BOTKEEP_API_KEY tidak di-set dan botkeep.key tidak ada")


def call(method, path, body=None, key=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Authorization", "Bearer " + key)
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.add_header("Idempotency-Key", str(uuid.uuid4()))
        req.data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"detail": str(e)}


def wait_for_capacity(key, max_min=60):
    """POST /workloads sampai berhasil (503 = kapasitas platform penuh)."""
    deadline = time.time() + max_min * 60
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        st, out = call("POST", "/workloads", CREATE_BODY, key)
        if st in (200, 201):
            print(f"[create] OK pada percobaan {attempt}")
            return out
        detail = out.get("detail", out) if isinstance(out, dict) else out
        print(f"[create] percobaan {attempt}: {st} {str(detail)[:120]}", flush=True)
        time.sleep(90)
    sys.exit("kapasitas tidak tersedia dalam batas waktu")


def find_workload(key):
    st, out = call("GET", "/workloads", key=key)
    if st != 200:
        sys.exit(f"gagal list workloads: {st}")
    for w in out.get("data", []):
        if w.get("name") == "alpha-trade":
            return w
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--skip-create", action="store_true")
    args = ap.parse_args()
    key = api_key()

    w = find_workload(key)
    if args.status:
        print(json.dumps(w, indent=1)[:2000] if w else "server alpha-trade belum ada")
        return

    if w is None:
        if args.skip_create:
            sys.exit("server alpha-trade belum ada; jalankan tanpa --skip-create")
        created = wait_for_capacity(key)
        wid = created.get("id") or created.get("workload", {}).get("id")
        if not wid:
            print("respons create:", json.dumps(created)[:800])
            sys.exit("id workload tidak ditemukan di respons create")
    else:
        wid = w.get("id")
        print(f"[create] server sudah ada: {wid}")

    print(f"workload id: {wid}")

    # env vars (satu per panggilan sesuai skema EnvironmentInput)
    for k, v in ENV_VARS:
        st, out = call("PUT", f"/workloads/{wid}/environment",
                       {"key": k, "value": v, "isSecret": False}, key)
        print(f"[env] {k}={v} -> {st}")

    # domain alias (HTTPS)
    st, out = call("PUT", f"/workloads/{wid}/domain/alias", {"alias": ALIAS}, key)
    print(f"[alias] {ALIAS} -> {st} {json.dumps(out)[:200]}")
    st, dom = call("GET", f"/workloads/{wid}/domain", key=key)
    print(f"[domain] {st} {json.dumps(dom)[:300]}")

    # start
    st, out = call("POST", f"/workloads/{wid}/actions", {"action": "start"}, key)
    print(f"[start] -> {st} {json.dumps(out)[:200]}")


if __name__ == "__main__":
    main()
