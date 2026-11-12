# Tes API key LLM untuk provider yang kompatibel dengan format OpenAI
# Jalankan: uv run test_api_key.py

import os
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")
API_KEY = os.getenv("LLM_API_KEY", "")
MODEL = os.getenv("LLM_MODEL", "")

# Pesan error per status code, biar langsung ketahuan masalahnya di mana
ERROR_HINTS = {
    401: "API key salah atau sudah dicabut.",
    402: "Kredit gratis bulanan habis, perlu beli kredit.",
    403: "API key valid, tapi nggak punya akses ke model atau endpoint ini.",
    404: "LLM_BASE_URL atau LLM_MODEL salah.",
    429: "Kena rate limit atau kuota/saldo habis.",
}


def check_env():
    missing = [
        name
        for name, value in [
            ("LLM_BASE_URL", BASE_URL),
            ("LLM_API_KEY", API_KEY),
            ("LLM_MODEL", MODEL),
        ]
        if not value
    ]
    if missing:
        print(f"[GAGAL] Variable belum diisi di .env: {', '.join(missing)}")
        sys.exit(1)


def print_error(response):
    hint = ERROR_HINTS.get(
        response.status_code, "Error lain, cek isi response di bawah."
    )
    print(f"[GAGAL] Status {response.status_code}: {hint}")
    print(response.text[:500])


def test_chat(client):
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Balas dengan satu kata saja: oke"}],
        # Token reasoning ikut dihitung di max_tokens, jadi jangan terlalu kecil
        "max_tokens": 200,
        "temperature": 0,
    }
    start = time.perf_counter()
    response = client.post(f"{BASE_URL}/chat/completions", json=payload)
    latency_ms = (time.perf_counter() - start) * 1000

    if response.status_code != 200:
        print_error(response)
        return False

    data = response.json()
    reply = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    print("[OK] API key bisa dipakai")
    print(f"     Model   : {data.get('model', MODEL)}")
    print(f"     Balasan : {reply!r}")
    print(f"     Latency : {latency_ms:.0f} ms")
    print(f"     Token   : {usage}")
    return True


def main():
    check_env()
    print(f"Ngetes {MODEL} di {BASE_URL} ...")
    headers = {"Authorization": f"Bearer {API_KEY}"}
    with httpx.Client(headers=headers, timeout=30) as client:
        try:
            ok = test_chat(client)
        except httpx.RequestError as exc:
            print(f"[GAGAL] Nggak bisa nyambung ke server: {exc}")
            ok = False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
