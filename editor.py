import os
import re
import sys
import json
import time
import hashlib
import requests
import subprocess

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from difflib import SequenceMatcher
from PIL import Image, ImageDraw, ImageFont

CHANNEL = "@russianpressnews"
HISTORY = Path("published.json")
QUEUE = Path("queue.json")
APPROVED = Path("approved.json")

MODE = os.getenv("EDITOR_MODE", "test")
MOSCOW = ZoneInfo("Europe/Moscow")

LIMITS = {
    "own": 4,
    "federal": 8,
    "regional": 8,
}

RESERVE = {
    "own": 6,
    "federal": 12,
    "regional": 12,
}

LABELS = {
    "own": "МАТЕРИАЛЫ РЕДАКЦИИ",
    "federal": "РОССИЯ И МИР",
    "regional": "РЕГИОНЫ И ЭКОНОМИКА",
}

ORDER = [
    "federal",
    "regional",
    "own",
    "federal",
    "regional",
] * 4

MAX_AGE = 24 * 3600


def load_json(path):
    if not path.exists():
        return []

    data = json.loads(
        path.read_text(encoding="utf-8")
    )

    if not isinstance(data, list):
        raise RuntimeError("Ошибка файла " + str(path))

    return data


def write_json(path, data):
    temp = path.with_suffix(".tmp")

    temp.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    temp.replace(path)


def git(*args):
    subprocess.run(
        ["git", *args],
        check=True
    )


def save_state(history, queue=None):
    write_json(HISTORY, history)
    git("add", str(HISTORY))

    if queue is not None:
        write_json(QUEUE, queue)
        git("add", str(QUEUE))

    changed = subprocess.run(
        ["git", "diff", "--cached", "--quiet"]
    ).returncode

    if changed == 0:
        return

    if changed != 1:
        raise RuntimeError("Ошибка Git")

    git("commit", "-m", "Update editorial state")
    git("push", "origin", "HEAD:main")


def save_history(history):
    save_state(history)


def normalize(text):
    return " ".join(
        re.findall(
            r"\w+",
            text.casefold().replace("ё", "е")
        )
    )


def duplicate(draft, records):
    ids = set(draft.get("source_ids", []))
    headline = normalize(
        draft.get("headline", "")
    )

    for old in records:
        if isinstance(old, str):
            if old in ids:
                return True
            continue

        if ids.intersection(
            old.get("source_ids", [])
        ):
            return True

        previous = normalize(
            old.get("headline", "")
        )

        if headline and previous:
            ratio = SequenceMatcher(
                None, headline, previous
            ).ratio()

            if ratio >= 0.80:
                return True

    return False


def fresh(draft):
    timestamp = draft.get("source_timestamp", 0)

    return (
        0 <= time.time() - timestamp <= MAX_AGE
    )


def clean_queue(queue, history):
    result = []

    for draft in queue:
        if not fresh(draft):
            continue

        if draft.get("category") not in LIMITS:
            continue

        if duplicate(draft, history + result):
            continue

        result.append(draft)

    return result


def counts(queue):
    return {
        category: sum(
            item.get("category") == category
            for item in queue
        )
        for category in LIMITS
    }


def refill(queue, history):
    current = counts(queue)

    if all(
        current[c] >= RESERVE[c]
        for c in RESERVE
    ):
        print("Резерв заполнен")
        return queue

    env = os.environ.copy()
    env["PUBLISH_ENABLED"] = "false"

    # Передаём очередь сборщику как временную
    # историю, чтобы он искал новые материалы.
    existed = HISTORY.exists()

    original = (
        HISTORY.read_bytes()
        if existed else None
    )

    try:
        write_json(HISTORY, history + queue)

        subprocess.run(
            [sys.executable, "-u", "news.py"],
            env=env,
            check=True,
            timeout=240
        )

        subprocess.run(
            [sys.executable, "-u", "publish.py"],
            env=env,
            check=True,
            timeout=360
        )

        drafts = load_json(APPROVED)

    except Exception as e:
        print("Ошибка пополнения:", type(e).__name__)
        return queue

    finally:
        if existed:
            HISTORY.write_bytes(original)
        else:
            HISTORY.unlink(missing_ok=True)

    for draft in drafts:
        category = draft.get("category")

        if category not in RESERVE:
            continue

        if current[category] >= RESERVE[category]:
            continue

        if draft.get("fact_check") != "ai_source_review":
            continue

        if not draft.get("evidence"):
            continue

        if duplicate(draft, history + queue):
            continue

        sources = draft.get("sources", [])

        if not sources:
            continue

        ages = [
            int(source.get("age", MAX_AGE))
            for source in sources
        ]

        draft["source_timestamp"] = (
            time.time() - max(ages) - 600
        )

        draft["queued_at"] = datetime.now(
            MOSCOW
        ).isoformat()

        draft["date"] = "queue"

        if not fresh(draft):
            continue

        queue.append(draft)
        current[category] += 1

        print("Добавлено в резерв:", draft["headline"])

    return queue


