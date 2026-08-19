"""DeepSeek chat-client (OpenAI-compatible). Alleen stdlib.

Gebruik: py scripts/deepseek_client.py "Schrijf een korte cold email voor een loodgieter"
"""
import json
import sys

from _common import env, post_json

API_URL = "https://api.deepseek.com/v1/chat/completions"


def chat(messages, model="deepseek-chat", temperature=0.7, max_tokens=1024):
    key = env("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY ontbreekt in .env")
    status, body = post_json(
        API_URL,
        {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
        headers={"Authorization": f"Bearer {key}"},
    )
    if status != 200:
        raise RuntimeError(f"DeepSeek error {status}: {body}")
    return json.loads(body)["choices"][0]["message"]["content"]


def main():
    prompt = " ".join(sys.argv[1:]) or "Zeg in 1 zin dat de Adsvantage Fleet werkt."
    try:
        print(chat([{"role": "user", "content": prompt}]))
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"FOUT: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
