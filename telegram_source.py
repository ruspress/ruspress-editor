"""Collect single-media posts from the public channel preview."""
import hashlib
import re
from datetime import datetime
from time import time

import requests
from lxml import html

NAME = "СОЛОВЬЁВ LIVE"
CHANNEL = "SolovievLive"
FEED = f"https://t.me/s/{CHANNEL}"
POST = re.compile(r"https://t\.me/SolovievLive/([1-9][0-9]*)", re.I)


def parse_posts(page, now):
    result = []
    for node in html.fromstring(page).xpath('//*[@data-post]'):
        link = "https://t.me/" + node.get("data-post")
        if not POST.fullmatch(link):
            continue
        captions = node.find_class("tgme_widget_message_text")
        if not captions or node.find_class("tgme_widget_message_grouped_wrap"):
            continue
        kind = None
        if node.find_class("tgme_widget_message_video_wrap"):
            kind = "video"
        elif node.find_class("tgme_widget_message_photo_wrap"):
            kind = "photo"
        if not kind:
            continue
        for br in captions[0].xpath(".//br"):
            br.tail = "\n" + (br.tail or "")
        caption = captions[0].text_content().strip()
        dates = node.xpath('.//time/@datetime')
        if not dates or len(caption) < 80:
            continue
        try:
            timestamp = datetime.fromisoformat(dates[-1]).timestamp()
        except ValueError:
            continue
        if not 0 <= now - timestamp <= 86400:
            continue
        result.append(dict(id=hashlib.sha256(link.lower().encode()).hexdigest(),
                           source=NAME, title=caption.splitlines()[0][:140],
                           summary=caption[:600], link=link, age=int(now - timestamp),
                           media_kind=kind, timestamp=timestamp))
    return result


def collect(url=FEED):
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return parse_posts(response.content.decode("utf-8"), time())