def font(size, bold=False):
    name = (
        "DejaVuSans-Bold.ttf"
        if bold else "DejaVuSans.ttf"
    )

    return ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/" + name,
        size
    )


def wrap_text(draw, text, face, width):
    lines = []
    line = ""

    for word in text.split():
        candidate = (line + " " + word).strip()

        if draw.textlength(
            candidate, font=face
        ) <= width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word

    if line:
        lines.append(line)

    return lines


def create_card(draft):
    width, height = 1280, 720

    image = Image.new(
        "RGB",
        (width, height),
        "#07152F"
    )

    draw = ImageDraw.Draw(image)

    for y in range(height):
        shade = y / height

        draw.line(
            (0, y, width, y),
            fill=(
                int(7 + shade * 8),
                int(21 + shade * 20),
                int(47 + shade * 45)
            )
        )

    draw.polygon(
        [(950, 0), (1280, 0),
         (1280, 400), (1100, 220)],
        fill="#123D77"
    )

    draw.rectangle(
        (65, 55, 75, 650),
        fill="#2799ED"
    )

    draw.text(
        (110, 60),
        "РОССИЙСКАЯ ПРЕССА",
        font=font(34, True),
        fill="#FFFFFF"
    )

    draw.text(
        (110, 125),
        LABELS[draft["category"]],
        font=font(23, True),
        fill="#60B8FF"
    )

    headline = draft["headline"]

    for size in range(60, 29, -2):
        face = font(size, True)

        lines = wrap_text(
            draw, headline, face, 1040
        )

        if (
            len(lines) * (size + 15) <= 350
            and all(
                draw.textlength(line, font=face)
                <= 1040
                for line in lines
            )
        ):
            break
    else:
        raise RuntimeError("Заголовок не помещается")

    y = 205

    for line in lines:
        draw.text(
            (110, y),
            line,
            font=face,
            fill="#FFFFFF"
        )

        y += size + 15

    draw.line(
        (110, 600, 1160, 600),
        fill="#31517A",
        width=2
    )

    draw.text(
        (110, 625),
        "@russianpressnews",
        font=font(25),
        fill="#A9D5FF"
    )

    draw.text(
        (840, 625),
        "КАРТОЧКА РЕДАКЦИИ",
        font=font(20),
        fill="#A9D5FF"
    )

    path = Path("news-card.jpg")

    image.save(
        path,
        "JPEG",
        quality=93
    )

    return path


def send_message(message, photo=None):
    token = os.environ["TELEGRAM_BOT_TOKEN"]

    try:
        if photo:
            with open(photo, "rb") as file:
                response = requests.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    data={
                        "chat_id": CHANNEL,
                        "caption": message,
                        "parse_mode": "HTML",
                    },
                    files={"photo": file},
                    timeout=45
                )
        else:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": CHANNEL,
                    "text": message,
                    "parse_mode": "HTML",
                    "link_preview_options": {
                        "is_disabled": True
                    },
                },
                timeout=30
            )

        data = response.json()

        if not data.get("ok"):
            raise RuntimeError("Telegram rejected")

        return data["result"]["message_id"]

    except Exception:
        raise RuntimeError(
            "Ошибка Telegram. Проверь канал "
            "и запись pending перед повтором."
        ) from None


