import os
import json
import hashlib

from datetime import datetime
from editor import (
    HISTORY,
    MOSCOW,
    LIMITS,
    save_history,
    send_message,
)

headline = (
    "Аэропорт Волгограда возобновил "
    "обслуживание рейсов"
)

url = "https://www.interfax.ru/russia/1131114"

message = (
    f"<b>{headline}</b>\n\n"
    "Ограничения на приём и выпуск воздушных "
    "судов в аэропорту Волгограда сняты.\n\n"
    "Об этом во вторник вечером "
    "сообщила Росавиация.\n\n"
    f'<a href="{url}">Источник: Интерфакс</a>\n\n'
    '<a href="https://t.me/russianpressnews">'
    'Подписаться на «Российскую прессу»</a>'
)

source_id = hashlib.sha256(
    url.encode("utf-8")
).hexdigest()

history = (
    json.loads(HISTORY.read_text(encoding="utf-8"))
    if HISTORY.exists()
    else []
)

if not isinstance(history, list):
    raise SystemExit("Ошибка истории")

for item in history:
    if isinstance(item, str):
        if item == source_id:
            raise SystemExit("Новость уже опубликована")
        continue

    if item.get("status") == "pending":
        raise SystemExit(
            "Обнаружена незавершённая отправка"
        )

    if source_id in item.get("source_ids", []):
        raise SystemExit("Новость уже опубликована")

now = datetime.now(MOSCOW)
today = now.date().isoformat()

if not 8 <= now.hour < 23:
    raise SystemExit("Вне времени публикаций")

slot = (now.hour * 60 + now.minute - 480) // 45
slot_id = f"{today}:{slot}"

daily = [
    item for item in history
    if isinstance(item, dict)
    and item.get("date") == today
]

if len(daily) >= 20:
    raise SystemExit("Суточный лимит исчерпан")

if sum(
    item.get("category") == "federal"
    for item in daily
) >= LIMITS["federal"]:
    raise SystemExit("Лимит федеральных новостей")

if any(
    item.get("slot") == slot_id
    for item in daily
):
    raise SystemExit("Слот уже использован")

if not os.getenv("TELEGRAM_BOT_TOKEN"):
    raise SystemExit("Не найден секрет Telegram")

record = {
    "date": today,
    "slot": slot_id,
    "category": "federal",
    "headline": headline,
    "source_ids": [source_id],
    "status": "pending",
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
print("История сохранена в GitHub")
print("=" * 50)
