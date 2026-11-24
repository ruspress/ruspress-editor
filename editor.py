import os, re, sys, json, time, hashlib, subprocess
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from difflib import SequenceMatcher
from html import unescape
import requests
from PIL import Image, ImageDraw, ImageFont
from photos import find_photo

CHANNEL = '@russianpressnews'
HISTORY, QUEUE, APPROVED = map(Path, ('published.json','queue.json','approved.json'))
MODE = os.getenv('EDITOR_MODE','test')
MOSCOW = ZoneInfo('Europe/Moscow')
LIMITS = {'own':4,'federal':8,'regional':8}
RESERVE = {'own':6,'federal':12,'regional':12}
LABELS = {'own':'МАТЕРИАЛЫ РЕДАКЦИИ','federal':'РОССИЯ И МИР','regional':'РЕГИОНЫ И ЭКОНОМИКА'}
ORDER = ['federal','regional','own','federal','regional']*4
MAX_AGE = 86400

def load_json(path):
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    if not isinstance(data,list): raise RuntimeError(str(path))
    return data

def write_json(path,data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)

def git(*args):
    subprocess.run(['git',*args],check=True)

def save_state(history,queue):
    write_json(HISTORY,history); write_json(QUEUE,queue)
    git('add',str(HISTORY),str(QUEUE))
    rc = subprocess.run(['git','diff','--cached','--quiet']).returncode
    if rc == 0: return
    if rc != 1: raise RuntimeError('Git diff failed')
    git('commit','-m','Update editorial state'); git('push','origin','HEAD:main')

def normalize(text):
    return ' '.join(re.findall(r'\w+',text.casefold().replace('ё','е')))

def duplicate(draft,records):
    ids=set(draft.get('source_ids',[])); title=normalize(draft.get('headline',''))
    for old in records:
        if isinstance(old,str):
            if old in ids: return True
            continue
        if ids.intersection(old.get('source_ids',[])): return True
        prev=normalize(old.get('headline',''))
        if title and prev and SequenceMatcher(None,title,prev).ratio()>=.80: return True
    return False

def fresh(d):
    return 0<=time.time()-d.get('source_timestamp',0)<=MAX_AGE

def clean_queue(queue,history):
    result=[]
    for d in queue:
        if fresh(d) and d.get('category') in LIMITS and not duplicate(d,history+result): result.append(d)
    return result

def counts(items):
    return {c:sum(isinstance(d,dict) and d.get('category')==c for d in items) for c in LIMITS}

def refill(queue,history):
    current=counts(queue)
    if all(current[c]>=RESERVE[c] for c in RESERVE): return queue
    env=os.environ.copy(); env['PUBLISH_ENABLED']='false'
    original=HISTORY.read_bytes() if HISTORY.exists() else None
    try:
        write_json(HISTORY,history+queue)
        for p in (Path('drafts.json'),APPROVED): p.unlink(missing_ok=True)
        subprocess.run([sys.executable,'-u','news.py'],env=env,check=True,timeout=240)
        subprocess.run([sys.executable,'-u','publish.py'],env=env,check=True,timeout=360)
        drafts=load_json(APPROVED)
    except Exception as e:
        print('Ошибка пополнения:',type(e).__name__); return queue
    finally:
        if original is None: HISTORY.unlink(missing_ok=True)
        else: HISTORY.write_bytes(original)
    for d in drafts:
        c=d.get('category')
        if c not in RESERVE or current[c]>=RESERVE[c]: continue
        if d.get('fact_check')!='ai_source_review' or not d.get('evidence') or duplicate(d,history+queue): continue
        if not d.get('sources'): continue
        d['source_timestamp']=time.time()-max(int(s.get('age',MAX_AGE)) for s in d['sources'])-600
        d['queued_at']=datetime.now(MOSCOW).isoformat(); d['date']='queue'
        if fresh(d):
            queue.append(d); current[c]+=1; print('Добавлено в резерв:',d['headline'])
    return queue

def font(size,bold=False):
    return ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans'+('-Bold' if bold else '')+'.ttf',size)

