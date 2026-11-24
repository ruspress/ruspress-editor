import os
import json
import subprocess
import sys
import hashlib
import requests

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

CHANNEL = "@russianpressnews"
HISTORY = Path("published.json")
APPROVED = Path("approved.json")

MODE = os.getenv("EDITOR_MODE", "test")
MOSCOW = ZoneInfo("Europe/Moscow")

LIMITS = {
    "own": 4,
    "federal": 8,
    "regional": 8,
}

ORDER = [
    "federal",
    "regional",
    "own",
    "federal",
    "regional",
] * 4


def git(*args):
    subprocess.run(
        ["git", *args],
        check=True
    )


def save_history(history):
    HISTORY.write_text(
        json.dumps(
            history,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    git("add", "published.json")
    git("commit", "-m", "Update publication history")
    git("push", "origin", "HEAD:main")


def send_message(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": CHANNEL,
                "text": message,
                "parse_mode": "HTML",
                "link_preview_options": {
                    "is_disabled": True
                }
            },
            timeout=30
        )

        data = response.json()

        if not data.get("ok"):
            raise RuntimeError("Telegram rejected message")

        return data["result"]["message_id"]

    except Exception:
        raise RuntimeError(
            "Ошибка Telegram. Проверь канал "
            "перед повторной отправкой."
        ) from None


def main():
    if MODE not in ("test", "single", "auto"):
        raise SystemExit("Неизвестный режим")

    print(f"Режим редакции: {MODE}")

    now = datetime.now(MOSCOW)
    today = now.date().isoformat()

    history = (
        json.loads(HISTORY.read_text(encoding="utf-8"))
        if HISTORY.exists()
        else []
    )

    if not isinstance(history, list):
        raise SystemExit("Ошибка истории")

    if any(
        isinstance(item, dict)
        and item.get("status") == "pending"
        for item in history
    ):
        raise SystemExit(
            "Есть незавершённая отправка. "
            "Необходима проверка истории и канала."
        )

    env = os.environ.copy()
    env["PUBLISH_ENABLED"] = "false"

    subprocess.run(
        [sys.executable, "-u", "news.py"],
        env=env,
        check=True
    )

    subprocess.run(
        [sys.executable, "-u", "publish.py"],
        env=env,
        check=True
    )

    approved = json.loads(
        APPROVED.read_text(encoding="utf-8")
    )

    print(f"Проверено новостей: {len(approved)}")

    if MODE == "test":
        print("Тест завершён. Отправка отключена.")
        return

    if not approved:
        print("Нет подходящих публикаций")
        return

    now = datetime.now(MOSCOW)

    if now.date().isoformat() != today:
        print("Дата изменилась. Запуск пропущен.")
        return

    if not 8 <= now.hour < 23:
        print("Редакция вне рабочего времени")
        return

    daily = {
        category: sum(
            1 for item in history
            if isinstance(item, dict)
            and item.get("date") == today
            and item.get("category") == category
        )
        for category in LIMITS
    }

    if sum(daily.values()) >= 20:
        print("Суточный лимит исчерпан")
        return

    slot = (
        (now.hour * 60 + now.minute) - 480
    ) // 45

    if not 0 <= slot < len(ORDER):
        return

    slot_id = f"{today}:{slot}"

    if any(
        isinstance(item, dict)
        and item.get("slot") == slot_id
        for item in history
    ):
        print("Этот слот уже использован")
        return

    preferred = ORDER[slot]

    candidates = [
        draft for draft in approved
        if draft.get("category") in LIMITS
        and daily[draft["category"]]
        < LIMITS[draft["category"]]
        and draft.get("fact_check")
        == "ai_source_review"
    ]

    if MODE == "auto":
        candidates = [
            draft for draft in candidates
            if draft["category"] == preferred
        ]

    if not candidates:
        print("Нет новостей нужной категории")
        return

    draft = candidates[0]

    message = draft["message"]

    if len(message) > 4096:
        raise SystemExit("Сообщение слишком длинное")

    # История сохраняется до отправки.
    # При сбое повторная публикация блокируется.

    record = {
        "date": today,
        "slot": slot_id,
        "category": draft["category"],
        "headline": draft["headline"],
        "source_ids": draft["source_ids"],
        "status": "pending",
        "fingerprint": hashlib.sha256(
            message.encode("utf-8")
        ).hexdigest(),
        "created_at": now.isoformat(),
    }

    history.append(record)
    save_history(history)

    message_id = send_message(message)

    record["status"] = "published"
    record["message_id"] = message_id
    record["published_at"] = datetime.now(
        MOSCOW
    ).isoformat()

    save_history(history)

    print("=" * 50)
    print("SUCCESS: новость опубликована")
    print("Telegram message ID:", message_id)
    print("Категория:", draft["category"])
    print("Заголовок:", draft["headline"])


if __name__ == "__main__":
    main()