def publish(draft, history, queue, slot):
    now = datetime.now(MOSCOW)

    message = draft["message"]

    daily = [
        item for item in history
        if isinstance(item, dict)
        and item.get("date") == now.date().isoformat()
        and item.get("status") == "published"
    ]

    photo_count = sum(
        bool(item.get("has_photo"))
        for item in daily
    )

    photo = None

    # Цель — около 70% публикаций с карточками.
    desired = ((len(daily) + 1) * 7 + 9) // 10

    plain = re.sub(r"<[^>]+>", "", message)

    if photo_count < desired and len(plain) <= 950:
        try:
            photo = create_card(draft)
        except Exception as e:
            print("Карточка пропущена:", type(e).__name__)

    record = {
        "date": now.date().isoformat(),
        "slot": slot,
        "category": draft["category"],
        "headline": draft["headline"],
        "source_ids": draft["source_ids"],
        "status": "pending",
        "has_photo": bool(photo),
        "created_at": now.isoformat(),
        "fingerprint": hashlib.sha256(
            message.encode("utf-8")
        ).hexdigest(),
    }

    history.append(record)

    # Сначала сохраняем состояние, затем отправляем.
    save_state(history, queue)

    message_id = send_message(message, photo)

    record["status"] = "published"
    record["message_id"] = message_id
    record["published_at"] = datetime.now(
        MOSCOW
    ).isoformat()

    queue.remove(draft)

    save_state(history, queue)

    print("SUCCESS: новость опубликована")
    print("Telegram message ID:", message_id)
    print("Изображение:", bool(photo))


def main():
    if MODE not in ("test", "single", "auto"):
        raise SystemExit("Неизвестный режим")

    history = load_json(HISTORY)
    queue = load_json(QUEUE)

    if any(
        isinstance(item, dict)
        and item.get("status") == "pending"
        for item in history
    ):
        raise SystemExit(
            "Незавершённая отправка. Проверь канал."
        )

    queue = clean_queue(queue, history)

    now = datetime.now(MOSCOW)
    today = now.date().isoformat()

    daily = [
        item for item in history
        if isinstance(item, dict)
        and item.get("date") == today
        and item.get("status") == "published"
    ]

    used = counts(daily)

    minute = now.hour * 60 + now.minute

    working = 480 <= minute < 1380

    due = (
        min(20, (minute - 480) // 45 + 1)
        if working else 0
    )

    # Если есть готовая новость, публикуем
    # до обращения к API генерации.
    if MODE != "test" and working:
        if len(daily) < due:
            wanted = counts([
                {"category": category}
                for category in ORDER[:due]
            ])

            candidates = [
                draft for draft in queue
                if used[draft["category"]]
                < min(
                    LIMITS[draft["category"]],
                    wanted[draft["category"]]
                )
            ]

            if candidates:
                candidates.sort(
                    key=lambda item: item[
                        "source_timestamp"
                    ]
                )

                slot = f"{today}:queue:{len(daily)}"

                publish(
                    candidates[0],
                    history,
                    queue,
                    slot
                )

                daily.append(history[-1])

                if MODE == "single":
                    return

    # Пополняем очередь при нехватке материалов.
    queue = refill(queue, history)
    queue = clean_queue(queue, history)

    now = datetime.now(MOSCOW)

    # Если первая попытка не удалась из-за пустой
    # очереди, пробуем после её пополнения.
    if MODE != "test":
        minute = now.hour * 60 + now.minute

        if (
            now.date().isoformat() == today
            and 480 <= minute < 1380
        ):
            daily = [
                item for item in history
                if isinstance(item, dict)
                and item.get("date") == today
                and item.get("status") == "published"
            ]

            due = min(
                20, (minute - 480) // 45 + 1
            )

            used = counts(daily)

            wanted = counts([
                {"category": category}
                for category in ORDER[:due]
            ])

            candidates = [
                draft for draft in queue
                if used[draft["category"]]
                < min(
                    LIMITS[draft["category"]],
                    wanted[draft["category"]]
                )
            ]

            if len(daily) < due and candidates:
                candidates.sort(
                    key=lambda item: item[
                        "source_timestamp"
                    ]
                )

                publish(
                    candidates[0],
                    history,
                    queue,
                    f"{today}:queue:{len(daily)}"
                )

    save_state(history, queue)

    print("=" * 50)
    print("Режим:", MODE)
    print("Опубликовано сегодня:", len([
        item for item in history
        if isinstance(item, dict)
        and item.get("date") == today
        and item.get("status") == "published"
    ]))

    print("Всего в резерве:", len(queue))

    for category, number in counts(queue).items():
        print(
            category,
            f"{number}/{RESERVE[category]}"
        )

    if MODE == "test":
        print("Тест завершён. Отправка отключена.")


if __name__ == "__main__":
    main()