def create_card(d):
    im=Image.new('RGB',(1280,720),'#07152F'); draw=ImageDraw.Draw(im)
    for y in range(720): draw.line((0,y,1280,y),fill=(7+y*8//720,21+y*20//720,47+y*45//720))
    draw.polygon([(950,0),(1280,0),(1280,400),(1100,220)],fill='#123D77')
    draw.rectangle((65,55,75,650),fill='#2799ED')
    draw.text((110,60),'РОССИЙСКАЯ ПРЕССА',font=font(34,True),fill='white')
    draw.text((110,125),LABELS[d['category']],font=font(23,True),fill='#60B8FF')
    for size in range(60,29,-2):
        face=font(size,True); lines=[]; line=''
        for word in d['headline'].split():
            candidate=(line+' '+word).strip()
            if draw.textlength(candidate,font=face)>1040 and line: lines.append(line); line=word
            else: line=candidate
        if line: lines.append(line)
        if len(lines)*(size+15)<=350 and all(draw.textlength(x,font=face)<=1040 for x in lines): break
    else: raise RuntimeError('Headline too long')
    for i,line in enumerate(lines): draw.text((110,205+i*(size+15)),line,font=face,fill='white')
    draw.line((110,600,1160,600),fill='#31517A',width=2)
    draw.text((110,625),CHANNEL,font=font(25),fill='#A9D5FF')
    draw.text((840,625),'КАРТОЧКА РЕДАКЦИИ',font=font(20),fill='#A9D5FF')
    path=Path('news-card.jpg'); im.save(path,quality=93); return path

def caption_length(message):
    return len(unescape(re.sub(r'<[^>]+>','',message)).encode('utf-16-le'))//2

def prepare_media(d,history,daily):
    message=d['message']; photo=None; source=None; kind=None
    desired=((len(daily)+1)*7+9)//10
    if sum(bool(x.get('has_photo')) for x in daily)<desired:
        try:
            result=find_photo(d,history)
            if result:
                path,credit,url=result
                candidate=message+'\n\n'+credit
                if caption_length(candidate)<=1024: return candidate,path,url,'commons'
        except Exception as e: print('Ошибка фото:',type(e).__name__)
        if caption_length(message)<=1024:
            try: photo=create_card(d); kind='card'
            except Exception as e: print('Ошибка карточки:',type(e).__name__)
    return message,photo,source,kind

def send_message(message,photo):
    token=os.environ['TELEGRAM_BOT_TOKEN']
    try:
        base=f'https://api.telegram.org/bot{token}/'
        if photo:
            with open(photo,'rb') as f:
                r=requests.post(base+'sendPhoto',data={'chat_id':CHANNEL,'caption':message,'parse_mode':'HTML'},files={'photo':f},timeout=45)
        else:
            r=requests.post(base+'sendMessage',json={'chat_id':CHANNEL,'text':message,'parse_mode':'HTML','link_preview_options':{'is_disabled':True}},timeout=30)
        data=r.json()
        if not data.get('ok'): raise RuntimeError('Telegram rejected')
        return data['result']['message_id']
    except Exception: raise RuntimeError('Ошибка Telegram. Проверь запись pending и канал.') from None

def daily_records(history,now):
    return [x for x in history if isinstance(x,dict) and x.get('date')==now.date().isoformat() and x.get('status')=='published']

def candidate(queue,history):
    now=datetime.now(MOSCOW); minute=now.hour*60+now.minute
    if not 480<=minute<1380: return None
    daily=daily_records(history,now); due=min(20,(minute-480)//45+1)
    if len(daily)>=due: return None
    used=counts(daily); wanted=counts([{'category':c} for c in ORDER[:due]])
    options=[d for d in queue if fresh(d) and used[d['category']]<min(LIMITS[d['category']],wanted[d['category']])]
    return min(options,key=lambda d:d['source_timestamp']) if options else None

def publish(d,history,queue):
    now=datetime.now(MOSCOW); daily=daily_records(history,now)
    message,photo,source,kind=prepare_media(d,history,daily)
    # Recheck time and eligibility after network requests.
    if candidate(queue,history) is not d: return False
    now=datetime.now(MOSCOW)
    record={'date':now.date().isoformat(),'slot':f'{now.date()}:queue:{len(daily)}','category':d['category'],'headline':d['headline'],'source_ids':d['source_ids'],'status':'pending','has_photo':bool(photo),'photo_source':source,'photo_kind':kind,'created_at':now.isoformat(),'fingerprint':hashlib.sha256(message.encode()).hexdigest()}
    history.append(record); save_state(history,queue)
    record['message_id']=send_message(message,photo); record['status']='published'; record['published_at']=datetime.now(MOSCOW).isoformat()
    queue.remove(d); save_state(history,queue)
    print('SUCCESS: новость опубликована',record['message_id'],'Изображение:',kind)
    return True

def main():
    if MODE not in ('test','single','auto'): raise SystemExit('Неизвестный режим')
    history=load_json(HISTORY); queue=load_json(QUEUE)
    if any(isinstance(x,dict) and x.get('status')=='pending' for x in history): raise SystemExit('Незавершённая отправка. Проверь канал.')
    queue=clean_queue(queue,history); sent=False
    if MODE!='test':
        d=candidate(queue,history)
        if d: sent=publish(d,history,queue)
    if not (MODE=='single' and sent):
        queue=clean_queue(refill(queue,history),history)
        if MODE!='test' and not sent:
            d=candidate(queue,history)
            if d: sent=publish(d,history,queue)
    save_state(history,queue)
    print('Режим:',MODE,'Опубликовано сегодня:',len(daily_records(history,datetime.now(MOSCOW))))
    print('Всего в резерве:',len(queue))
    for c,n in counts(queue).items(): print(c,f'{n}/{RESERVE[c]}')
    if MODE=='test':
        # Exercise media selection without sending anything.
        for d in queue:
            if find_photo(d,history):
                print('Тест Commons: фото найдено'); break
        else: print('Тест Commons: подходящее фото не найдено')
        print('Тест завершён. Отправка отключена.')

if __name__=='__main__': main()
