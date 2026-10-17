"""Accès IORT et inventaire annuel ; téléchargements confiés au serveur JORT."""
import json
import re
import time
import urllib.parse
import urllib.request
import http.cookiejar
import argparse
import hashlib
import io
import os
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://www.iort.gov.tn'
START = BASE + '/WD120AWP/WD120Awp.exe/CONNECT/SITEIORT'

class Page(HTMLParser):
    def __init__(self, raw, url):
        super().__init__()
        self.url=url; self.action=''; self.inputs={}; self.links=[]; self.selects={}
        self.current_link=None; self.current_select=None; self.current_option=None
        self.text=[]
        self.html=raw.decode('utf-8',errors='replace')
        self.feed(self.html)
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag=='form': self.action=urllib.parse.urljoin(self.url,a.get('action',''))
        if tag=='input' and a.get('name'): self.inputs[a['name']]=a.get('value','')
        if tag=='a':
            self.current_link=dict(a,text='');self.links.append(self.current_link)
        if tag=='select':
            self.current_select=a.get('name');self.selects[self.current_select]=[]
        if tag=='option' and self.current_select:
            self.current_option=dict(a,text='');self.selects[self.current_select].append(self.current_option)
    def handle_endtag(self,tag):
        if tag=='a':self.current_link=None
        if tag=='select':self.current_select=None;self.current_option=None
        if tag=='option':self.current_option=None
    def handle_data(self,text):
        self.text.append(text)
        if self.current_link is not None:self.current_link['text']+=text
        if self.current_option is not None:self.current_option['text']+=text

class Client:
    def __init__(self):
        self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.page=None
    def read(self,url,data=None):
        req=urllib.request.Request(url,data=urllib.parse.urlencode(data).encode() if data is not None else None,
                                   headers={'User-Agent':'JORT-Private-Research/0.1'})
        with self.opener.open(req,timeout=45) as r:
            raw=r.read();url=r.url
        if raw.startswith(b'%PDF-'): return raw,url
        self.page=Page(raw,url)
        return self.page
    def click(self,button,fields=None):
        data=self.page.inputs.copy()
        for name,options in self.page.selects.items():
            selected=next((o for o in options if 'selected' in o),options[0] if options else {})
            data[name]=selected.get('value',selected.get('text',''))
        data.update(WD_BUTTON_CLICK_=button,WD_ACTION_='')
        data.update(fields or {})
        return self.read(self.page.action,data)

class PDFMismatch(ValueError):
    """PDF valide, mais correspondant à une autre ligne du catalogue IORT."""

class IortSessionError(ValueError):
    """La session IORT a renvoyé une page au lieu du PDF demandé."""

