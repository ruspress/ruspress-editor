import re
from html import escape, unescape
from pathlib import Path
from urllib.parse import urlparse
from io import BytesIO
import requests
from PIL import Image

# Narrow, generic subjects only; never imply a photo depicts the event.
TOPICS = [
    (r'аэропорт|авиарейс', 'airport terminal interior photograph'),
    (r'урожа|аграри|посев', 'agricultural field photograph'),
    (r'снегопад|метел', 'snow covered street photograph'),
    (r'железнодорож|поезд', 'railway tracks photograph'),
    (r'библиотек', 'library bookshelves photograph'),
    (r'солнечн.*энерг', 'solar panels photograph'),
]
LICENSES = {'CC BY 4.0':'https://creativecommons.org/licenses/by/4.0/', 'CC BY-SA 4.0':'https://creativecommons.org/licenses/by-sa/4.0/', 'CC0':'https://creativecommons.org/publicdomain/zero/1.0/'}
HEADERS = {'User-Agent':'RusPressEditor/7.0 (https://ruspress.ru/)'}

def plain(s):
    return ' '.join(unescape(re.sub(r'<[^>]*>', ' ', s or '')).split())

def find_photo(draft, history):
    query = next((q for pattern,q in TOPICS if re.search(pattern,draft['headline'].lower())),None)
    if not query:
        return None
    used = {x.get('photo_source') for x in history if isinstance(x,dict)}
    try:
        response = requests.get('https://commons.wikimedia.org/w/api.php', params={'action':'query','generator':'search','gsrsearch':query,'gsrnamespace':6,'gsrlimit':8,'prop':'imageinfo','iiprop':'url|mime|extmetadata','iiurlwidth':1200,'format':'json'},headers=HEADERS,timeout=10)
        response.raise_for_status()
        for page in response.json().get('query',{}).get('pages',{}).values():
            info = page.get('imageinfo',[{}])[0]
            meta = info.get('extmetadata',{})
            val = lambda k: plain(meta.get(k,{}).get('value',''))
            license = val('LicenseShortName')
            author = val('Artist')
            source = info.get('descriptionurl','')
            url = info.get('thumburl','')
            if license not in LICENSES or not author or len(author)>100 or val('Restrictions') or val('Permission'):
                continue
            if source in used or urlparse(source).hostname!='commons.wikimedia.org' or urlparse(source).scheme!='https':
                continue
            if urlparse(url).hostname!='upload.wikimedia.org' or urlparse(url).scheme!='https' or info.get('mime')!='image/jpeg':
                continue
            with requests.get(url,headers=HEADERS,timeout=12,stream=True,allow_redirects=False) as r:
                if r.status_code!=200: continue
                data=bytearray()
                for chunk in r.iter_content(65536):
                    data.extend(chunk)
                    if len(data)>8_000_000: break
            if len(data)>8_000_000: continue
            with Image.open(BytesIO(data)) as im:
                if im.width<500 or im.height<300 or max(im.size)/min(im.size)>5: continue
                im.verify()
            path=Path('commons-photo.jpg'); path.write_bytes(data)
            credit=f'Иллюстративное фото (не с места события): {escape(author)} · <a href="{escape(source,quote=True)}">Wikimedia Commons</a> · <a href="{LICENSES[license]}">{license}</a>'
            return path,credit,source
    except Exception as e:
        print('Фото Commons недоступно:',type(e).__name__)
    return None
