"""Deploy backend+frontend Alpha-Trade ke Hugging Face Spaces (Docker, gratis 24/7).

Pemakaian:
  python deploy_hf.py --token hf_xxx          # build bundle + create/upload Space
  python deploy_hf.py --token hf_xxx --check  # cek /api/health Space yang sudah jalan

Token: buat di https://huggingface.co/settings/tokens (tipe Write).
Atau simpan ke file hf.token / set env HF_TOKEN.

Catatan:
- Space memakai SDK Docker, app listen di port 7860 (satu-satunya port publik).
- Kode yang di-upload DIBERSIHKAN dari key hardcoded (news keys wajib via Secrets).
- Frontend dilayani backend yang sama -> satu origin, polling 4 detik.
"""
import argparse
import os
import shutil
import sys
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
SPACE_NAME = "alpha-trade"
APP_PORT = 7860

DOCKERFILE = f"""FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 AT_PORT={APP_PORT} AT_WS_PORT=7861
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY frontend/ frontend/
EXPOSE {APP_PORT}
CMD ["python", "main.py"]
"""

README = f"""---
title: Alpha-Trade
emoji: "\\U0001F4C8"
colorFrom: yellow
colorTo: gray
sdk: docker
app_port: {APP_PORT}
pinned: false
---

Dashboard trading Alpha-Trade (XAUUSD) — engine confluence 5-gate + lapisan GODMODE.
Backend Python (stdlib http + websockets) melayani REST API dan frontend statik
dalam satu origin; frontend memakai polling 4 detik (Space membuka satu port).
"""


def find_token():
    tok = os.getenv("HF_TOKEN")
    if tok:
        return tok.strip()
    kf = os.path.join(ROOT, "hf.token")
    if os.path.isfile(kf):
        return open(kf, encoding="utf-8").read().strip()
    return None


def strip_hardcoded_keys(text):
    """Hapus key hardcoded (buang dari repo publik; isi lewat Secrets Space)."""
    for literal in ("pub_f9baa82bf10240d4bc29c431ef0817d0",
                    "d1c4Rk4nOBFM7BzfSeA8fj85FAsYws7X"):
        text = text.replace(literal, "")
    return text


def build_bundle(dest):
    # backend + frontend
    for folder in ("backend", "frontend"):
        src = os.path.join(ROOT, folder)
        dst = os.path.join(dest, folder)
        shutil.copytree(src, dst,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # bersihkan key hardcoded dari salinan
    nf = os.path.join(dest, "backend", "news_feed.py")
    with open(nf, encoding="utf-8") as f:
        txt = f.read()
    with open(nf, "w", encoding="utf-8") as f:
        f.write(strip_hardcoded_keys(txt))
    # requirements
    shutil.copy(os.path.join(ROOT, "requirements.txt"), dest)
    # Dockerfile + README metadata
    with open(os.path.join(dest, "Dockerfile"), "w", encoding="utf-8", newline="\n") as f:
        f.write(DOCKERFILE)
    with open(os.path.join(dest, "README.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(README)
    n = sum(len(fs) for _, _, fs in os.walk(dest))
    print(f"[bundle] {dest} ({n} file, key hardcoded dibersihkan)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=None)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    token = args.token or find_token()

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    who = api.whoami()
    user = who["name"]
    repo_id = f"{user}/{SPACE_NAME}"
    app_url = f"https://{user.lower()}-{SPACE_NAME}.hf.space"

    if args.check:
        with urllib.request.urlopen(f"{app_url}/api/health", timeout=30) as r:
            print("health:", r.read().decode()[:300])
        return

    bundle = tempfile.mkdtemp(prefix="at-hf-space-")
    build_bundle(bundle)

    api.create_repo(repo_id=repo_id, repo_type="space", space_sdk="docker",
                    private=False, exist_ok=True)
    api.upload_folder(folder_path=bundle, repo_id=repo_id, repo_type="space",
                      commit_message="deploy Alpha-Trade backend+frontend")

    # key news via Secrets Space (bukan di dalam kode)
    for k in ("NEWS_KEY_NEWSDATA", "NEWS_KEY_FMP"):
        val = os.getenv(k, "")
        if val:
            api.add_space_secret(repo_id=repo_id, key=k, value=val)
            print(f"[secret] {k} terpasang")
        else:
            print(f"[secret] {k} kosong — isi manual di Settings Space bila perlu")

    print(f"[selesai] Space : https://huggingface.co/spaces/{repo_id}")
    print(f"[selesai] App   : {app_url}  (tunggu build ~2-5 menit)")
    print(f"[cek]     python deploy_hf.py --token ... --check")


if __name__ == "__main__":
    main()