def save_json(path, value):
    atomic_write_bytes(path, json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8'))

def atomic_write_bytes(path, payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temp_name=tempfile.mkstemp(prefix=f'.{path.stem}-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(12):
            try:
                os.replace(temp_name,path)
                break
            except PermissionError:
                if attempt==11:
                    raise
                time.sleep(min(.2*2**attempt,2.5))
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

def open_year(year=2024):
    c=Client();c.read(START)
    for text in ['Français','Journal annonces légales','Recherche','Année journal']:
        link=next(a for a in c.page.links if a['text'].strip()==text)
        c.click(link['name'])
        time.sleep(.4)
    choices=[(name,o) for name,options in c.page.selects.items() for o in options]
    found=next(((name,o) for name,o in choices if o['text'].strip()==str(year)),None)
    if not found:
        offered=sorted({o['text'].strip() for _,o in choices if re.fullmatch(r'\d{4}',o['text'].strip())})
        raise ValueError(f"Année {year} non proposée par l'IORT (années affichées : {', '.join(offered) or 'aucune'}).")
    field,option=found
    c.click(field,{field:option['value']})
    return c

def rows(page,year=2024):
    grouped={}
    for a in page.links:
        href=a.get('href','')
        if 'WD_ACTION_=CLICTABLE;A5' in href:
            grouped.setdefault(href,[]).append(a['text'].strip())
    out=[]
    page.row_errors=[]
    for href,values in grouped.items():
        number=next((int(v) for v in values if v.isdigit()),None)
        date=next((v for v in values if re.fullmatch(rf'\d{{2}}/\d{{2}}/{year}',v)),None)
        if number is None or date is None:
            # La dernière page IORT contient aussi des lignes de table vides.
            # Une ligne incomplète ne doit pas effacer les autres résultats.
            if any(values):
                page.row_errors.append('Ligne IORT incomplète : '+' | '.join(values))
            continue
        d,m,y=date.split('/')
        out.append(dict(number=number,date=f'{y}-{m}-{d}',url=urllib.parse.urljoin(BASE,href)))
    return out

def find_entry(year,number):
    """Renouvelle la session IORT et retrouve le lien de téléchargement d'un journal."""
    client=open_year(year)
    pages_seen=set()
    while True:
        page_rows=rows(client.page,year)
        signature=tuple(entry['number'] for entry in page_rows)
        if signature in pages_seen:
            raise ValueError('Pagination répétée pendant le renouvellement de session IORT.')
        pages_seen.add(signature)
        for entry in page_rows:
            if entry['number']==number:
                return client,entry
        nxt=next((a for a in client.page.links if a['text'].strip()=='>' and 'SCROLLTABLE' in a.get('href','')),None)
        if not nxt:
            break
        client.read(urllib.parse.urljoin(BASE,nxt['href']))
    raise LookupError(f"Journal n° {number} introuvable dans le catalogue IORT de {year}.")

def verify_pdf(raw,entry):
    from pypdf import PdfReader
    try:
        from .importer_echantillon import MOIS
        from .texte_pdf import extract_page
    except ImportError:
        from importer_echantillon import MOIS
        from texte_pdf import extract_page
    if not raw.startswith(b'%PDF-') or b'%%EOF' not in raw[-1024:]:
        raise ValueError('Document incomplet ou non PDF')
    reader=PdfReader(io.BytesIO(raw))
    first,ocr_warnings=extract_page(reader,0)
    year=int(entry['date'][:4])
    match=re.search(r'(\d{1,2})\s+('+'|'.join(MOIS)+rf')\s+{year}\s+N[°º]\s*(\d+)',first,re.I)
    if not match:
        extra=(' '+ocr_warnings[0]) if ocr_warnings else ''
        raise ValueError(f'Date/numéro français {year} introuvables dans le PDF.{extra}')
    day,month,number=match.groups()
    date=f'{year}-{MOIS[month.lower()]:02d}-{int(day):02d}'
    if int(number)!=entry['number'] or date!=entry['date']:
        raise PDFMismatch(f'PDF reçu : n° {number}, {date} ; attendu : n° {entry["number"]}, {entry["date"]}')
    return dict(sha256=hashlib.sha256(raw).hexdigest(),pages=len(reader.pages))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year',type=int,default=2024,help='Année du journal (par défaut : 2024).')
    parser.add_argument('--download',action='store_true',help='Collecter les PDF manquants ; sans cette option, inventaire seul.')
    args=parser.parse_args()
    if not 1956<=args.year<=datetime.now().year: parser.error('--year doit être entre 1956 et l’année actuelle.')
    if args.download:
        # Un seul moteur de téléchargement, partagé par le site et les scripts.
        # Ne jamais effectuer un second POST si la réponse est incertaine.
        api='http://127.0.0.1:8765'
        try:
            with urllib.request.urlopen(api+'/api/health',timeout=5) as response:
                if json.load(response).get('service')!='jort-local':
                    raise ValueError('Le port 8765 ne correspond pas au serveur JORT.')
            body=json.dumps(dict(action='download',years=[args.year],limit=0)).encode('utf-8')
            request=urllib.request.Request(api+'/api/collecte/job',data=body,method='POST',
                headers={'Content-Type':'application/json','X-JORT-Local':'1','Origin':api})
            with urllib.request.urlopen(request,timeout=20) as response:
                lane=json.load(response)['job']['lanes']['collection']
            print(json.dumps({k:lane.get(k) for k in ('id','state','years','action')},ensure_ascii=False))
        except Exception as exc:
            parser.error(f'Le serveur doit être lancé avec lancer-jort.ps1. Réponse de collecte non confirmée : {exc}. Vérifiez les tâches avant toute relance.')
        return
    year=args.year
    path=ROOT/f'data/inventaire-{year}-fr.json'
    previous=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    saved={v['number']:v for v in previous.get('journals',[])}
    c=open_year(year)
    expected=int(c.page.inputs['A58'])
    manifest=dict(year=year,language='fr',expected=expected,inventory_complete=False,
                  entrypoint=START,updated_at=datetime.now(timezone.utc).isoformat(),journals=[])
    visited=set();failures=0
    while True:
        page=c.page
        for entry in rows(page,year):
            number=entry['number']
            if number in visited:raise ValueError('Pagination répétée : arrêt de sécurité')
            visited.add(number)
            old=saved.get(number,{})
            entry.update(status='inventorie')
            candidates=[]
            if old.get('path'):candidates.append(ROOT/old['path'])
            candidates.extend((ROOT/'dist/sources').glob(f'jal-{year}-{number:03d}-{entry["date"]}-fr-*.pdf'))
            candidates.extend((ROOT/'data/downloads').glob(f'jal-{year}-{number:03d}-{entry["date"]}-fr.pdf'))
            if year==2024 and number==151:candidates.append(ROOT/'documents/2024/fr/jal-2024-151-2024-12-31-fr.pdf')
            for local in candidates:
                if local.exists():
                    try:
                        info=verify_pdf(local.read_bytes(),entry)
                        entry.update(info,path=local.relative_to(ROOT).as_posix(),status='telecharge',
                                     download_url=old.get('download_url'),downloaded_at=old.get('downloaded_at'),
                                     provenance=old.get('provenance','fichier_local_verifie'))
                        break
                    except Exception:pass
            if args.download and entry['status']!='telecharge':
                refreshed_entry=None
                for attempt in range(2):
                    raw=None
                    download_client=c
                    download_entry=entry
                    try:
                        if refreshed_entry is not None:
                            download_client,download_entry=refreshed_entry
                        result=download_client.read(download_entry['url'])
                        if not isinstance(result,tuple):raise IortSessionError('Réponse HTML au lieu du PDF (session expirée ?)')
                        raw,url=result
                        info=verify_pdf(raw,download_entry)
                        target=ROOT/'data/downloads'/f'jal-{year}-{number:03d}-{entry["date"]}-fr.pdf'
                        target.parent.mkdir(parents=True,exist_ok=True)
                        atomic_write_bytes(target,raw)
                        entry.update(info,path=target.relative_to(ROOT).as_posix(),status='telecharge',
                                     download_url=url,downloaded_at=datetime.now(timezone.utc).isoformat(),provenance='iort')
                        entry.pop('error',None);failures=0
                        break
                    except Exception as exc:
                        error=str(exc)
                        entry.update(status='erreur',error=error)
                        if attempt==0 and isinstance(exc,(PDFMismatch,IortSessionError)):
                            try:
                                refreshed_entry=find_entry(year,number)
                                entry.update(refreshed_entry[1])
                                entry['status']='inventorie'
                                entry.pop('error',None)
                            except Exception as refresh_exc:
                                error=f'{exc} ; renouvellement de session impossible : {refresh_exc}'
                                entry.update(status='erreur',error=error)
                                break
                        elif attempt==0:
                            time.sleep(2)
                if entry['status']=='erreur':failures+=1
            manifest['journals'].append(entry)
            save_json(path,manifest)
            print(f'{len(visited)}/{expected} - JAL {number}: {entry["status"]}',flush=True)
            if failures>=3:raise RuntimeError('Trois échecs consécutifs : relancer pour reprendre après contrôle du site.')
            if args.download:time.sleep(.7)
        nxt=next((a for a in page.links if a['text'].strip()=='>' and 'SCROLLTABLE' in a.get('href','')),None)
        if not nxt:break
        c.read(urllib.parse.urljoin(BASE,nxt['href']))
        if len(visited)>=expected:break
    manifest['inventory_complete']=len(visited)==expected
    manifest['downloaded']=sum(v['status']=='telecharge' for v in manifest['journals'])
    save_json(path,manifest)
    print(json.dumps({'expected':expected,'inventoried':len(visited),'downloaded':manifest['downloaded']},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
