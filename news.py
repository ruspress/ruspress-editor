import feedparser
import calendar
import time

SOURCES = {
    "Российская пресса": "https://ruspress.ru/feed/",
    "ТАСС": "https://tass.ru/rss/v2.xml",
    "РИА Новости": "https://ria.ru/export/rss2/index.xml",
    "Интерфакс": "https://www.interfax.ru/rss.asp",
}

news = []
seen = set()

for source, url in SOURCES.items():
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:15]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            date = entry.get("published_parsed")
            if not title or not link or not date:
                continue
            age = time.time() - calendar.timegm(date)
            if not 0 <= age <= 86400:
                continue
            key = title.casefold()
            if key in seen:
                continue
            seen.add(key)
            news.append((age, source, title, link))
    except Exception:
        print(f"Ошибка источника: {source}")

news.sort()

print(f"Найдено новостей: {len(news)}")

for age, source, title, link in news[:20]:
    print(f"\n{title}\nИсточник: {source}\n{link}")

if not news:
    raise SystemExit("Нет свежих новостей")
