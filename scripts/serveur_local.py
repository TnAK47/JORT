"""Site et collecte privés, lancés uniquement sur la machine locale."""
import csv
import copy
import gzip
import hashlib
import hmac
import io
import json
import os
import queue
import re
import shutil
import sqlite3
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.error
from contextlib import closing, nullcontext
from collections import deque
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from email.parser import BytesParser
from email.policy import default as email_policy

from collecter_2024 import open_year, rows, verify_pdf, save_json as _save_json, atomic_write_bytes, find_entry, PDFMismatch, IortSessionError, BASE
from importer_echantillon import MOIS, VERSION as IMPORT_VERSION
from agregation_base import reconstruire
from construire_base_2024 import EXTRACTION_VERSION, synchroniser_sqlite
from rapprochement_identites import candidats_phonetiques, cle_prenom_nom, normaliser_nom_personne, occurrences_distinctes, PHONETIC_STRATEGY, STRATEGY_VERSION
from erreurs_traitement import erreur_temporaire, ressource_indisponible
from chemins_drive import google_drive_jort, resolve_drive_reference, check_google_drive_access

ROOT = Path(__file__).resolve().parents[1]
BUILD = hashlib.sha256(b''.join(p.read_bytes() for p in sorted((ROOT/'scripts').glob('*.py')))).hexdigest()
context = threading.local()
DOWNLOADS = ROOT / 'data' / 'downloads'


class BatchPause(Exception):
    """Arrêt temporaire d'un batch quand une ressource locale est indisponible."""

def recoverable_index_error(exc):
    """Ne relance que les délais réseau ou le verrouillage SQLite temporaire."""
    return erreur_temporaire(exc)

def year():
    return getattr(context, 'year', 2024)

def select_year(value):
    value = int(value)
    if not 1956 <= value <= datetime.now().year:
        raise ValueError('Année hors de la période 1956 à aujourd’hui')
    context.year = value
    for folder in (store(), archive(), inbox(), DOWNLOADS):
        folder.mkdir(parents=True, exist_ok=True)

def store():
    return ROOT / f'data/{year()}/logs'

def archive():
    return ROOT / f'documents/PDF/{year()}/originaux'

def google_drive_root():
    # JORT_GOOGLE_DRIVE_ROOT désigne « Mon Drive », pas son dossier JORT.
    return google_drive_jort()

def resolve_managed_pdf(relative):
    """Résoudre une référence projet ou Drive en empêchant les sorties de dossier."""
    value = str(relative or '').replace('\\', '/')
    if not value or value.startswith('/') or re.match(r'^[A-Za-z]:', value):
        return None
    parts = Path(value).parts
    if any(part in ('..', '') for part in parts):
        return None
    if value.lower().startswith('drive/'):
        try:
            target = resolve_drive_reference(value)
        except (ValueError, OSError):
            return None
        root = google_drive_root().resolve()
    else:
        root = ROOT.resolve()
        target = (root / Path(value)).resolve()
    try:
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    return target

def managed_pdf_reference(path):
    target = Path(path).resolve()
    for root, prefix in ((ROOT.resolve(), ''), (google_drive_root().resolve(), 'drive/')):
        try:
            return prefix + target.relative_to(root).as_posix()
        except ValueError:
            continue
    raise ValueError('Le PDF est en dehors des dossiers JORT autorisés.')

def managed_file_state(path):
    """Distinguer un fichier absent d’un lecteur Drive illisible par manque d’accès."""
    try:
        return 'present' if path and Path(path).is_file() else 'missing'
    except OSError:
        return 'unavailable'

def managed_file_exists(path):
    return managed_file_state(path)=='present'

def assert_drive_inventory_access(manifest_data):
    """Ne jamais interpréter un Drive illisible comme un lot de PDF manquants."""
    root=google_drive_root()
    try:
        root=check_google_drive_access(require_jort_folder=False)
        for row in manifest_data.get('journals',[]):
            number=row.get('number')
            date=row.get('date')
            if not number or not date:
                continue
            reference=str(row.get('path') or '')
            candidate=(resolve_managed_pdf(reference) if reference.startswith('drive/')
                       else root/str(year())/f'jal-{year()}-{int(number):03d}-{date}-fr.pdf')
            if not candidate:
                continue
            try:
                candidate.stat()
            except FileNotFoundError:
                continue  # Absent confirmé : le téléchargement peut être proposé.
    except OSError as exc:
        raise BatchPause(f'Google Drive inaccessible ({root}) : {exc}. Aucun nouveau téléchargement lancé; rétablissez l’accès puis relancez.') from exc

def pdf_sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()

def inbox():
    return ROOT / f'documents/PDF/{year()}/a-importer'

def manifest_path():
    return ROOT / f'data/inventaire-{year()}-fr.json'

def dataset():
    return ROOT / f'data/{year()}/base.json'

def year_status_snapshot(selected_year):
    """Résumé compact de l’état annuel, lu dans SQLite sans charger les textes."""
    selected_year=int(selected_year)
    db_path=ROOT/f'data/{selected_year}/jort-{selected_year}-fr.sqlite'
    if not db_path.is_file():
        return read_snapshot(ROOT/f'data/{selected_year}/base.json',{})
    try:
        stamp=(db_path.stat().st_mtime_ns,db_path.stat().st_size)
        cached=year_status_cache.get(selected_year)
        if cached and cached[0]==stamp and time.monotonic()-cached[1]<3:
            return cached[2]
        uri=db_path.resolve().as_uri()+'?mode=ro'
        with closing(sqlite3.connect(uri,uri=True,timeout=0.75)) as db:
            db.row_factory=sqlite3.Row
            sources=[dict(row) for row in db.execute('''
                SELECT s.id,s.number,s.date,s.sha256,s.pdf,s.canonical_pdf,s.extractor_version,
                       s.importer_version,s.extraction_state,
                       COUNT(a.id) AS announcement_count,
                       SUM(CASE WHEN a.indexation_state='terminee' AND COALESCE(a.indexation_error,'')=''
                                THEN 1 ELSE 0 END) AS processed_count
                FROM sources s LEFT JOIN announcements a ON a.source_id=s.id
                GROUP BY s.id''')]
            totals=db.execute('''SELECT
                (SELECT COUNT(*) FROM announcements),
                (SELECT COALESCE(SUM(category='a_classer'),0) FROM announcements),
                (SELECT COUNT(*) FROM relations),
                (SELECT COALESCE(SUM(type='personne'),0) FROM entities),
                (SELECT COALESCE(SUM(type!='personne'),0) FROM entities)''').fetchone()
        bilan=read_snapshot(ROOT/f'data/{selected_year}/bilan-{selected_year}.json',{})
        result=dict(sources=sources,source_summary=True,stats=dict(
            announcements=int(totals[0]),unclassified=int(totals[1]),relations=int(totals[2]),
            persons=int(totals[3]),organizations=int(totals[4]),pages=int(bilan.get('stats',{}).get('pages',0) or 0)))
        year_status_cache[selected_year]=(stamp,time.monotonic(),result)
        if len(year_status_cache)>8:
            year_status_cache.pop(next(iter(year_status_cache)))
        return result
    except (OSError,sqlite3.Error):
        cached=year_status_cache.get(selected_year)
        if cached:
            return cached[2]
        # Compatibilité avec une base absente ou temporairement verrouillée.
        return read_snapshot(ROOT/f'data/{selected_year}/base.json',{})

select_year(2024)
lock = threading.RLock()
write_lock = threading.RLock()
review_lock = threading.RLock()
inventory_lock = threading.RLock()
wake = threading.Condition(lock)
shutdown_requested = threading.Event()
lane_cancel = {name: threading.Event() for name in ('collection','indexation','maintenance')}

class Cancellation:
    def is_set(self):
        return shutdown_requested.is_set() or lane_cancel.get(getattr(context,'lane','indexation'),lane_cancel['indexation']).is_set()
    def set(self):
        for event in lane_cancel.values(): event.set()
    def clear(self):
        for event in lane_cancel.values(): event.clear()

cancel = Cancellation()
# Tous les téléchargements PDF IORT de ce serveur partagent cette file,
# Une seule requête IORT PDF à la fois, sans temporisation artificielle.
iort_pdf_lock = threading.Lock()

def read_iort_pdf(client, url, number=None):
    """Sérialise les requêtes PDF IORT sans pause fixe."""
    with iort_pdf_lock:
        if cancel.is_set():
            return None
        return client.read(url)

job_path=ROOT/'data/collecte-traitement.json'
job=json.loads(job_path.read_text(encoding='utf-8')) if job_path.exists() else {}
process = None
job_thread = None
lane_threads = {}
snapshot_cache = {}
year_status_cache = {}
overview_cache = {}
overview_lock = threading.Lock()


def annual_overview():
    """Bilan des références enregistrées, sans ouvrir les PDF ni interroger Drive."""
    values = []
    with overview_lock:
        for selected_year in range(datetime.now().year, 1955, -1):
            manifest_file = ROOT / f'data/inventaire-{selected_year}-fr.json'
            db_file = ROOT / f'data/{selected_year}/jort-{selected_year}-fr.sqlite'
            def stamp(path):
                try:
                    stat = path.stat()
                    return (stat.st_mtime_ns, stat.st_size)
                except OSError:
                    return None
            signature = (stamp(manifest_file), stamp(db_file))
            cached = overview_cache.get(selected_year)
            if cached and cached[0] == signature:
                result = dict(cached[1])
            else:
                inventory = read_json(manifest_file, {})
                rows_by_number = {int(row['number']): row for row in inventory.get('journals', [])}
                sources = {}
                unavailable = False
                if signature[1]:
                    try:
                        with closing(sqlite3.connect(db_file.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.5)) as db:
                            db.row_factory = sqlite3.Row
                            sources = {int(row['number']): dict(row) for row in db.execute('''
                                SELECT s.number,s.date,s.sha256,s.pdf,s.canonical_pdf,s.extractor_version,
                                       s.importer_version,s.extraction_state,COUNT(a.id) AS announcement_count,
                                       SUM(CASE WHEN a.indexation_state='terminee' AND COALESCE(a.indexation_error,'')=''
                                                THEN 1 ELSE 0 END) AS processed_count
                                FROM sources s LEFT JOIN announcements a ON a.source_id=s.id GROUP BY s.id''')}
                    except (OSError, sqlite3.Error):
                        unavailable = True
                elif (ROOT / f'data/{selected_year}/base.json').is_file():
                    # Les anciennes bases JSON seront examinées dans le détail annuel.
                    unavailable = True
                known = set(rows_by_number) | set(sources)
                recorded = analyzed = current = local = drive = missing = 0
                error_numbers = set()
                for number in known:
                    row = rows_by_number.get(number, {})
                    source = sources.get(number, {})
                    reference = (source.get('canonical_pdf') or source.get('pdf') or
                                 (row.get('path') if row.get('status') == 'telecharge' else ''))
                    if reference:
                        recorded += 1
                        if str(reference).replace('\\', '/').startswith('drive/'):
                            drive += 1
                        else:
                            local += 1
                    elif row:
                        missing += 1
                    complete, up_to_date = source_checkpoint_state(source, row or source, compact=True)
                    analyzed += int(complete)
                    current += int(up_to_date and bool(reference))
                    if row.get('error') or row.get('status') == 'erreur':
                        error_numbers.add(number)
                result = dict(year=selected_year, expected=inventory.get('expected'), known=len(known),
                              inventory_complete=bool(inventory.get('inventory_complete')), recorded=recorded,
                              local=local, drive=drive, analyzed=None if unavailable else analyzed,
                              pending=None if unavailable else max(0, recorded-current), missing=missing,
                              error_numbers=sorted(error_numbers), recorded_numbers=sorted(known),
                              has_data=bool(signature[0] or signature[1] or unavailable),
                              detail_required=unavailable)
                # Un verrou SQLite temporaire doit être réessayé au prochain passage.
                if not unavailable:
                    overview_cache[selected_year] = (signature, dict(result))
            task_errors = {int(task.get('number') or key.split(':')[-1])
                           for key, task in list(job.get('file_tasks', {}).items())
                           if str(key).startswith(f'{selected_year}:') and task.get('state') == 'erreur'}
            result['errors'] = len(set(result.pop('error_numbers', [])) | task_errors)
            result.pop('recorded_numbers', None)
            values.append(result)
    return dict(years=values, updated_at=now(), availability='recorded',
                note='Bilan des références enregistrées. Ouvrir une année vérifie la présence réelle des PDF locaux et sur Drive.')
startup_report = {}
projection_thread = None
PUBLICATION_INTERVAL_SECONDS = 60
PUBLICATION_JOURNAL_LIMIT = 10
MAX_PENDING_PDFS = 20
publication_last_attempt = 0.0
full_publication_cache = None
full_publication_revision = None


def save_json(path, value):
    # A stale scan must not replace a PDF already committed by the collector.
    path=Path(path)
    if path.name.startswith('inventaire-'):
        with inventory_lock:
            latest=read_json(path,{}) if path.exists() else {}
            old={str(row['number']):row for row in latest.get('journals',[])}
            fresh={str(row['number']):dict(row) for row in value.get('journals',[])}
            for key,row in old.items():
                incoming=fresh.get(key)
                if incoming is None:
                    fresh[key]=row
                elif row.get('status')=='telecharge' and row.get('path'):
                    recorded=resolve_managed_pdf(row['path'])
                    moved_to_drive=(str(row.get('path','')).startswith('drive/')
                                    and not str(incoming.get('path','')).startswith('drive/')
                                    and row.get('sha256') and row.get('sha256')==incoming.get('sha256'))
                    if managed_file_state(recorded) in ('present','unavailable') and (incoming.get('status')!='telecharge' or moved_to_drive or str(row.get('downloaded_at',''))>str(incoming.get('downloaded_at',''))):
                        fresh[key]={**incoming,**row}
            value=dict(value,journals=sorted(fresh.values(),key=lambda row:int(row['number']),reverse=True))
            _save_json(path,value)
    else:
        _save_json(path,value)


def now():
    return datetime.now(timezone.utc).isoformat()

def dataset_revision(data):
    snapshot=dict(data)
    snapshot.pop('revision',None)
    payload=json.dumps(snapshot,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()

def read_json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

def read_snapshot(path, default):
    """Vue en lecture seule, relue seulement après un changement sur disque."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        snapshot_cache.pop(str(path), None)
        return default
    stamp = (stat.st_mtime_ns, stat.st_size)
    key = str(path)
    cached = snapshot_cache.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    value = read_json(path, default)
    # Ne garder que quelques années et le corpus partagé en mémoire.
    if len(snapshot_cache) >= 8:
        snapshot_cache.pop(next(iter(snapshot_cache)))
    snapshot_cache[key] = (stamp, value)
    return value

archived_tail_cache={}


def read_jsonl_tail(path, count=80, max_bytes=262144):
    """Lit seulement la fin du journal pour éviter de charger les logs entiers."""
    if not path.exists():
        if path.name=='evenements.jsonl':
            archive=max(path.parent.glob('evenements*.jsonl.gz'),key=lambda p:p.stat().st_mtime,default=None)
            if archive:return read_jsonl_tail(archive,count,max_bytes)
        return []
    if path.suffix == '.gz':
        key=(str(path),path.stat().st_mtime_ns,count,max_bytes)
        if key in archived_tail_cache:return archived_tail_cache[key]
        lines = deque()
        size = 0
        with gzip.open(path, 'rb') as stream:
            for line in stream:
                lines.append(line)
                size += len(line)
                while len(lines) > count or (size > max_bytes and len(lines) > 1):
                    size -= len(lines.popleft())
        result = []
        for line in lines:
            try: result.append(json.loads(line))
            except (json.JSONDecodeError, UnicodeError): continue
        if len(archived_tail_cache)>=32:archived_tail_cache.pop(next(iter(archived_tail_cache)),None)
        archived_tail_cache[key]=result
        return result
    with path.open('rb') as stream:
        stream.seek(0, os.SEEK_END)
        size=stream.tell()
        stream.seek(max(0,size-max_bytes))
        raw=stream.read().decode('utf-8',errors='replace')
    lines=raw.splitlines()[-count:]
    result=[]
    for line in lines:
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def full_publication():
    global full_publication_cache, full_publication_revision
    marker = read_json(ROOT/'dist/publication.json', {})
    current = marker.get('revision')
    if full_publication_cache is None or full_publication_revision != current:
        full_publication_cache = read_snapshot(ROOT/'dist/data.json', {})
        full_publication_revision = current
    return full_publication_cache

def manifest():
    return read_json(manifest_path(), {'year':year(), 'expected':151 if year()==2024 else None, 'inventory_complete':False, 'journals':[]})

def tickets():
    local=read_snapshot(store()/'interventions.json', {})
    identities=read_snapshot(ROOT/'data/logs/interventions-identites.json', {})
    return {**local, **identities}

def save_tickets(items):
    local={key:value for key,value in items.items() if value.get('kind')!='identite'}
    identities={key:value for key,value in items.items() if value.get('kind')=='identite'}
    save_json(store()/'interventions.json',local)
    save_json(ROOT/'data/logs/interventions-identites.json',identities)

def identity_fingerprint(cin):
    """Empreinte stable non réversible du ticket, protégée par une clé locale."""
    key_path=ROOT/'data/logs/cle-identites.bin'
    if key_path.exists():
        key=key_path.read_bytes()
    else:
        key=secrets.token_bytes(32)
        atomic_write_bytes(key_path,key)
    return hmac.new(key,str(cin).encode('ascii'),hashlib.sha256).hexdigest()[:16]

def log(message, level='info', number=None, task=None, current=None, total=None):
    with lock:
        lane_name=getattr(context,'lane',None)
        lane=job.get('lanes',{}).get(lane_name,{}) if job and lane_name else {}
        run_id=str(lane.get('id',''))
        run_years=lane.get('years') or ([lane.get('year')] if lane.get('year') else [year()])
        event_path=store()/'evenements.jsonl'
        if event_path.exists() and event_path.stat().st_size >= 10*1024*1024:
            stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
            archive_path=store()/f'evenements-{stamp}.jsonl'
            suffix=1
            while archive_path.exists():
                archive_path=store()/f'evenements-{stamp}-{suffix}.jsonl'
                suffix+=1
            os.replace(event_path,archive_path)
        with event_path.open('a', encoding='utf-8') as stream:
            event=dict(time=now(), level=level, message=message, number=number)
            if task is not None:
                event['task']=task
            if current is not None:
                event['current']=current
            if total is not None:
                event['total']=total
            if run_id:
                event.update(run_id=run_id,lane=lane_name,action=lane.get('action'),years=run_years)
            stream.write(json.dumps(event, ensure_ascii=False)+'\n')
        if run_id:
            execution_dir=ROOT/'data/logs/executions'
            execution_dir.mkdir(parents=True,exist_ok=True)
            run_path=execution_dir/f'{run_id}.jsonl'
            with run_path.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(event,ensure_ascii=False)+'\n')
        if job:
            job.update(message=message, current=number if number is not None else current)
            if task is not None:
                job['task']=task
                if task in ('indexation_pdf','indexation_journal','indexation_annonce'):
                    job['index_message']=message
                    if number is not None:
                        job['index_current_number']=number
                elif task in ('telechargement','inventaire'):
                    job['download_message']=message
                    if number is not None:
                        job['download_current_number']=number
            if current is not None:
                job['task_current']=current
                if task in ('indexation_pdf','indexation_journal','indexation_annonce'):
                    job['index_current']=current
                elif task in ('telechargement','inventaire'):
                    job['download_current']=current
            if total is not None:
                job['task_total']=total
                if task in ('indexation_pdf','indexation_journal','indexation_annonce'):
                    job['index_total']=total
                elif task in ('telechargement','inventaire'):
                    job['download_total']=total
            lane=job.get('lanes',{}).get(getattr(context,'lane',''))
            if lane is not None:
                lane.update(message=message,year=year(),current_year=year(),updated_at=now())
                if number is not None: lane['current_number']=number
                if current is not None: lane['current']=current
                if total is not None: lane['total']=total
                if task=='indexation_annonce':
                    if current is not None: lane['announcement_current']=current
                    if total is not None: lane['announcement_total']=total
                if task in ('inventaire','telechargement'):
                    lane['stage']=task
                    lane['completed']=job.get('processed_total',0) if task=='telechargement' else job.get('completed_total',0)
                    if task=='telechargement': lane['download_total']=job.get('download_total',total)
            save_coordinator()

def set_file_task(number, state, error=None):
    key=f'{year()}:{number}' if job.get('lanes') else str(number)
    with lock:
        if not job:
            return
        tasks=job.setdefault('file_tasks',{})
        item=dict(tasks.get(key,{}),year=year(),number=number,state=state,updated_at=now())
        if error:
            item['error']=str(error)
        else:
            item.pop('error',None)
        tasks[key]=item
        save_json(job_path,job)
    detail=f' · {error}' if error else ''
    log(f'Journal n° {number} — {state}{detail}',
        'error' if state=='erreur' else 'info',number,task='indexation_pdf')

def ticket(key, kind, message, **fields):
    with review_lock:
        items = tickets()
        previous = items.get(key, {})
        items[key] = dict(previous, id=key, kind=kind, severity='attention',
                          state=previous.get('state', 'ouvert'), message=message,
                          note=previous.get('note', ''), updated_at=now(), **fields)
        save_tickets(items)

def resolve_missing(number):
    with review_lock:
        items = tickets()
        key = f'missing:{number}'
        if key in items:
            items[key].update(state='resolu', updated_at=now())
            save_tickets(items)

def csv_bytes(items):
    stream = io.StringIO(newline='')
    columns = list(dict.fromkeys(k for row in items for k in row)) or ['message']
    writer = csv.DictWriter(stream, fieldnames=columns, delimiter=';')
    writer.writeheader()
    writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k,v in row.items()} for row in items)
    return ('\ufeff'+stream.getvalue()).encode('utf-8')

def _refresh_tickets():
    data = dict(sources=[],announcements=[],entities=[],relations=[])
    local_source_ids = set()
    local_announcement_ids = set()
    for annual_path in sorted((ROOT/'data').glob('[0-9]*/base.json')):
        annual_data=read_json(annual_path,{})
        if annual_path.parent.name == str(year()):
            local_source_ids.update(s['id'] for s in annual_data.get('sources', []))
            local_announcement_ids.update(a['id'] for a in annual_data.get('announcements', []))
        for key in data:
            data[key].extend(annual_data.get(key,[]))
    sources = {s['id']:s for s in data.get('sources', [])}
    items = tickets()
    changed = False
    announcements = {a['id']: a for a in data.get('announcements', [])}
    evidence_by_entity = {}
    for relation in data.get('relations', []):
        for entity_id in (relation.get('from'), relation.get('to')):
            evidence_by_entity.setdefault((entity_id, relation.get('announcement_id')), relation.get('quote', ''))
    entities_by_cin = {}
    # Réservé à une stratégie future : retourne [] et ne fusionne rien.
    phonetic_candidates = candidats_phonetiques(data.get('entities', []), enabled=False)
    assert not phonetic_candidates, 'La stratégie phonétique doit rester inactive.'
    current_identity_keys=set()
    for entity in occurrences_distinctes(data.get('entities', [])):
        if entity.get('type') == 'personne' and entity.get('cin') and not re.search(r'\b(?:soci[ée]t[ée]|ste|sarl|suarl|sa|matricule|fiscal)\b',str(entity.get('name','')),re.I):
            cin = re.sub(r'\D', '', str(entity['cin']))
            if len(cin)==8:
                entities_by_cin.setdefault(cin, []).append(entity)
    for cin, candidates in entities_by_cin.items():
        if len(candidates) < 2:
            continue
        # Même CIN et même nom normalisé : rapprochement automatique. Les
        # occurrences et leurs annonces sources restent toutes conservées.
        # Un ticket n'est utile que si des noms différents apparaissent dans
        # des annonces distinctes ; une répétition dans la même annonce n'en
        # constitue jamais un.
        name_keys = {cle_prenom_nom(e.get('name')) or normaliser_nom_personne(e.get('name')) for e in candidates}
        name_keys.discard('')
        if len(name_keys) <= 1:
            continue
        # Une répétition dans une annonce ne constitue jamais un conflit à
        # faire valider. Il faut deux graphies distinctes, chacune attestée
        # dans des annonces différentes, pour créer une intervention.
        announcement_ids = {str(entity.get('announcement_id') or '') for entity in candidates}
        cross_announcement_conflict = len(name_keys) > 1 and len(announcement_ids) > 1
        if not cross_announcement_conflict:
            continue
        fingerprint = identity_fingerprint(cin)
        key = f'identity:all:{fingerprint}'
        current_identity_keys.add(key)
        candidate_rows_by_name = {}
        for entity in candidates:
            announcement = announcements.get(entity.get('announcement_id'), {})
            source = sources.get(announcement.get('source_id'), {})
            evidence = evidence_by_entity.get((entity.get('id'), announcement.get('id')), '')
            candidate = dict(entity_id=entity['id'], name=entity.get('name'),
                announcement_id=announcement.get('id'), reference=announcement.get('reference'),
                pages=announcement.get('pages', []), quote=evidence,
                source_pdf=source.get('canonical_pdf') or source.get('pdf'),
                source_url=source.get('source_url'), number=source.get('number'))
            name_key = cle_prenom_nom(entity.get('name')) or normaliser_nom_personne(entity.get('name')) or entity['id']
            grouped = candidate_rows_by_name.setdefault(name_key, dict(name=entity.get('name'), entity_ids=[], occurrences=[]))
            grouped['entity_ids'].append(entity['id'])
            grouped['occurrences'].append(candidate)
        candidate_rows = list(candidate_rows_by_name.values())
        previous = items.get(key, {})
        entity_ids=[entity['id'] for entity in candidates]
        previous_name_keys = {cle_prenom_nom(row.get('name')) or normaliser_nom_personne(row.get('name'))
                              for row in previous.get('candidates', [])}
        unchanged_candidates=(set(previous.get('entity_ids',[]))==set(entity_ids)
            and previous_name_keys==name_keys)
        value = dict(previous, id=key, kind='identite', severity='attention',
            state=previous.get('state', 'ouvert') if unchanged_candidates else 'ouvert',
            note=previous.get('note', ''),
            message=f'{len(name_keys)} identités prénom/nom distinctes portent la même CIN dans plusieurs annonces. Vérifier les preuves avant de rapprocher.',
            candidate_cin_suffix=cin[-2:], entity_ids=entity_ids,
            candidates=candidate_rows, updated_at=previous.get('updated_at',now()))
        value.update(identity_method='exact_cin', strategy_version=STRATEGY_VERSION,
                     phonetic_strategy=PHONETIC_STRATEGY)
        if not unchanged_candidates and previous:
            value.setdefault('decisions',[])
            if previous.get('state') in ('valide','a_corriger') and not previous.get('decisions'):
                value['decisions'].append(dict(state=previous['state'],note=previous.get('note',''),entity_ids=previous.get('entity_ids',[]),revision=previous.get('revision',0),at=previous.get('updated_at')))
            value['candidates_changed']=True
        if value != previous:
            value['updated_at']=now()
        if value != previous:
            items[key] = value
            changed = True
    for key, value in list(items.items()):
        if value.get('kind')=='identite' and key.startswith('identity:') and not key.startswith('identity:all:'):
            value.update(state='resolu',resolution_reason='Ancien regroupement remplacé ; historique conservé.')
            changed=True
            continue
        if value.get('kind')=='identite' and key not in current_identity_keys and value.get('state')!='resolu':
            value.update(state='resolu',resolution_reason='Moins de deux occurrences de personnes valides distinctes ; rapprochement sans objet.',updated_at=now())
            changed=True
    for source in sources.values():
        if source['id'] not in local_source_ids:
            continue
        warnings=[w for w in source.get('warnings',[]) if 'OCR requis' in w or 'Texte court' in w]
        if warnings:
            key='ocr:'+source['id']
            if key not in items:
                items[key]=dict(id=key,kind='extraction',severity='attention',state='ouvert',number=source.get('number'),message='; '.join(warnings),source_pdf=source.get('canonical_pdf') or source.get('pdf'),note='',updated_at=now())
                changed=True
    # Les avertissements ordinaires ne demandent pas une validation humaine.
    for key, value in list(items.items()):
        if key.startswith('announcement:') and value.get('state')=='ouvert' and not value.get('note'):
            del items[key]
            changed = True
    downloaded={r['number'] for r in manifest()['journals'] if r.get('status')=='telecharge'}
    for failure in read_json(ROOT/f'data/{year()}/bilan-{year()}.json', {}).get('coverage', {}).get('errors', []):
        if failure.get('number') not in downloaded:
            continue
        key = 'extraction-error:'+str(failure.get('number'))
        if key not in items:
            items[key] = dict(id=key, kind='extraction', severity='attention', state='ouvert',
                number=failure.get('number'), message=failure.get('error','Extraction impossible'), note='', updated_at=now())
            changed = True
    failed_announcements = {a['id']: a for a in data.get('announcements', [])
                            if a['id'] in local_announcement_ids and a.get('indexation_error')}
    for aid, announcement in failed_announcements.items():
        key = 'indexation-error:'+str(aid)
        if key not in items:
            items[key] = dict(id=key, kind='extraction', severity='attention', state='ouvert',
                announcement_id=aid, message='Échec après trois tentatives : '+announcement['indexation_error'], note='', updated_at=now())
            changed = True
    for key, value in list(items.items()):
        if key.startswith('indexation-error:') and value.get('state') == 'ouvert' and value.get('announcement_id') not in failed_announcements:
            value.update(state='resolu', message='Indexation reprise avec succès.', updated_at=now())
            changed = True
    for fragment in read_json(ROOT/f'data/{year()}/fragments-sans-reference-{year()}.json', []):
        if len(fragment.get('text',''))<1000 or not re.search(r'(raison\s+sociale|capital\s+social|constitution\s+de)',fragment.get('text',''),re.I):
            continue
        key = 'fragment:'+fragment['id']
        if key not in items:
            source = sources.get(fragment.get('source_id'), {})
            items[key] = dict(id=key, kind='extraction', severity='attention', state='ouvert',
                number=source.get('number'), pages=fragment.get('pages', []),
                source_pdf=source.get('pdf'), message='Passage sans référence : vérifier s’il contient une annonce.', note='', updated_at=now())
            changed = True
    if changed:
        save_tickets(items)

def source_checkpoint_state(source, row, announcements=(), compact=False):
    """Séparer l'extraction enregistrée d'une extraction à la version actuelle."""
    if not source or source.get('date')!=row.get('date'):
        return False, False
    if compact:
        complete=(source.get('extraction_state')=='vide_a_verifier' or
                  (int(source.get('announcement_count') or 0)>0
                   and source.get('announcement_count')==source.get('processed_count')))
    else:
        complete=(source.get('extraction_state')=='vide_a_verifier' or
                  bool(announcements and all(a.get('indexation_state')=='terminee'
                                             and not a.get('indexation_error') for a in announcements)))
    current=bool(complete and (not row.get('sha256') or source.get('sha256')==row.get('sha256'))
                 and source.get('extractor_version')==EXTRACTION_VERSION
                 and source.get('importer_version')==IMPORT_VERSION)
    return bool(complete), current


def files_status(base=None, m=None):
    m = manifest() if m is None else m
    known = {r['number']:r for r in m['journals']}
    if base is None:
        base = year_status_snapshot(year())
    indexed = {s['number']:s for s in base.get('sources', [])}
    announcements_by_source={}
    compact_status=bool(base.get('source_summary'))
    if not compact_status:
        for announcement in base.get('announcements',[]):
            announcements_by_source.setdefault(announcement.get('source_id'),[]).append(announcement)
    file_tasks=job.get('file_tasks',{}) if job else {}
    result = []
    numbers = sorted(set(range(1,m['expected']+1)) | set(known)) if year()==2024 and m.get('expected') else sorted(known)
    for number in numbers:
        row = known.get(number, {})
        old_source = indexed.get(number)
        drive_unavailable = False
        filename = f'jal-{year()}-{number:03d}-{row["date"]}-fr.pdf' if row.get('date') else ''
        path = archive()/filename if filename else None
        if row.get('path'):
            recorded=resolve_managed_pdf(row['path'])
            if managed_file_exists(recorded): path=recorded
            elif str(row['path']).startswith('drive/') and managed_file_state(recorded)=='unavailable': drive_unavailable=True
        if not managed_file_exists(path) and old_source:
            recorded=resolve_managed_pdf(old_source.get('canonical_pdf') or old_source.get('pdf'))
            if managed_file_exists(recorded): path=recorded
            elif str(old_source.get('canonical_pdf') or old_source.get('pdf') or '').startswith('drive/') and managed_file_state(recorded)=='unavailable': drive_unavailable=True
        # Les PDF déjà archivés sur Drive restent visibles même si une ancienne
        # version de l'inventaire n'a pas encore enregistré leur chemin.
        if not managed_file_exists(path) and filename:
            drive_copy=google_drive_root()/str(year())/filename
            if managed_file_exists(drive_copy): path=drive_copy
            elif managed_file_state(drive_copy)=='unavailable': drive_unavailable=True
        present = managed_file_exists(path)
        drive_unavailable = drive_unavailable or managed_file_state(path)=='unavailable'
        source_current=bool(old_source and (not row.get('sha256') or old_source.get('sha256') == row.get('sha256')))
        source_announcements=announcements_by_source.get(old_source.get('id'),[]) if old_source and not compact_status else []
        if compact_status and old_source:
            announcement_count=int(old_source.get('announcement_count') or 0)
            processed_count=int(old_source.get('processed_count') or 0)
            extracted_ok=(old_source.get('extraction_state')=='vide_a_verifier'
                          or (announcement_count>0 and announcement_count==processed_count))
        else:
            extracted_ok=(old_source.get('extraction_state')=='vide_a_verifier'
                          or (source_announcements and all(a.get('indexation_state')=='terminee' and not a.get('indexation_error') for a in source_announcements))) if old_source else False
        source_processed=bool(source_current and old_source.get('extractor_version')==EXTRACTION_VERSION
                              and old_source.get('importer_version')==IMPORT_VERSION and extracted_ok) if old_source else False
        has_announcements=bool(announcement_count if compact_status and old_source else source_announcements)
        same_document=bool(source_processed and has_announcements)
        task_state=file_tasks.get(f'{year()}:{number}',file_tasks.get(str(number),{}))
        transient_state=task_state.get('state') if task_state.get('state') in ('en_attente','en_cours','erreur','a_traiter') else None
        processing_state=('—' if not present else (transient_state or ('a_verifier' if source_processed and not has_announcements else ('termine' if same_document else 'a_traiter'))))
        if row.get('error') and present and not transient_state:
            processing_state='erreur'
        result.append(dict(year=year(), number=number, date=row.get('date'), filename=filename,
            path=managed_pdf_reference(path) if path else '',
            status='present' if present else ('drive_unavailable' if drive_unavailable else ('missing' if row else 'unknown')),
            indexed=same_document, indexation_current=source_processed, processing_state=processing_state,
            processing_error=task_state.get('error'), processing_updated_at=task_state.get('updated_at'),
            source_url=row.get('url'), error=row.get('error')))
    return result

def exports():
    files = files_status()
    (store()/'fichiers-manquants.csv').write_bytes(csv_bytes([f for f in files if f['status']=='missing']))
    (store()/'interventions.csv').write_bytes(csv_bytes(list(tickets().values())))

def transfer_indexed_pdfs(selected_years, progress=None, cancelled=lambda: False, numbers=None):
    """Copier puis vérifier les PDF indexés sur Drive avant de retirer leur copie locale."""
    drive = google_drive_root().resolve()
    if not drive.parent.is_dir():
        raise RuntimeError(f'Google Drive indisponible : {drive.parent}. Connectez le lecteur G: et relancez le batch.')
    drive.mkdir(parents=True, exist_ok=True)
    probe = drive / f'.jort-verification-{os.getpid()}.tmp'
    try:
        probe.write_bytes(b'JORT')
        probe.unlink()
    except OSError as exc:
        try: probe.unlink(missing_ok=True)
        except OSError: pass
        raise RuntimeError(f'Google Drive est en lecture seule ou indisponible : {exc}') from exc

    if not selected_years:
        selected_years=sorted(int(path.parent.name) for path in (ROOT/'data').glob('[0-9][0-9][0-9][0-9]/base.json'))
    candidates=[]
    for selected_year in selected_years:
        if cancelled(): return dict(state='cancelled', moved=0, skipped=0, errors=0)
        select_year(selected_year)
        base=read_json(dataset(),{})
        manifest_data=manifest()
        for item in files_status(base,manifest_data):
            if numbers is not None and item.get('number') not in numbers:
                continue
            extracted = item.get('indexed') or item.get('processing_state')=='a_verifier'
            if not extracted or item.get('status')!='present':
                continue
            reference=item.get('path','')
            if reference.startswith('drive/'):
                drive_copy=resolve_managed_pdf(reference)
                local_copy=archive()/item['filename']
                if managed_file_exists(drive_copy) and managed_file_exists(local_copy):
                    candidates.append((selected_year,item['number'],local_copy,drive_copy))
                continue
            source=resolve_managed_pdf(reference)
            if source and source.is_file():
                candidates.append((selected_year,item['number'],source,None))

    moved=skipped=errors=0
    total=len(candidates)
    for current,(selected_year,number,source,existing_target) in enumerate(candidates,1):
        if cancelled(): return dict(state='cancelled',moved=moved,skipped=skipped,errors=errors,total=total)
        select_year(selected_year)
        target_dir=drive/str(selected_year)
        target_dir.mkdir(parents=True,exist_ok=True)
        target=existing_target or target_dir/source.name
        created=False
        try:
            source_hash=pdf_sha256(source)
            if target.exists():
                target_hash=pdf_sha256(target)
                if target_hash!=source_hash:
                    raise ValueError(f'Le fichier Drive existe déjà avec un contenu différent : {target.name}')
            else:
                temporary=target.with_name(target.name+f'.{os.getpid()}.part')
                try:
                    shutil.copyfile(source,temporary)
                    copied_hash=pdf_sha256(temporary)
                    if copied_hash!=source_hash:
                        raise OSError('La copie sur Drive ne correspond pas au PDF source.')
                    os.replace(temporary,target)
                    created=True
                finally:
                    temporary.unlink(missing_ok=True)

            with write_lock:
                data=read_json(dataset(),{})
                source_record=next((row for row in data.get('sources',[]) if row.get('number')==number),None)
                manifest_data=manifest()
                journal=next((row for row in manifest_data['journals'] if row.get('number')==number),None)
                if not source_record or not journal:
                    raise ValueError('Référence de base ou d’inventaire introuvable ; PDF source conservé.')
                source_record['canonical_pdf']=managed_pdf_reference(target)
                source_record['pdf']=source_record['canonical_pdf']
                from agregation_base import revision as revision_base
                data['revision']=revision_base(data)
                save_json(dataset(),data)
                db_path=ROOT/f'data/{selected_year}/jort-{selected_year}-fr.sqlite'
                synchroniser_sqlite(db_path,data,changed_numbers={number})
                journal['path']=managed_pdf_reference(target)
                journal['status']='telecharge'
                with inventory_lock:
                    save_json(manifest_path(),manifest_data)
                if source.resolve()!=target.resolve(): source.unlink()
            moved+=1
            level='info'
            message=f'JORT n° {number} ({selected_year}) transféré et vérifié sur Google Drive.'
        except Exception as exc:
            errors+=1
            level='error'
            message=f'Transfert JORT n° {number} ({selected_year}) impossible ; PDF local conservé : {exc}'
            # Garder une copie Drive déjà vérifiée est sans risque ; la reprise est idempotente.
        log(message,level,number=number,task='transfert_drive',current=current,total=total)
        if progress:
            progress(dict(year=selected_year,current=current,total=total,number=number,message=message))
    return dict(state='done',total=total,moved=moved,skipped=skipped,errors=errors,
                message=f'{moved} PDF transféré(s), {errors} erreur(s).')

def identify(raw):
    from pypdf import PdfReader
    from texte_pdf import extract_page
    if not raw.startswith(b'%PDF-') or b'%%EOF' not in raw[-1024:]:
        raise ValueError('PDF incomplet')
    first, warnings = extract_page(PdfReader(io.BytesIO(raw)),0)
    if not first.strip():
        raise ValueError('; '.join(warnings) or 'PDF illisible')
    if not re.search(r'annonces\s+l[ée]gales', first, re.I):
        raise ValueError('Ce document ne semble pas être un journal d’annonces légales français')
    found = re.search(r'(\d{1,2})\s+('+'|'.join(MOIS)+r')\s+(19\d{2}|20\d{2})\s+N[°º]\s*(\d+)', first, re.I)
    if not found:
        raise ValueError('Numéro et date français introuvables : contrôle manuel ou reconnaissance du texte nécessaire')
    day, month, pdf_year, number = found.groups()
    if int(pdf_year)!=year():
        raise ValueError('PDF d’une autre année')
    entry = dict(number=int(number), date=f'{pdf_year}-{MOIS[month.lower()]:02d}-{int(day):02d}')
    return entry, verify_pdf(raw, entry)

def scan():
    m = manifest()
    assert_drive_inventory_access(m)
    known = {r['number']:r for r in m['journals']}
    paths = set()
    for folder in (archive(), inbox(), DOWNLOADS, ROOT/'dist/sources', ROOT/f'documents/{year()}/fr', ROOT/f'documents/{year()}/a-importer'):
        if folder.exists():
            paths.update(folder.glob('*.pdf'))
            paths.update(folder.glob('*.crdownload'))
    for path in sorted(paths):
        if cancel.is_set():
            break
        try:
            if path.suffix=='.crdownload' and time.time()-path.stat().st_mtime<30:
                continue
            raw = path.read_bytes()
            entry, info = identify(raw)
            number = entry['number']
            old = known.get(number, {})
            if old.get('date') and old['date']!=entry['date']:
                raise ValueError('Date différente de l’inventaire officiel')
            target = archive()/f'jal-{year()}-{number:03d}-{entry["date"]}-fr.pdf'
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=info['sha256']:
                ticket('conflict:'+str(number)+':'+info['sha256'], 'fichier', 'Deux fichiers différents pour ce journal : comparer les originaux.', number=number, source_pdf=str(path))
                continue
            if not target.exists():
                atomic_write_bytes(target, raw)
                log('PDF vérifié et archivé : '+target.name, number=number)
            if path != target and path.exists() and path.parent in {
                inbox(), archive(), DOWNLOADS, ROOT/f'documents/{year()}/fr',
                ROOT/f'documents/{year()}/a-importer'
            }:
                path.unlink()
            known[number] = dict(old, **entry, **info, path=target.relative_to(ROOT).as_posix(), status='telecharge', provenance=old.get('provenance','fichier_local_verifie'))
            known[number].pop('error', None)
            resolve_missing(number)
        except Exception as exc:
            if path.parent==inbox() or path.parent==archive() or path.name.startswith(f'jal-{year()}'):
                ticket('invalid:'+str(path), 'fichier', str(exc), source_pdf=str(path))
                log(path.name+': '+str(exc), 'warning')
    # Réconcilier les journaux déjà sur Drive à partir de leur nom canonique.
    # Cela évite de les retélécharger quand l'inventaire local est ancien.
    drive_year=google_drive_root()/str(year())
    for number, row in known.items():
        date=row.get('date')
        if not date:
            continue
        filename=f'jal-{year()}-{number:03d}-{date}-fr.pdf'
        recorded=resolve_managed_pdf(row.get('path'))
        if managed_file_exists(recorded):
            row['status']='telecharge'
            row.pop('error',None)
            continue
        drive_copy=drive_year/filename
        try:
            if managed_file_exists(drive_copy) and drive_copy.stat().st_size>=5:
                with drive_copy.open('rb') as stream:
                    if stream.read(5)==b'%PDF-':
                        row.update(path=managed_pdf_reference(drive_copy),status='telecharge',provenance='google_drive_reconcilie')
                        row.pop('error',None)
        except OSError as exc:
            log(f'Lecture Drive impossible pour le journal n° {number} : {exc}','warning',number=number)
    m['journals'] = sorted(known.values(), key=lambda r:r['number'], reverse=True)
    for row in m['journals']:
        recorded=resolve_managed_pdf(row.get('path'))
        if managed_file_state(recorded)=='unavailable':
            row['status']='telecharge'
            log('Google Drive est inaccessible ; inventaire conservé sans re-téléchargement.', 'warning')
        elif not recorded or not managed_file_exists(recorded):
            row['status'] = 'inventorie'
            ticket('missing:'+str(row['number']), 'fichier', 'PDF absent : télécharger puis déposer dans le dossier de réception de l’année.', number=row['number'], source_url=row.get('url'), source_pdf=f'documents/PDF/{year()}/originaux/jal-{year()}-{row["number"]:03d}-{row["date"]}-fr.pdf')
    save_json(manifest_path(), m)
    exports()

def collect(action, limit, index_queue=None, index_worker=None):
    if job:
        with lock:
            job['stage'] = 'inventaire'
            job['message'] = f'Inventaire complet de {year()} avant les téléchargements'
            save_json(job_path, job)
    scan()
    if cancel.is_set():
        return
    client = open_year(year())
    m = manifest()
    known = {r['number']:r for r in m['journals']}
    m['expected'] = int(client.page.inputs['A58'])
    m['inventory_complete'] = False
    m.pop('inventory_error', None)
    m['inventory_started_at'] = now()
    save_json(manifest_path(), m)
    visited = set()
    catalog = []
    # Première passe : parcourir tout le catalogue de l’année et enregistrer
    # les fichiers attendus. Aucun PDF n'est téléchargé pendant cette passe.
    while not cancel.is_set():
        page = client.page
        repeated_number = None
        for entry in rows(page,year()):
            number = entry['number']
            if number in visited:
                repeated_number = number
                break
            visited.add(number)
            row = dict(known.get(number, {}), **entry)
            if row.get('status') != 'telecharge' or not managed_file_exists(resolve_managed_pdf(row.get('path'))):
                row['status'] = 'inventorie'
            known[number] = row
            catalog.append(entry)
            m['journals'] = sorted(known.values(), key=lambda r:r['number'], reverse=True)
            m['updated_at'] = now()
            save_json(manifest_path(), m)
            log(f'Journal n° {number} inventorié pour le {entry["date"]}.',
                number=number, task='inventaire', current=len(visited), total=int(client.page.inputs.get('A58', m.get('expected') or 0)))
            if cancel.is_set():
                break
        for warning in getattr(page, 'row_errors', []):
            log(warning, 'warning', task='inventaire')
        if repeated_number is not None:
            m['inventory_complete'] = False
            m['inventory_error'] = f'Pagination répétée au journal n° {repeated_number}.'
            m['inventory_checked_at'] = now()
            save_json(manifest_path(), m)
            log(m['inventory_error'], 'warning', repeated_number, task='inventaire', current=len(visited), total=m.get('expected'))
            if action != 'download':
                scan()
                return 'paused'
            break
        if cancel.is_set():
            break
        nxt = next((a for a in page.links if a['text'].strip()=='>' and 'SCROLLTABLE' in a.get('href','')), None)
        if not nxt:
            m['inventory_complete'] = len(visited)==m['expected']
            m['inventory_checked_at'] = now()
            if not m['inventory_complete']:
                m['inventory_error'] = f'Catalogue incomplet : {len(visited)}/{m["expected"]} journaux relevés.'
                log(m['inventory_error'], 'warning', task='inventaire', current=len(visited), total=m.get('expected'))
            save_json(manifest_path(), m)
            break
        next_url = urllib.parse.urljoin(BASE, nxt['href'])
        page_error = None
        for attempt in range(1, 4):
            try:
                next_page = client.read(next_url)
                if isinstance(next_page, tuple) or not rows(next_page, year()):
                    raise IortSessionError('La page suivante du catalogue IORT ne contient aucun journal lisible.')
                page_error = None
                break
            except Exception as exc:
                page_error = exc
                log(f'Navigation du catalogue en échec (tentative {attempt}/3) : {type(exc).__name__}: {exc}',
                    'warning', task='inventaire', current=len(visited), total=m.get('expected'))
                if attempt < 3:
                    time.sleep(attempt)
        if page_error is not None:
            m['inventory_complete'] = False
            m['inventory_error'] = f'{type(page_error).__name__}: {page_error}'
            m['inventory_checked_at'] = now()
            save_json(manifest_path(), m)
            log(f'Inventaire partiel après {len(visited)}/{m.get("expected")} journaux : {m["inventory_error"]}',
                'warning', task='inventaire', current=len(visited), total=m.get('expected'))
            # Le lot peut traiter les entrées déjà relevées. La suite
            # reste marquée comme incomplète et sera reprise au prochain scan.
            if action != 'download':
                scan()
                return 'paused'
            break
        if m.get('inventory_complete'):
            m.pop('inventory_error', None)
    if cancel.is_set() or action != 'download':
        scan()
        return 'complete'

    if not m.get('inventory_complete'):
        # Les liens d'une session antérieure doivent être retrouvés avant usage.
        catalog.extend(dict(row, refresh_url=True) for number,row in known.items()
                       if number not in visited and row.get('date'))
        log('Inventaire incomplet : poursuite sur les journaux identifiés ; les lignes absentes restent à inventorier.', 'warning')
    return download_catalog(client, m, catalog, limit, index_queue, index_worker)


def retry_download(number):
    """Relance un seul journal sans refaire l'inventaire de toute l'année."""
    m = manifest()
    assert_drive_inventory_access(m)
    row = next((row for row in m['journals'] if row['number'] == number), None)
    if not row or not row.get('date'):
        raise ValueError('Journal inconnu : mettre à jour l’inventaire avant sa relance.')
    return download_catalog(None, m, [dict(row, refresh_url=True)], 0, DurableSink(), None)


def download_catalog(client, m, catalog, limit, index_queue=None, index_worker=None):
    known = {row['number']:row for row in m['journals']}
    base=year_status_snapshot(year())
    sources_by_number={source.get('number'):source for source in base.get('sources',[])}
    announcements_by_source={}
    for announcement in base.get('announcements',[]):
        announcements_by_source.setdefault(announcement.get('source_id'),[]).append(announcement)
    already_indexed=set()
    to_index=[]
    for entry in catalog:
        number=entry['number']
        row=known.get(number,{})
        source=sources_by_number.get(number)
        source_announcements=announcements_by_source.get(source.get('id'),[]) if source else []
        complete,current=source_checkpoint_state(source,entry,source_announcements,bool(base.get('source_summary')))
        if complete:
            already_indexed.add(number)
        # Chercher après l'inventaire aussi : un PDF Drive peut être absent
        # de l'ancien manifeste sans être absent du stockage.
        filename=f'jal-{year()}-{number:03d}-{entry["date"]}-fr.pdf'
        source_reference=source.get('canonical_pdf') if source and source.get('date')==entry.get('date') else None
        # Un fichier Drive n'est une preuve exploitable que si une référence
        # enregistrée le désigne. Un simple nom identique dans Drive ne doit
        # pas faire passer un journal « inventorié » à « téléchargé » : il
        # peut s'agir d'un autre fichier portant le même numéro.
        candidates=[resolve_managed_pdf(row.get('path')),resolve_managed_pdf(source_reference),
                    archive()/filename]
        if str(row.get('path') or source_reference or '').startswith('drive/'):
            candidates.append(google_drive_root()/str(year())/filename)
        present=next((path for path in candidates if managed_file_exists(path)),None)
        if present:
            row.update(path=managed_pdf_reference(present),status='telecharge')
            if source and source_reference==row['path'] and not row.get('sha256'):
                row['sha256']=source.get('sha256')
            _,current=source_checkpoint_state(source,row,source_announcements,bool(base.get('source_summary')))
            if index_queue is not None and not current:
                to_index.append(number)
        elif complete:
            log(f'Journal n° {number} déjà indexé ; original indisponible, aucun nouveau téléchargement.',
                'warning',number,task='telechargement')
        elif row.get('status')=='telecharge':
            row['status']='inventorie'
    # Enregistrer les chemins réconciliés avant que queue_pdf relise le manifeste.
    save_json(manifest_path(),m)
    m=manifest()
    known={row['number']:row for row in m['journals']}
    for number in to_index:
        if cancel.is_set(): return 'paused'
        if index_queue is not None:
            set_file_task(number,'en_attente')
            if not enqueue_index(index_queue, index_worker, number):
                return 'paused'

    # Un échec est conservé pour relance sans empêcher les numéros suivants.
    # La base indexée est consultée même si l'inventaire ou son chemin Drive
    # est incomplet. Une version d'extracteur plus récente demande une analyse
    # du PDF conservé, jamais un nouveau téléchargement d'un journal déjà indexé.
    missing = [entry for entry in catalog if known[entry['number']].get('status') != 'telecharge'
               and entry['number'] not in already_indexed]
    if job:
        with lock:
            planned = set(job.get('lanes',{}).get('collection',{}).get('planned_by_year',{}).get(str(year()), []))
            # Un inventaire partiel peut avoir figé un plan incomplet. À la
            # reprise, ajouter les numéros nouvellement inventoriés sans
            # réintroduire ceux déjà traités dans ce lot.
            planned.update(entry['number'] for entry in missing)
            job['planned_downloads'] = sorted(planned)
            job['lanes']['collection'].setdefault('planned_by_year',{})[str(year())]=sorted(planned)
            completed = {number for number in planned if known.get(number, {}).get('status') == 'telecharge'
                         and managed_file_exists(resolve_managed_pdf(known[number].get('path')))}
            job['completed_downloads'] = sorted(completed)
            job['completed_total'] = len(completed)
            save_json(job_path, job)
    else:
        planned = {entry['number'] for entry in missing}
        completed = set()
    lane_state = job.get('lanes', {}).get('collection', {}) if job else {}
    processed_by_year = lane_state.setdefault('processed_by_year', {}) if job else {}
    processed = {int(value) for value in processed_by_year.get(str(year()), [])}
    # Reprise des succès d'une tâche enregistrée avant ce compteur.
    processed.update(completed)
    missing = [entry for entry in catalog if entry['number'] in planned
               and known[entry['number']].get('status') != 'telecharge'
               and entry['number'] not in already_indexed
               and entry['number'] not in processed]
    inventory_label = 'Inventaire terminé' if m.get('inventory_complete') else 'Inventaire partiel'
    log(f'{inventory_label} pour {year()} : {len(catalog)}/{m.get("expected")} journaux relevés, {len(missing)} PDF manquant(s).')
    if job:
        with lock:
            job['stage'] = 'telechargement'
            remaining_limit = max(0, limit - len(processed)) if limit > 0 else len(missing)
            attempt_total = min(len(missing), remaining_limit) + len(processed)
            job['processed_downloads'] = sorted(processed)
            job['processed_total'] = len(processed)
            job['planned_total'] = attempt_total
            job['download_total'] = attempt_total
            save_json(job_path, job)
    for entry in missing:
        if cancel.is_set() or (limit > 0 and len(processed) >= limit):
            break
        number = entry['number']
        row = known[number]
        last_error = None
        downloaded_now = False
        session_refreshed = False
        for attempt in range(1, 4):
            if cancel.is_set():
                break
            try:
                if client is None or entry.pop('refresh_url', False):
                    client, refreshed_entry = find_entry(year(), number)
                    entry.update(refreshed_entry)
                log(f'Téléchargement {number} (tentative {attempt}/3)', number=number,
                    task='telechargement', current=len(processed), total=job.get('planned_total') if job else len(planned))
                raw = None
                result = read_iort_pdf(client, entry['url'], number)
                if result is None and cancel.is_set():
                    break
                if not isinstance(result, tuple):
                    raise IortSessionError('Le site IORT a renvoyé une page HTML au lieu du PDF.')
                raw, url = result
                info = verify_pdf(raw, entry)
                target = archive()/f'jal-{year()}-{number:03d}-{entry["date"]}-fr.pdf'
                if shutil.disk_usage(target.parent).free < len(raw) + 1024 * 1024:
                    raise BatchPause('Espace disque insuffisant : le téléchargement est en pause. Libérez de l’espace puis relancez le batch.')
                atomic_write_bytes(target, raw)
                row.update(info, path=target.relative_to(ROOT).as_posix(), status='telecharge', download_url=url, downloaded_at=now(), provenance='iort')
                row.pop('error', None)
                resolve_missing(number)
                completed.add(number)
                if job:
                    with lock:
                        job['completed_downloads'] = sorted(completed)
                        job['completed_total'] = len(completed)
                        save_json(job_path, job)
                log(f'Journal n° {number} téléchargé et vérifié.', number=number,
                    task='telechargement', current=len(processed)+1, total=job.get('planned_total') if job else len(planned))
                last_error = None
                downloaded_now = True
                break
            except (PDFMismatch, IortSessionError) as exc:
                last_error = exc
                if isinstance(exc, PDFMismatch) and raw:
                    quarantine = ROOT/'documents'/'PDF'/str(year())/'a-verifier'
                    digest = hashlib.sha256(raw).hexdigest()[:12]
                    saved_pdf = quarantine/f'journal-{year()}-{number:03d}-inattendu-{digest}.pdf'
                    if not saved_pdf.exists():
                        atomic_write_bytes(saved_pdf, raw)
                    ticket('unexpected-pdf:'+str(number)+':'+digest, 'fichier',
                           'PDF conservé : son numéro ou sa date ne correspond pas au journal demandé.',
                           number=number, source_pdf=saved_pdf.relative_to(ROOT).as_posix(), source_url=entry['url'])
                if session_refreshed:
                    row.update(status='erreur', error=str(exc))
                    ticket('missing:'+str(number), 'fichier', str(exc), number=number, source_url=entry['url'])
                    log(f'Journal n° {number} sauté après renouvellement : {exc}', 'warning', number, task='telechargement')
                    break
                log(f'{exc} ; renouvellement de la session IORT avant un nouvel essai.', 'warning', number, task='telechargement')
                try:
                    client, refreshed_entry = find_entry(year(), number)
                    entry.update(refreshed_entry)
                    session_refreshed = True
                except Exception as refresh_exc:
                    row.update(status='erreur', error=str(refresh_exc))
                    ticket('missing:'+str(number), 'fichier', f'Session IORT à renouveler : {refresh_exc}', number=number, source_url=entry['url'])
                    last_error = refresh_exc
                    log(f'Journal n° {number} sauté : impossible de retrouver son lien IORT ({type(refresh_exc).__name__}: {refresh_exc})',
                        'warning', number, task='telechargement')
                    break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = exc
                log(f'Erreur réseau à la tentative {attempt}/3 : {exc}', 'warning', number, task='telechargement')
                if attempt < 3:
                    time.sleep(attempt)
            except BatchPause:
                raise
            except (OSError, MemoryError) as exc:
                if ressource_indisponible(exc):
                    log(f'Pause de sécurité : {exc}', 'error', number)
                    raise BatchPause(f'Problème de disque ou de mémoire pendant le journal {number} ; reprise disponible après intervention.') from exc
                last_error=exc
                log(f'Tentative {attempt}/3 : {exc}', 'warning', number, task='telechargement')
                if attempt<3 and erreur_temporaire(exc): time.sleep(attempt)
                elif not erreur_temporaire(exc): break
            except Exception as exc:
                last_error = exc
                log(f'Tentative {attempt}/3 : {exc}', 'warning', number, task='telechargement')
                if attempt < 3:
                    time.sleep(attempt)
        if last_error is not None:
            failures=lane_state.setdefault('failed_downloads_by_year',{}).setdefault(str(year()),[])
            if number not in failures: failures.append(number)
            row.update(status='erreur', error=str(last_error))
            ticket('missing:'+str(number), 'fichier', str(last_error), number=number, source_url=entry['url'])
            log(f'Journal n° {number} écarté après ses tentatives ; le batch continue : {type(last_error).__name__}: {last_error}',
                'warning', number, task='telechargement', current=len(processed)+1,
                total=job.get('planned_total') if job else len(planned))
        # La quantité sélectionnée compte les journaux tentés, échecs inclus.
        if downloaded_now or last_error is not None:
            processed.add(number)
            if job:
                with lock:
                    processed_by_year[str(year())] = sorted(processed)
                    job['processed_downloads'] = sorted(processed)
                    job['processed_total'] = len(processed)
                    save_json(job_path, job)
        m['journals'] = sorted(known.values(), key=lambda r:r['number'], reverse=True)
        m['updated_at'] = now()
        save_json(manifest_path(), m)
        if downloaded_now and index_queue is not None:
            set_file_task(number,'en_attente')
            if not enqueue_index(index_queue, index_worker, number):
                return 'paused'
    return 'complete'

def refresh_tickets():
    with review_lock:
        _refresh_tickets()


def apply_reviews():
    """Only the coordinator calls this at a boundary between PDF processes."""
    with review_lock:
        combined=read_json(ROOT/'data/logs/interventions-identites.json',{})
        for path in sorted((ROOT/'data').glob('[0-9]*/logs/interventions.json')):
            for key,value in read_json(path,{}).items():
                combined[f'{path.parent.parent.name}:{key}']={**value,'year':int(path.parent.parent.name)}
        review_items=copy.deepcopy(list(combined.values()))
    announcement_reviews={v.get('announcement_id'):v for v in review_items if v.get('announcement_id')}
    identity_states={}
    for review in review_items:
        if review.get('kind')!='identite': continue
        decisions=review.get('decisions',[])
        if not decisions and review.get('state') in ('valide','a_corriger'):
            decisions=[dict(state=review['state'],entity_ids=review.get('entity_ids',[]))]
        for decision in decisions:
            state={'valide':'confirme','a_corriger':'ecarte'}.get(decision.get('state'))
            if state:
                scope={str(c.get('entity_id')):c for c in decision.get('candidates',[])}
                for entity_id in decision.get('entity_ids',[]): identity_states[entity_id]=(state,review['id'],scope.get(str(entity_id)))
    for path in sorted((ROOT/'data').glob('[0-9]*/base.json')):
        data=read_json(path,{})
        changed=set()
        by_announcement={a['id']:a for a in data.get('announcements',[])}
        for entity in data.get('entities',[]):
            assignment=identity_states.get(entity.get('id'))
            if not assignment or entity.get('type')!='personne' or not entity.get('cin'): continue
            state,identity_key,proof=assignment
            cin=re.sub(r'\D','',str(entity['cin']))
            if identity_key.startswith('identity:all:') and identity_key!='identity:all:'+identity_fingerprint(cin): continue
            if proof and (proof.get('name')!=entity.get('name') or proof.get('announcement_id')!=entity.get('announcement_id')): continue
            if entity.get('identity_match_status')!=state:
                entity['identity_match_status']=state
                changed.add(entity.get('announcement_id'))
        for announcement in data.get('announcements',[]):
            review=announcement_reviews.get(announcement['id'],{})
            if review.get('state') in ('valide','a_corriger'):
                values={'status':review['state'],'review_note':review.get('note','')}
                if any(announcement.get(k)!=v for k,v in values.items()):
                    announcement.update(values); changed.add(announcement['id'])
        for relation in data.get('relations',[]):
            review=announcement_reviews.get(relation.get('announcement_id'),{})
            state='a_corriger' if review.get('state')=='a_corriger' else 'extrait_automatiquement'
            if review and relation.get('status')!=state:
                relation['status']=state; changed.add(relation.get('announcement_id'))
        if changed:
            source_ids={by_announcement[aid].get('source_id') for aid in changed if aid in by_announcement}
            numbers={v['number'] for v in data.get('sources',[]) if v['id'] in source_ids}
            data['revision']=dataset_revision(data)
            save_json(path,data)
            synchroniser_sqlite(path.parent/f'jort-{path.parent.name}-fr.sqlite',data,numbers)
    with review_lock:
        # A newer decision saved during projection must remain pending.
        for previous in review_items:
            path=ROOT/'data/logs/interventions-identites.json' if previous.get('kind')=='identite' else ROOT/f'data/{previous.get("year",year())}/logs/interventions.json'
            items=read_json(path,{})
            current=items.get(previous['id'])
            if current and current.get('revision',0)==previous.get('revision',0) and current.get('application_state')=='pending':
                current['application_state']='applied'; current['applied_at']=now()
                save_json(path,items)


def publish_boundary():
    global publication_last_attempt
    with write_lock:
        publication_last_attempt=time.monotonic()
        with lock:
            projected_revision=job.get('review_revision',0)
            job.setdefault('publication',{}).update(state='publishing',updated_at=now())
            save_coordinator()
        refresh_tickets()
        apply_reviews()
        if any((ROOT/'data').glob('[0-9]*/base.json')):
            reconstruire(ROOT)
        with lock:
            job['projection_revision']=projected_revision
            job['publication']=dict(state='ready',pending_journals=0,published_at=now(),updated_at=now())
            save_coordinator()


def mark_publication_pending():
    """Le journal est enregistré ; seule la vue globale attend sa reconstruction."""
    with lock:
        state=job.setdefault('publication',{})
        state.update(state='pending',pending_journals=int(state.get('pending_journals',0))+1,updated_at=now())
        save_coordinator()


def publish_index_boundary(force=False):
    """Limiter le coût de la vue globale ; toujours la terminer à la fin de la file."""
    global publication_last_attempt
    with write_lock:
        state=job.get('publication',{})
        if state.get('state') not in ('pending','publishing'):
            return True
        if not force and state.get('error') and time.monotonic()-publication_last_attempt<PUBLICATION_INTERVAL_SECONDS:
            return False
        if not force and state.get('pending_journals',0)<PUBLICATION_JOURNAL_LIMIT and time.monotonic()-publication_last_attempt<PUBLICATION_INTERVAL_SECONDS:
            return False
        publication_last_attempt=time.monotonic()
        try:
            publish_boundary()
            return True
        except PermissionError as exc:
            # Un PDF déjà indexé ne devient pas erroné parce qu'un navigateur
            # garde le fichier de restitution ouvert. La reprise ne relit pas le PDF.
            with lock:
                job.setdefault('publication',{}).update(state='pending',error=str(exc),updated_at=now())
                save_coordinator()
            log(f'Restitution à republier après libération du fichier : {exc}', 'warning',task='publication')
            return False


def request_projection():
    global projection_thread
    with lock:
        if projection_thread and projection_thread.is_alive(): return
        projection_thread=threading.Thread(target=projection_worker,daemon=True)
        projection_thread.start()


def projection_worker():
    context.lane='maintenance'
    while not shutdown_requested.is_set():
        retry=False
        with write_lock:
            with lock:
                reviews_pending=job.get('projection_revision',-1)<job.get('review_revision',0)
                if not reviews_pending and job.get('publication',{}).get('state') not in ('pending','publishing'): return
            try:
                if reviews_pending: publish_boundary()
                else:
                    retry=not publish_index_boundary(force=True)
                    if not retry: return
            except Exception as exc:
                with lock:
                    job.setdefault('publication',{}).update(state='pending',error=str(exc),updated_at=now())
                    save_coordinator()
                log(f'Publication à reprendre : {exc}','error',task='publication')
                if not isinstance(exc,PermissionError): return
                retry=True
        # Aucun verrou conservé pendant l'attente : analyse et décisions manuelles
        # continuent. Une publication interrompue reprend sans relire les PDF.
        if retry and shutdown_requested.wait(PUBLICATION_INTERVAL_SECONDS): return


def _invoke_builder(cancel_path, limit=None, number=None, defer_aggregate=False, force_number=None):
    global process
    if job:
        with lock:
            job['stage'] = 'indexation'
            job['message'] = 'Extraction séquentielle des PDF disponibles'
            save_json(job_path, job)
    worker_env=dict(os.environ,PYTHONIOENCODING='utf-8')
    command=[sys.executable,str(ROOT/'scripts/construire_base_2024.py'),'--process-label','JO-JORT-INDEXATION',
             '--year',str(year()),'--cancel-file',str(cancel_path)]
    if number is not None:
        command.extend(['--number',str(number)])
    if force_number is not None:
        command.extend(['--force-number',str(force_number)])
    elif limit is not None:
        command.extend(['--limit',str(limit)])
    if defer_aggregate:
        command.append('--defer-aggregate')
    with lock:
        if cancel.is_set():
            raise RuntimeError('Traitement annulé')
        process=subprocess.Popen(command, cwd=ROOT, env=worker_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        save_json(ROOT/'data/logs/travailleur-processus.json',dict(pid=process.pid,started_epoch=time.time(),python=sys.executable,
            root=str(ROOT),script=str(ROOT/'scripts/construire_base_2024.py'),role='worker',process_label='JO-JORT-INDEXATION'))
    active=process
    failure=None
    try:
        for line in active.stdout:
            message=line.strip()
            if not message:
                continue
            if message.startswith('JORT_WORKER_ERROR '):
                try:
                    failure=json.loads(message[len('JORT_WORKER_ERROR '):])
                except json.JSONDecodeError:
                    pass
                continue
            match=re.search(r'Import ou mise à jour \d+ : JAL (\d+)', message)
            progress=re.search(r'Indexation (\d+)/(\d+) : (.+)', message)
            if progress:
                log(message, number=None, task='indexation_annonce',
                    current=int(progress.group(1)), total=int(progress.group(2)))
            else:
                log(message, number=int(match.group(1)) if match else number,
                    task='indexation_journal' if match else 'indexation')
        code=active.wait()
    finally:
        with lock:
            if process is active:
                process=None
    if code and failure and not cancel.is_set():
        if failure.get('kind')=='temporary':
            raise sqlite3.OperationalError('temporarily unavailable: '+failure.get('message',''))
        if failure.get('kind')=='resource':
            raise BatchPause(failure.get('message','Ressource locale indisponible'))
        raise RuntimeError(failure.get('message','Échec du traitement'))
    return code

def index_one_pdf(number, cancel_path, force=False):
    code=_invoke_builder(cancel_path,number=number,defer_aggregate=True,force_number=number if force else None)
    if code and not cancel.is_set():
        if code & 0xffffffff in (0xc0000017,0xc000009a):
            raise BatchPause('Mémoire insuffisante ; le point de reprise est conservé.')
        raise RuntimeError(f'Journal n° {number} non analysable (code {code}) ; le traitement passe au suivant.')
    if cancel.is_set():
        return False
    data=read_json(dataset(),{})
    source=next((s for s in data.get('sources',[]) if s.get('number')==number),None)
    if not source:
        raise RuntimeError(f'Le journal n° {number} n’apparaît pas dans la base après indexation.')
    errors=[a for a in data.get('announcements',[]) if a.get('source_id')==source['id'] and (a.get('indexation_state')!='terminee' or a.get('indexation_error'))]
    if errors:
        raise RuntimeError(f'{len(errors)} annonce(s) du journal n° {number} restent à indexer.')
    return 'a_verifier' if source.get('extraction_state')=='vide_a_verifier' else True

def index_queue_worker(selected_year, pending):
    select_year(selected_year)
    cancel_path=store()/'annulation.flag'
    while True:
        number=pending.get()
        try:
            if number is None:
                return
            if cancel.is_set():
                set_file_task(number,'a_traiter')
                continue
            set_file_task(number,'en_cours')
            completed=False
            last_error=None
            for attempt in range(1,4):
                if cancel.is_set():
                    break
                try:
                    completed=index_one_pdf(number,cancel_path)
                    last_error=None
                    break
                except BatchPause as exc:
                    last_error=exc
                    break
                except Exception as exc:
                    if ressource_indisponible(exc):
                        last_error=BatchPause(f'Ressource locale indisponible : {exc}. Reprise conservée.')
                        break
                    last_error=exc
                    log(f'Indexation du journal n° {number}, tentative {attempt}/3 : {exc}',
                        'warning',number,task='indexation_pdf')
                    if not recoverable_index_error(exc) or attempt>=3:
                        break
                    time.sleep(attempt)
            if isinstance(last_error,BatchPause):
                exc=last_error
                set_file_task(number,'erreur',exc)
                with lock:
                    if job:
                        job.update(state='paused',message=str(exc),finished_at=now())
                        save_json(job_path,job)
                cancel.set()
            elif last_error:
                set_file_task(number,'erreur',last_error)
            else:
                set_file_task(number, 'a_verifier' if completed=='a_verifier' else ('termine' if completed else 'a_traiter'))
        finally:
            pending.task_done()

def index_pdfs(limit, cancel_path, pause_on_error=False):
    scan()
    if cancel.is_set():
        return
    code=_invoke_builder(cancel_path,limit=limit)
    if code and not cancel.is_set():
        if pause_on_error:
            raise BatchPause('Indexation en pause après une erreur locale (mémoire ou disque). Le point de reprise est conservé.')
        raise RuntimeError('Extraction interrompue ; consulter le journal.')
    refresh_tickets()
    apply_reviews()

def index_pending(limit=0):
    """Traite les PDF déjà présents un par un, avec un point de reprise par journal."""
    scan()
    candidates=[item for item in files_status()
        if item.get('status')=='present' and item.get('processing_state') not in ('termine','a_verifier')]
    candidates.sort(key=lambda item:int(item.get('number') or 0))
    if limit:
        candidates=candidates[:limit]
    pending=queue.Queue(maxsize=1)
    worker=threading.Thread(target=index_queue_worker,args=(year(),pending),daemon=True)
    worker.start()
    try:
        for item in candidates:
            if cancel.is_set():
                break
            set_file_task(item['number'],'en_attente')
            if not enqueue_index(pending, worker, item['number']):
                break
    finally:
        finish_index_stream(pending,worker)

def enqueue_index(pending, worker, number):
    if isinstance(pending, DurableSink):
        if not queue_pdf(year(),number): return False
        reported=False
        while not cancel.is_set():
            with wake:
                backlog=sum(t.get('kind')=='pdf' and t.get('state') in ('pending','active') for t in job['tasks'])
                if backlog<MAX_PENDING_PDFS: return True
                if lane_cancel['indexation'].is_set():
                    raise BatchPause('Analyse en pause ; les PDF en attente sont conservés et la collecte attend sa reprise.')
                if not worker_alive('indexation'):
                    start_index()
                if not reported:
                    log(f'Collecte en attente : {backlog} PDF restent à analyser. Reprise dès qu’une place se libère.',task='file_analyse')
                    reported=True
                wake.wait(timeout=2)
        return False
    """Une file pleine ne doit jamais masquer la disparition du travailleur."""
    while not cancel.is_set():
        if worker is None or not worker.is_alive():
            set_file_task(number, 'a_traiter')
            raise BatchPause('Le travailleur d’extraction s’est arrêté ; reprise conservée par journal.')
        try:
            pending.put(number, timeout=.25)
            return True
        except queue.Full:
            continue
    set_file_task(number, 'a_traiter')
    return False

def finish_index_stream(pending, worker):
    if worker is None:
        return
    while worker.is_alive():
        try:
            pending.put(None, timeout=.25)
            break
        except queue.Full:
            continue
    worker.join(timeout=1)
    while worker.is_alive():
        try:
            worker.join(timeout=1)
        except RuntimeError:
            break
    if not worker.is_alive() and pending.unfinished_tasks:
        # La file n'a plus de consommateur : conserver la reprise dans l'état
        # des fichiers plutôt que d'attendre un acquittement qui ne viendra pas.
        while True:
            try:
                number=pending.get_nowait()
                if number is not None:
                    set_file_task(number,'a_traiter')
                pending.task_done()
            except queue.Empty:
                break


class DurableSink:
    pass


def migrate_coordinator():
    global job
    old=job or {}
    if old.get('schema_version')==2:
        job=old
    else:
        lanes={name:dict(state='idle',stop_requested=False) for name in lane_cancel}
        action=old.get('action')
        name='collection' if action in ('download','night_batch','inventory','scan') else 'indexation'
        if old:
            lanes[name]={**old,'years':old.get('years') or [old.get('year',2024)]}
        job={**old,'schema_version':2,'lanes':lanes,'tasks':[],'review_revision':0}
    job.setdefault('tasks',[])
    for task in job['tasks']:
        if task.get('state')=='active': task['state']='pending'
    for name in lane_cancel:
        lane=job.setdefault('lanes',{}).setdefault(name,dict(state='idle'))
        if lane.get('action')=='night_batch':
            lane.update(action='download',state='cancelled',stop_requested=True,
                        message='Ancien téléchargement nocturne retiré ; lancer la collecte depuis l’administration.')
        if lane.get('stop_requested'):
            lane['state']='cancelled'; lane_cancel[name].set()
    job.setdefault('file_tasks',{})
    job.setdefault('projection_revision',0)


def save_coordinator():
    with lock:
        if not job.get('lanes'): migrate_coordinator()
        lanes=job['lanes']
        states=[v.get('state') for v in lanes.values()]
        job['state']='stopping' if 'stopping' in states else 'running' if 'running' in states else 'paused' if 'paused' in states else 'failed' if 'failed' in states else 'cancelled' if 'cancelled' in states else 'done'
        job['updated_at']=now()
        job['queue']={state:sum(t.get('state')==state for t in job['tasks']) for state in ('pending','active','completed','error')}
        saved_keys=lanes['indexation'].get('run_task_keys')
        run_keys=set(saved_keys) if saved_keys is not None else None
        pdf_tasks=[t for t in job['tasks'] if t.get('kind')=='pdf' and (run_keys is None or t.get('key') in run_keys)]
        lanes['indexation']['completed_journals']=sum(t.get('state')=='completed' for t in pdf_tasks)
        lanes['indexation']['failed_journals']=sum(t.get('state')=='error' for t in pdf_tasks)
        lanes['indexation']['total_journals']=len(pdf_tasks)
        save_json(job_path,job)
        wake.notify_all()


def worker_alive(name):
    return bool(lane_threads.get(name) and lane_threads[name].is_alive())


def coordinator_busy():
    return any(worker_alive(name) for name in lane_cancel) or bool(projection_thread and projection_thread.is_alive())


def public_job():
    with lock:
        value=copy.deepcopy({key:value for key,value in job.items() if key not in ('tasks','file_tasks')})
        for lane in value.get('lanes',{}).values(): lane.pop('run_task_keys',None)
        # compatibility with older pipeline rendering
        value['file_tasks']={str(v['number']):v for v in job.get('file_tasks',{}).values() if v.get('year',year())==year()}
        return value


def queue_pdf(selected_year,number,force=False,start=True):
    select_year(selected_year)
    checkpoint=next((f for f in files_status() if f.get('number')==number),{})
    if not force and checkpoint.get('indexation_current'):
        # Le point de reprise durable prime sur un ancien état « à traiter ».
        if start and any(t.get('year')==selected_year and t.get('number')==number
                         and t.get('state')=='pending' for t in job['tasks']):
            start_index()
        return True
    row=next((r for r in manifest().get('journals',[]) if r.get('number')==number),None)
    source_path=resolve_managed_pdf(row.get('path')) if row else None
    if not row or not source_path or not source_path.is_file():
        set_file_task(number,'erreur','PDF vérifié introuvable au moment de la mise en file.')
        log(f'Journal n° {number} non mis en file : PDF local absent.', 'error', number, task='indexation_pdf')
        return False
    digest=pdf_sha256(source_path)
    if digest!=row.get('sha256'):
        updated=manifest()
        next(r for r in updated['journals'] if r['number']==number)['sha256']=digest
        save_json(manifest_path(),updated)
    key=f'{selected_year}:{number}:{digest}:{IMPORT_VERSION}:{EXTRACTION_VERSION}'
    with lock:
        existing=next((t for t in job['tasks'] if t.get('key')==key),None)
        if existing and existing.get('state')=='active': return True
        if existing and existing.get('state')=='pending':
            if force:
                existing['force']=True
                save_coordinator()
            if start: start_index()
            return True
        if existing and existing.get('state')=='completed' and not force:
            current=next((f for f in files_status() if f.get('number')==number),{})
            if current.get('processing_state') in ('termine','a_verifier'): return True
        task=dict(key=key,kind='pdf',year=selected_year,number=number,sha256=digest,importer_version=IMPORT_VERSION,extractor_version=EXTRACTION_VERSION,state='pending',queued_at=now(),force=bool(force))
        if existing:
            existing.update(task)
            existing.pop('error',None); existing.pop('finished_at',None)
        else: job['tasks'].append(task)
        lane=job['lanes']['indexation']
        if worker_alive('indexation'):
            keys=lane.setdefault('run_task_keys',[])
            if key not in keys: keys.append(key)
        save_coordinator()
    if start: start_index()
    return True


def submit_existing(years,limit=0):
    submitted=0
    for selected_year in years:
        select_year(selected_year)
        candidates=[f for f in files_status() if f.get('status')=='present' and f.get('processing_state') not in ('termine','a_verifier')]
        selected=sorted(candidates,key=lambda f:int(f.get('number') or 0))[:limit or None]
        for item in selected:
            if queue_pdf(selected_year,item['number'],start=False): submitted+=1
        if selected:
            log(f'Contrôle des PDF pour {selected_year} : {len(selected)} à analyser ou reprendre.',
                task='indexation_pdf',current=0,total=len(selected))
    if submitted:
        start_index()
    return submitted


def reconcile_downloaded(selected_year):
    """Remet en file, en fin de collecte, tout PDF présent sans indexation achevée."""
    select_year(selected_year)
    files=files_status()
    candidates=[item for item in files if item.get('status')=='present'
                and item.get('processing_state') not in ('termine','a_verifier')]
    if not candidates:
        log(f'Contrôle après collecte {selected_year} : tous les PDF présents ont un état terminal.',
            task='indexation_pdf',current=0,total=0)
        return 0
    queued=0
    for item in sorted(candidates,key=lambda f:int(f.get('number') or 0)):
        if cancel.is_set():
            break
        if queue_pdf(selected_year,item['number'],start=False):
            queued+=1
    log(f'Contrôle après collecte {selected_year} : {len(candidates)} PDF à analyser ou reprendre, {queued} demandes vérifiées dans la file.',
        task='indexation_pdf',current=0,total=len(candidates))
    if queued:
        start_index()
    return queued


def start_index(explicit=False):
    with lock:
        lane=job['lanes']['indexation']
        if explicit:
            lane_cancel['indexation'].clear(); lane.update(stop_requested=False)
        if worker_alive('indexation') or shutdown_requested.is_set(): return
        if lane_cancel['indexation'].is_set() and not any(t.get('kind')=='maintenance' and t.get('state')=='pending' for t in job['tasks']): return
        requested=job['lanes']['collection'].get('years',[]) if worker_alive('collection') else lane.get('years',[])
        task_years=[t.get('year') for t in job['tasks'] if t.get('kind')=='pdf' and t.get('state')=='pending']
        active_years=list(dict.fromkeys([*requested,*task_years]))
        lane.update(id=str(time.time_ns()),state='cancelled' if lane_cancel['indexation'].is_set() else 'running',action='index',message='PDF en attente',started_at=now(),current=0,total=0,current_number=None,error=None,
                    years=active_years,run_task_keys=[t['key'] for t in job['tasks'] if t.get('kind')=='pdf' and t.get('state')=='pending'])
        lane_threads['indexation']=threading.Thread(target=coordinator_index,daemon=True)
        lane_threads['indexation'].start()
        save_coordinator()


def coordinator_index():
    context.lane='indexation'
    try:
        while not shutdown_requested.is_set():
            with lock:
                task=next((t for t in job['tasks'] if t.get('state')=='pending' and not lane_cancel['maintenance' if t.get('kind')=='maintenance' else 'indexation'].is_set()),None)
                collection_active=worker_alive('collection')
                if task is not None:
                    name='maintenance' if task.get('kind')=='maintenance' else 'indexation'
                    task['state']='active'
                    lane=job['lanes'][name]
                    if name=='indexation':
                        keys=lane.setdefault('run_task_keys',[])
                        if task['key'] not in keys: keys.append(task['key'])
                    lane.update(state='running',year=task.get('year',2024),current_year=task.get('year',2024),current_number=task.get('number'),current=0,total=0,message='Analyse du journal' if name=='indexation' else 'Maintenance de la base',error=None)
                    save_coordinator()
            if task is None:
                if not lane_cancel['indexation'].is_set():
                    # Écrire hors du verrou de la file pour conserver le même
                    # ordre de verrous que la maintenance et les validations.
                    try:
                        published=publish_index_boundary(force=not collection_active)
                        if not published and not collection_active:
                            request_projection()
                    except Exception as exc:
                        with lock:
                            job.setdefault('publication',{}).update(state='pending',error=str(exc),updated_at=now())
                            job['lanes']['indexation'].update(state='paused' if ressource_indisponible(exc) else 'failed',error=str(exc),message='Publication à reprendre')
                            save_coordinator()
                        log(f'Publication finale à reprendre : {exc}','error',task='publication')
                        if ressource_indisponible(exc): lane_cancel['indexation'].set()
                        break
                if lane_cancel['indexation'].is_set() or not collection_active:
                    break
                with wake: wake.wait(timeout=3)
                continue
            context.lane=name
            select_year(task.get('year',2024))
            try:
                write_guard = nullcontext() if name=='maintenance' and task.get('mode')=='transfer_drive' else write_lock
                with write_guard:
                    if name=='maintenance':
                        if task['mode']=='transfer_drive':
                            report=transfer_indexed_pdfs(task.get('years'),progress=maintenance_progress,cancelled=cancel.is_set)
                        else:
                            from maintenance_base import maintenance
                            report=maintenance(ROOT,mode=task['mode'],years=task.get('years'),full=True,progress=maintenance_progress,cancelled=cancel.is_set,publish=False,
                                               identity_entity_ids=task.get('identity_entity_ids'),identity_name=task.get('identity_name'),identity_options=task.get('identity_options'))
                        lane['report']=report
                        if report.get('state') in ('failed','error'):
                            raise RuntimeError(report.get('error') or report.get('message','Échec de la maintenance ; restitution conservée.'))
                        if report.get('state') in ('cancelled','stopping'):
                            lane_cancel['maintenance'].set()
                        if task['mode'] not in ('check','optimize','backup') and report.get('can_publish',True) and not cancel.is_set():
                            publish_boundary()
                            marker = read_json(ROOT/'dist/publication.json', {})
                            if marker.get('revision'):
                                report['published_revision'] = marker['revision']
                                if report.get('identity_merge') is not None:
                                    report['identity_merge']['published'] = True
                            lane['report'] = report
                            if report.get('id'):
                                report_dir = ROOT/'data/maintenance'
                                save_json(report_dir/f"rapport-{report['id']}.json", report)
                                save_json(report_dir/'dernier-rapport.json', report)
                    else:
                        # Reconcile durable requests with completed PDF checkpoints.
                        f=next((f for f in files_status() if f.get('number')==task['number']),{})
                        if task.get('force'):
                            log(f'Relance manuelle de l’extraction du journal n° {task["number"]}.',number=task['number'],task='indexation_pdf')
                        checkpoint_current=f.get('indexation_current',f.get('processing_state') in ('termine','a_verifier'))
                        result='a_verifier' if checkpoint_current and not f.get('indexed',True) else True
                        if task.get('force') or not checkpoint_current:
                            flag=store()/'annulation.flag'
                            if flag.exists() and not cancel.is_set(): flag.unlink()
                            set_file_task(task['number'],'en_cours')
                            last=None
                            for attempt in range(1,4):
                                try:
                                    task['attempts']=attempt
                                    log(f'Analyse du journal n° {task["number"]} (tentative {attempt}/3).',
                                        number=task['number'],task='indexation_pdf')
                                    result=index_one_pdf(task['number'],flag,force=task.get('force',False)); last=None; break
                                except Exception as exc:
                                    last=exc
                                    log(f'Analyse du journal n° {task["number"]}, tentative {attempt}/3 : {exc}',
                                        'warning',task['number'],task='indexation_pdf')
                                    if cancel.is_set() or isinstance(exc,BatchPause) or not recoverable_index_error(exc) or attempt==3: break
                                    time.sleep(attempt)
                            if last: raise last
                        if not cancel.is_set(): set_file_task(task['number'],'a_verifier' if result=='a_verifier' else 'termine')
                        if result!='a_verifier' and not cancel.is_set():
                            try:
                                moved=transfer_indexed_pdfs([task.get('year',year())],cancelled=cancel.is_set,
                                                            numbers={task['number']})
                                if moved.get('errors'):
                                    log(f"Transfert Drive du journal n° {task['number']} à reprendre ; le PDF local est conservé.",
                                        'warning',number=task['number'],task='transfert_drive')
                            except Exception as drive_error:
                                log(f"Indexation réussie, transfert Drive du journal n° {task['number']} à reprendre : {drive_error}",
                                    'warning',number=task['number'],task='transfert_drive')
                        if not cancel.is_set():
                            mark_publication_pending()
                with lock:
                    task['state']='pending' if cancel.is_set() else 'completed'
                    if name=='indexation' and cancel.is_set(): set_file_task(task['number'],'a_traiter')
                    task['finished_at']=now()
                    lane.update(state='cancelled' if cancel.is_set() else 'running',current_number=None,current=0,total=0)
                    if name=='maintenance': lane.update(state='cancelled' if cancel.is_set() else 'done',finished_at=now())
                    save_coordinator()
            except Exception as exc:
                with lock:
                    task.update(state='pending' if cancel.is_set() or isinstance(exc,BatchPause) else 'error',error=str(exc))
                    if name=='indexation': set_file_task(task['number'],'a_traiter' if cancel.is_set() else 'erreur',exc)
                    lane.update(state='cancelled' if cancel.is_set() else 'paused' if isinstance(exc,BatchPause) else 'failed',error=str(exc),message=str(exc))
                    if name=='maintenance': lane['finished_at']=now()
                    save_coordinator()
                log(str(exc),'error',task.get('number'))
                if isinstance(exc,BatchPause): lane_cancel[name].set()
                elif name=='indexation' and not cancel.is_set():
                    log(f'Journal n° {task["number"]} écarté ; poursuite avec le PDF suivant.',
                        'warning',task['number'],task='indexation_pdf')
            context.lane='indexation'
            if lane_cancel['indexation'].is_set() and not any(t.get('kind')=='maintenance' and t.get('state')=='pending' and not lane_cancel['maintenance'].is_set() for t in job['tasks']): break
    finally:
        with lock:
            lane=job['lanes']['indexation']
            if lane.get('state') in ('running','stopping'): lane.update(state='cancelled' if lane_cancel['indexation'].is_set() else 'done',finished_at=now())
            if lane_threads.get('indexation') is threading.current_thread():
                lane_threads.pop('indexation',None)
            save_coordinator()
            eligible=any(t.get('state')=='pending' and not lane_cancel['maintenance' if t.get('kind')=='maintenance' else 'indexation'].is_set() for t in job['tasks'])
            if not shutdown_requested.is_set() and eligible: start_index()


def maintenance_progress(*args,**kwargs):
    value=args[0] if args else kwargs
    message=value.get('message','Maintenance') if isinstance(value,dict) else str(value)
    if isinstance(value,dict) and value.get('year') is not None: select_year(value['year'])
    log(message,number=value.get('number') if isinstance(value,dict) else None,task='maintenance',current=value.get('current') if isinstance(value,dict) else None,total=value.get('total') if isinstance(value,dict) else None)


def coordinator_collect():
    context.lane='collection'
    lane=job['lanes']['collection']
    try:
        for selected_year in lane['years']:
            if cancel.is_set(): break
            resume_year=lane.get('resume_from_year')
            # Les années antérieures au curseur sont déjà couvertes par cette
            # exécution. Les échecs y restent au rapport pour une reprise ciblée.
            if resume_year is not None and int(selected_year)>int(resume_year):
                continue
            if selected_year in lane.get('completed_years',[]): continue
            select_year(selected_year)
            with lock:
                lane.update(year=selected_year,current_year=selected_year,
                            current_number=None,current=0,total=0,resume_from_year=selected_year,
                            message='Préparation de la collecte')
                save_coordinator()
            resource_pause=False
            try:
                if lane['action']=='download_one':
                    result=retry_download(lane['number'])
                elif lane['action']=='download':
                    result=collect('download',lane.get('limit',0),DurableSink(),None)
                else:
                    result=scan()
                if lane['action']=='scan' and not cancel.is_set():
                    submit_existing([selected_year], 0)
                if result=='paused':
                    raise ValueError('Inventaire partiel ; journaux non identifiés conservés pour reprise.')
                if not cancel.is_set():
                    if manifest().get('inventory_complete') or lane['action'] in ('scan','download_one'):
                        if selected_year not in lane.setdefault('completed_years',[]):
                            lane['completed_years'].append(selected_year)
                    else:
                        if selected_year not in lane.setdefault('incomplete_years',[]):
                            lane['incomplete_years'].append(selected_year)
                    with lock:
                        save_coordinator()
            except BatchPause:
                resource_pause=True
                raise
            except Exception as exc:
                if ressource_indisponible(exc) and not isinstance(exc, urllib.error.URLError):
                    resource_pause=True
                    raise BatchPause(f'Ressource locale indisponible : {exc}') from exc
                lane.setdefault('errors_by_year',{})[str(selected_year)]=f'{type(exc).__name__}: {exc}'
                log(f'Année {selected_year} en erreur : {type(exc).__name__}: {exc}. Poursuite sur l’année suivante.', 'error')
                save_coordinator()
            finally:
                # La mise en file par téléchargement est immédiate ; ce contrôle
                # de fin rattrape aussi un PDF que la file n'aurait pas reçu.
                if lane['action'] in ('download','download_one') and not cancel.is_set() and not resource_pause:
                    try:
                        reconcile_downloaded(selected_year)
                    except Exception as exc:
                        if ressource_indisponible(exc):
                            raise BatchPause(f'Ressource indisponible au contrôle final de {selected_year} : {exc}') from exc
                        lane.setdefault('errors_by_year',{})[str(selected_year)]=f'Contrôle final : {type(exc).__name__}: {exc}'
                        log(f'Contrôle final de {selected_year} à reprendre : {exc}. Poursuite sur l’année suivante.','warning')
        with lock:
            lane.update(state='cancelled' if cancel.is_set() else 'done',finished_at=now())
            save_coordinator()
    except Exception as exc:
        with lock:
            lane.update(state='cancelled' if cancel.is_set() else 'paused' if isinstance(exc,BatchPause) else 'failed',message=str(exc),error=str(exc),finished_at=now())
            save_coordinator()
        log(str(exc),'error')
    finally:
        with lock: wake.notify_all()


def queue_maintenance(mode,years=None,identity_entity_ids=None,identity_name=None,identity_options=None):
    if mode not in ('check','repair','reextract_entities','clean_parasitic_extractions','merge_entities','undo_merge','connections','deduplicate_announcements','merge_duplicates','merge_person','optimize','backup','transfer_drive'): raise ValueError('Mode de maintenance invalide')
    if mode in ('connections','reextract_entities','clean_parasitic_extractions','merge_entities','undo_merge','deduplicate_announcements','merge_duplicates','merge_person'):
        years=None
    if years is None:
        years=sorted(int(path.parent.name) for path in (ROOT/'data').glob('[0-9][0-9][0-9][0-9]/base.json'))
        if not years: raise ValueError('Aucune base annuelle disponible pour la maintenance.')
    if not years: raise ValueError('Indiquer au moins une année pour la maintenance.')
    with lock:
        existing=next((t for t in job['tasks'] if t.get('kind')=='maintenance' and t.get('state') in ('pending','active')),None)
        if existing and mode in ('merge_entities','undo_merge','merge_person'):
            raise ValueError('Une maintenance est déjà demandée ; attendez son résultat avant cette décision.')
        if existing and (existing['state']=='active' or existing['mode']!=mode or existing.get('years')!=years): raise ValueError('Maintenance déjà demandée')
        lane_cancel['maintenance'].clear()
        job['lanes']['maintenance'].update(id=str(time.time_ns()),state='running',action='maintenance_'+mode,years=years,
            started_at=now(),finished_at=None,current=0,total=0,completed=0,year=years[0],current_year=years[0],current_number=None,error=None,report=None,
            stop_requested=False,message='Maintenance en attente entre deux PDF')
        if not existing:
            task=dict(key='maintenance:'+str(time.time_ns()),kind='maintenance',mode=mode,years=years,year=(years or [year()])[0],state='pending')
            if mode=='merge_person':
                task.update(identity_entity_ids=identity_entity_ids,identity_name=identity_name)
            if identity_options is not None:
                task['identity_options']=identity_options
            job['tasks'].append(task)
        save_coordinator()
    start_index()


def submit_job(body):
    action=body.get('action'); limit=int(body.get('limit',0))
    if action not in ('scan','download','download_one','index','index_one','maintenance_check','maintenance_repair','maintenance_reextract_entities','maintenance_clean_parasitic_extractions','maintenance_merge_entities','maintenance_undo_merge','maintenance_connections','maintenance_deduplicate_announcements','maintenance_merge_duplicates','maintenance_merge_person','maintenance_optimize','maintenance_backup','maintenance_transfer_drive') or limit not in (0,5,10,25,151): raise ValueError('Action ou quantité invalide')
    years=body.get('years') or [int(body.get('year',year()))]
    if not isinstance(years,list) or not years or len(years)>datetime.now().year-1956+1: raise ValueError('Années invalides')
    years=[int(v) for v in years]
    if len(set(years))!=len(years) or any(v<1956 or v>datetime.now().year for v in years): raise ValueError('Années invalides')
    plan_id=str(body.get('plan_id') or '')
    if plan_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',plan_id):
        raise ValueError('Identifiant de collecte invalide')
    resume_from_year=body.get('resume_from_year')
    if resume_from_year is not None:
        try: resume_from_year=int(resume_from_year)
        except (TypeError,ValueError): raise ValueError('Année de reprise invalide')
        if action!='download' or not plan_id or resume_from_year not in years:
            raise ValueError('Le point de reprise ne correspond pas à ce plan')
    number=None
    if action=='download_one':
        number=int(body.get('number',0))
        if len(years)!=1 or number<=0: raise ValueError('Indiquer une année et un numéro de journal')
        select_year(years[0])
        if not any(row['number']==number and row.get('date') for row in manifest()['journals']):
            raise ValueError('Journal inconnu : mettre à jour l’inventaire avant sa relance.')
        limit=0
    if action=='index_one':
        number=int(body.get('number',0))
        if len(years)!=1 or number<=0: raise ValueError('Indiquer une année et un numéro de journal')
        if worker_alive('indexation'): raise ValueError('Une indexation est déjà en cours ; relancez ce journal lorsqu’elle sera terminée.')
        select_year(years[0])
        selected=next((item for item in files_status() if item.get('number')==number),None)
        if not selected or selected.get('status')!='present': raise ValueError('Ce journal ne possède pas de PDF local vérifié à analyser.')
        with lock:
            lane_cancel['indexation'].clear()
            job['lanes']['indexation'].update(id=str(time.time_ns()),state='running',action='index_one',years=years,year=years[0],current_number=number,stop_requested=False,started_at=now(),message=f'Reprise individuelle du journal n° {number}')
        queue_pdf(years[0],number,force=True,start=False)
        start_index(explicit=True)
        save_coordinator()
        return
    if action.startswith('maintenance_'):
        requested_years = None if body.get('maintenance_scope') == 'all' else (body.get('years') or years)
        mode=action.removeprefix('maintenance_')
        if mode in ('merge_entities','undo_merge'):
            options=body.get('identity_options')
            if not isinstance(options,dict) or options.get('confirmed') is not True:
                raise ValueError('Confirmez la décision après avoir consulté les annonces sources.')
            if mode=='merge_entities':
                ids=options.get('canonical_ids')
                if (not isinstance(ids,list) or not 2<=len(ids)<=100 or len(set(ids))!=len(ids)
                        or any(not isinstance(value,str) or not re.fullmatch(r'ent-[a-f0-9]{32}',value) for value in ids)
                        or not 5<=len(str(options.get('reason') or '').strip())<=1000
                        or len(str(options.get('label') or ''))>150):
                    raise ValueError('Choisissez des fiches distinctes et renseignez le motif vérifié.')
            elif not re.fullmatch(r'fusion-[a-f0-9]{32}', str(options.get('event_id') or '')):
                raise ValueError('Identifiant de fusion invalide.')
            queue_maintenance(mode,requested_years,identity_options=options)
        elif mode=='merge_person':
            entity_ids=body.get('entity_ids')
            person_name=str(body.get('person_name') or '').strip()
            if (not isinstance(entity_ids,list) or len(entity_ids)<2 or len(entity_ids)>100
                    or any(not isinstance(value,str) or not value.strip() for value in entity_ids)
                    or len(set(entity_ids))!=len(entity_ids)
                    or person_name!='Selma Elloumi Rekik'):
                raise ValueError('Fusion manuelle invalide ; les occurrences autorisées ne sont pas reconnues.')
            queue_maintenance(mode,requested_years,entity_ids,person_name)
        else:
            queue_maintenance(mode,requested_years)
        return
    if action not in ('index',) and worker_alive('collection'): raise ValueError('La voie collecte est déjà active')
    with lock:
        job['id']=job.get('id') or str(time.time_ns())
        job.update(action=action,year=years[0])
    if action=='index':
        # No scans or PDF parsing inside this request; consume verified files.
        submit_existing(years,limit)
        job['lanes']['indexation'].update(id=str(time.time_ns()),years=years,year=years[0],current_year=years[0],current=0,total=0,current_number=None,stop_requested=False)
        start_index(explicit=True)
    else:
        with lock:
            if worker_alive('collection'): raise ValueError('La voie collecte est déjà active')
            lane_cancel['collection'].clear()
            previous=job.get('lanes',{}).get('collection',{})
            same_stopped_plan=(action=='download' and plan_id and previous.get('plan_id')==plan_id
                               and previous.get('state') in ('cancelled','paused','failed','stopping'))
            cursor=resume_from_year
            if same_stopped_plan:
                saved_cursor=previous.get('current_year',previous.get('year'))
                cursor=saved_cursor if saved_cursor in years else cursor
                if cursor is not None and int(cursor) not in years:
                    raise ValueError('Le point de reprise sauvegardé ne correspond plus aux années demandées')
            carry=(lambda key,default: list(previous.get(key,default) or []))
            new_lane=dict(id=str(time.time_ns()),plan_id=plan_id,state='running',action=action,years=years,
                          year=cursor if same_stopped_plan and cursor is not None else years[0],
                          current_year=cursor if same_stopped_plan and cursor is not None else years[0],
                          resume_from_year=cursor if same_stopped_plan else None,
                          number=number,limit=limit,stop_requested=False,
                          completed_years=carry('completed_years',[]) if same_stopped_plan else [],
                          incomplete_years=carry('incomplete_years',[]) if same_stopped_plan else [],
                          errors_by_year=dict(previous.get('errors_by_year',{})) if same_stopped_plan else {},
                          failed_downloads_by_year=dict(previous.get('failed_downloads_by_year',{})) if same_stopped_plan else {},
                          processed_by_year=dict(previous.get('processed_by_year',{})) if same_stopped_plan else {},
                          planned_by_year=dict(previous.get('planned_by_year',{})) if same_stopped_plan else {},
                          started_at=now())
            job['lanes']['collection']=new_lane
            lane_threads['collection']=threading.Thread(target=coordinator_collect,daemon=True)
            lane_threads['collection'].start()
        if action in ('scan','download','download_one'): start_index(explicit=True)
    save_coordinator()


def stop_lanes(name='all'):
    if name not in (*lane_cancel,'all'): raise ValueError('Voie inconnue')
    for selected in lane_cancel if name=='all' else [name]:
        lane_cancel[selected].set()
        lane=job['lanes'][selected]
        lane.update(stop_requested=True,state='stopping' if worker_alive(selected) or (selected=='maintenance' and lane.get('state')=='running') else 'cancelled')
        if selected in ('indexation','maintenance') and process and process.poll() is None and getattr(context,'lane','')!='collection':
            active=next((t for t in job['tasks'] if t.get('state')=='active'),{})
            if selected==('maintenance' if active.get('kind')=='maintenance' else 'indexation') and active.get('year'):
                flag=ROOT/f'data/{active["year"]}/logs/annulation.flag'; flag.parent.mkdir(parents=True,exist_ok=True); flag.write_text('stop',encoding='utf-8')
    save_coordinator()


def shutdown_service(server):
    shutdown_requested.set()
    with lock: stop_lanes()
    child=process
    if child and child.poll() is None:
        try: child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name=='nt': subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/taskkill.exe'),'/PID',str(child.pid),'/T','/F'],capture_output=True,timeout=10)
            else: child.terminate()
    for thread in list(lane_threads.values()):
        if thread is not threading.current_thread(): thread.join(timeout=3)
    # Ne supprimer aucun WAL SQLite : ramener les transactions validées dans
    # la base lorsque le rédacteur a fini ; sinon la reprise SQLite reste sûre.
    if write_lock.acquire(timeout=2):
        try:
            consultation=ROOT/'data/restitution.sqlite'
            if consultation.exists():
                with closing(sqlite3.connect(consultation,timeout=2)) as db:
                    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except (OSError,sqlite3.Error) as exc:
            log(f'Point SQLite conservé pour la reprise : {exc}','warning',task='arret')
        finally:write_lock.release()
    server.shutdown()


migrate_coordinator()


class Handler(SimpleHTTPRequestHandler):
    def local_host(self):
        return self.headers.get('Host', '').lower() in ('127.0.0.1:8765', 'localhost:8765')

    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(ROOT/'dist'),**kwargs)

    def end_headers(self):
        # Les pages restent fraîches. Les gros fichiers de données sont
        # conservés par le navigateur et revalidés par date avant transfert.
        path=urllib.parse.urlsplit(self.path).path
        if path in ('/','/index.html','/collecte.html'):
            self.send_header('Cache-Control','no-store')
        elif path.endswith(('.js','.json','.css')):
            self.send_header('Cache-Control','no-cache')
        super().end_headers()

    def respond(self, value, status=200, content_type='application/json; charset=utf-8'):
        raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode('utf-8')
        try:
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Le navigateur a quitté la requête; ce n'est pas une panne du serveur.
            return

    def do_GET(self):
        if not self.local_host():
            self.respond({'error':'Hôte local requis'},403)
            return
        parsed=urllib.parse.urlsplit(self.path)
        if parsed.path=='/api/health':
            health_job=public_job()
            lanes=health_job.get('lanes',{})
            lane_fields=('id','plan_id','state','action','years','year','current_year','current_number',
                         'current','total','completed','completed_years','incomplete_years',
                         'errors_by_year','failed_downloads_by_year','error','message','started_at','finished_at',
                         'completed_journals','failed_journals','total_journals')
            self.respond(dict(service='jort-local',state='ok',build=BUILD,pid=os.getpid(),root=str(ROOT),startup_check=startup_report,
                              runtime=dict(temp=os.environ.get('TEMP'),drive=str(google_drive_root()),python=sys.executable),
                              worker_active=coordinator_busy(),
                              job_id=health_job.get('id'),queue=health_job.get('queue',{}),publication=health_job.get('publication',{}),
                              lanes={name:{key:lane[key] for key in lane_fields if key in lane} for name,lane in lanes.items()}))
            return
        query=urllib.parse.parse_qs(parsed.query)
        if parsed.path in ('/api/restitution/meta','/api/restitution/announcements','/api/restitution/entities','/api/restitution/graph','/api/annonce'):
            from restitution_index import Store
            try:
                consultation=Store(ROOT)
                value=lambda key,default='':query.get(key,[default])[0]
                exact=value('exact')=='1'
                if parsed.path=='/api/restitution/meta':
                    payload=consultation.metadata()
                elif parsed.path=='/api/restitution/announcements':
                    from postgresql_local import enabled
                    args=(value('q'),exact,max(0,int(value('page','0'))),40,value('category'),value('source'),value('entity'))
                    payload=consultation.search(*args,publication=value('publication'),include_convocations=value('include_convocations')=='1') if enabled(ROOT) else consultation.search(*args)
                elif parsed.path=='/api/restitution/entities':
                    payload=consultation.nodes(value('q'),exact,max(0,int(value('page','0'))),min(100,max(1,int(value('limit','100')))),value('type'),value('featured')=='1')
                elif parsed.path=='/api/restitution/graph':
                    payload=consultation.graph(value('root')[:200],max(1,min(4,int(value('depth','2')))),value('event','all'))
                else:
                    payload=consultation.announcement(value('id')[:200])
                    if payload is None:
                        self.respond({'error':'Annonce introuvable'},404)
                        return
                self.respond(payload)
            except Exception as exc:
                self.respond({'error':f'Consultation indisponible : {exc}'},503)
            return
        if parsed.path=='/api/collecte/overview':
            try:
                self.respond(annual_overview())
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.respond({'error':f'Bilan annuel indisponible : {exc}'},500)
            return
        if parsed.path=='/api/collecte/identity-preview':
            from referentiel_entites import previsualiser
            ids=query.get('ids',[''])[0].split(',')
            if not 2<=len(ids)<=100 or any(not re.fullmatch(r'ent-[a-f0-9]{32}',value) for value in ids):
                self.respond({'error':'Sélection d’entités invalide'},400)
            else:
                self.respond(previsualiser(read_snapshot(ROOT/'dist/data.json',{}),ids))
            return
        if parsed.path=='/api/collecte/identities':
            try:
                from postgresql_local import enabled
                if enabled(ROOT):
                    from consultation_postgresql import Store as PostgreStore
                    self.respond(PostgreStore(ROOT).identities(query.get('q',[''])[0][:150]))
                else:
                    from referentiel_entites import consulter
                    self.respond(consulter(ROOT,query.get('q',[''])[0][:150]))
            except (OSError,ValueError,sqlite3.Error) as exc:
                self.respond({'error':f'Référentiel indisponible : {exc}'},503)
            return
        if parsed.path=='/api/collecte/maintenance-report':
            self.respond({'report':read_snapshot(ROOT/'data/maintenance/dernier-rapport.json',{})})
            return
        if parsed.path=='/api/collecte/runs':
            selected_year=None
            if 'year' in query:
                try:
                    selected_year=int(query['year'][0])
                    if not 1956 <= selected_year <= datetime.now().year: raise ValueError
                except (ValueError,TypeError):
                    self.respond({'error':'Année invalide'},400)
                    return
            runs=[]
            execution_dir=ROOT/'data/logs/executions'
            if execution_dir.is_dir():
                unique={p.name.split('.')[0]:p for p in execution_dir.glob('[0-9]*.jsonl.gz')}
                unique.update({p.stem:p for p in execution_dir.glob('[0-9]*.jsonl')})
                paths=sorted(unique.values(),key=lambda item:item.stat().st_mtime,reverse=True)
                for path in paths:
                    try:
                        summary = path.with_name(path.name.split('.')[0]+'.resume.json')
                        if path.suffix == '.gz' and summary.exists():
                            run=read_json(summary,{})
                            if selected_year is None or str(selected_year) in {str(value) for value in run.get('years',[])}:
                                runs.append(run)
                            continue
                        with path.open('r',encoding='utf-8') as stream: first_line=stream.readline()
                        first=json.loads(first_line) if first_line else {}
                        tail=read_jsonl_tail(path,count=1,max_bytes=8192)
                        last=tail[-1] if tail else first
                        run=dict(id=path.stem,started_at=first.get('time'),last_at=last.get('time'),lane=first.get('lane'),action=first.get('action'),years=first.get('years',[]),events=None)
                        if selected_year is None or str(selected_year) in {str(value) for value in run['years']}:
                            runs.append(run)
                    except (OSError,UnicodeError,json.JSONDecodeError):
                        continue
            runs.sort(key=lambda item:item.get('last_at') or '',reverse=True)
            self.respond({'items':runs[:100]})
            return
        if parsed.path=='/api/collecte/run-log':
            run_id=query.get('id',[''])[0]
            if not re.fullmatch(r'[0-9]{8,30}',run_id):
                self.respond({'error':'Identifiant d’exécution invalide'},400)
                return
            path=ROOT/'data/logs/executions'/f'{run_id}.jsonl'
            if not path.exists():
                path=path.with_name(path.name+'.gz')
            try:
                events=read_jsonl_tail(path,count=2000,max_bytes=2*1024*1024)
            except OSError:
                self.respond({'error':'Journal d’exécution introuvable'},404)
                return
            self.respond({'id':run_id,'events':events})
            return
        try:
            select_year(query.get('year',[2024])[0])
        except ValueError as exc:
            self.respond({'error':str(exc)},400)
            return
        if parsed.path=='/api/collecte/status':
            with lock:
                m=read_snapshot(manifest_path(), {'year':year(), 'expected':151 if year()==2024 else None, 'inventory_complete':False, 'journals':[]})
                data=year_status_snapshot(year())
                files=files_status(data, m)
                stats=data.get('stats') or dict(announcements=len(data.get('announcements',[])),organizations=sum(e.get('type')!='personne' for e in data.get('entities',[])),persons=sum(e.get('type')=='personne' for e in data.get('entities',[])),relations=len(data.get('relations',[])),unclassified=sum(a.get('category')=='a_classer' for a in data.get('announcements',[])),pages=sum(s.get('pages',0) for s in data.get('sources',[])))
                counts={}
                for value in tickets().values():
                    if value['state'] in ('ouvert','a_corriger'):
                        counts[value['kind']]=counts.get(value['kind'],0)+1
                events=read_jsonl_tail(store()/'evenements.jsonl')
                cursor=job.get('log_views',{}).get(str(year()),'')
                events=[event for event in events if event.get('time','')>cursor]
                visible_job=public_job()
                published=read_snapshot(ROOT/'dist/publication.json', {})
                self.respond(dict(service='jort-local',version=1,build=BUILD,worker_active=coordinator_busy(),startup_check=startup_report,year=year(),data_revision=published.get('revision'),paths=dict(archive=str(archive()),inbox=str(inbox()),quarantine=str(ROOT/'documents'/'PDF'/str(year())/'a-verifier'),downloads=str(DOWNLOADS)),coverage=dict(expected=m.get('expected'),known=len(m['journals']),available=sum(f['status']=='present' for f in files),indexed=sum(f['indexed'] for f in files),inventory_complete=m.get('inventory_complete',False)),job=visible_job,stats=stats,ticket_counts=counts,files=files,logs=events))
        elif parsed.path=='/api/collecte/tickets':
            values=list(tickets().values())
            kind=query.get('kind',['all'])[0]
            state=query.get('state',['open'])[0]
            values=[v for v in values if (kind=='all' or v['kind']==kind) and (state=='all' or v['state'] in ('ouvert','a_corriger'))]
            offset=max(0,int(query.get('offset',[0])[0]))
            limit=min(100,max(1,int(query.get('limit',[30])[0])))
            self.respond(dict(items=values[offset:offset+limit],total=len(values)))
        elif parsed.path=='/api/collecte/export':
            kind=query.get('type',['missing'])[0]
            if kind=='events':
                paths=sorted(store().glob('evenements-*.jsonl'))+sorted(store().glob('evenements*.jsonl.gz'))+([store()/'evenements.jsonl'] if (store()/'evenements.jsonl').exists() else [])
                values=[]
                for path in paths:
                    opener=gzip.open if path.suffix == '.gz' else open
                    with opener(path,'rt',encoding='utf-8') as stream:
                        for line in stream:
                            try: values.append(json.loads(line))
                            except json.JSONDecodeError: continue
            else:
                values=[f for f in files_status() if f['status']=='missing'] if kind=='missing' else list(tickets().values())
            self.respond(csv_bytes(values),content_type='text/csv; charset=utf-8')
        elif parsed.path.startswith('/documents/'):
            target=(ROOT/urllib.parse.unquote(parsed.path.lstrip('/'))).resolve()
            if not target.is_relative_to((ROOT/'documents').resolve()) or not target.is_file():
                self.send_error(404)
                return
            self.respond(target.read_bytes(),content_type='application/pdf')
        elif parsed.path.startswith('/drive/'):
            target=resolve_managed_pdf(urllib.parse.unquote(parsed.path.lstrip('/')))
            if not target or not target.is_file():
                self.send_error(404)
                return
            self.respond(target.read_bytes(),content_type='application/pdf')
        else:
            super().do_GET()

    def do_HEAD(self):
        if not self.local_host():
            self.send_error(403, 'Hote local requis')
            return
        super().do_HEAD()

    def do_POST(self):
        global job, job_thread
        origin=self.headers.get('Origin')
        if (self.headers.get('X-JORT-Local')!='1'
                or not self.local_host()
                or origin not in ('http://127.0.0.1:8765','http://localhost:8765')):
            self.respond({'error':'Requête locale requise'},403)
            return
        if shutdown_requested.is_set():
            self.respond({'error':'Arrêt du service en cours'},503)
            return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size < 0:
                raise ValueError('Taille de requête invalide')
            if urllib.parse.urlsplit(self.path).path=='/api/collecte/upload':
                select_year(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('year',[2024])[0])
                if not 0<size<=100*1024*1024:
                    raise ValueError('Le dépôt doit faire au maximum 100 Mo')
                with lock:
                    if coordinator_busy():
                        self.respond({'error':'Attendez la fin du traitement avant de déposer des PDF'},409)
                        return
                    content_type=self.headers.get('Content-Type','')
                    if not content_type.startswith('multipart/form-data'):
                        raise ValueError('Un formulaire de fichiers PDF est requis')
                    raw=self.rfile.read(size)
                    message=BytesParser(policy=email_policy).parsebytes(('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+raw)
                    prepared=[]
                    for part in message.iter_parts():
                        filename=part.get_filename()
                        if not filename:
                            continue
                        filename=Path(filename.replace('\\','/')).name
                        filename=re.sub(r'[^\w. -]','_',filename)[:150]
                        payload=part.get_payload(decode=True)
                        if not filename.lower().endswith('.pdf') or not payload or not payload.startswith(b'%PDF-') or b'%%EOF' not in payload[-1024:]:
                            raise ValueError('Fichier PDF incomplet ou invalide : '+filename)
                        prepared.append((filename,payload))
                    if not prepared:
                        raise ValueError('Aucun PDF fourni')
                    deposited=[]
                    for filename,payload in prepared:
                        target=inbox()/filename
                        if target.exists() and target.read_bytes()!=payload:
                            target=inbox()/(Path(filename).stem+'-'+hashlib.sha256(payload).hexdigest()[:10]+'.pdf')
                        atomic_write_bytes(target,payload)
                        deposited.append(dict(filename=target.name,path=target.relative_to(ROOT).as_posix()))
                    submit_job(dict(action='scan',year=year()))
                    self.respond({'files':deposited,'job':job})
                return
            if size>16384:
                raise ValueError('Requête trop volumineuse')
            body=json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(body,dict):
                raise ValueError('Un objet JSON est requis')
            select_year(body.get('year',2024))
            with lock:
                if self.path=='/api/collecte/clear-logs':
                    if coordinator_busy():
                        self.respond({'error':'Attendez la fin du traitement avant d’effacer les journaux'},409)
                        return
                    cursor=now()
                    job.setdefault('log_views',{})[str(year())]=cursor
                    save_coordinator()
                    self.respond({'ok':True,'year':year(),'events_removed':0,'processing_cleared':False,'visible_logs_cleared':True})
                elif self.path=='/api/collecte/job':
                    submit_job(body)
                    self.respond({'job':public_job()})
                elif self.path=='/api/collecte/open-inbox':
                    if os.name!='nt':
                        raise ValueError('Copiez le chemin du dossier pour l’ouvrir dans votre gestionnaire de fichiers')
                    os.startfile(str(inbox()))
                    self.respond({'ok':True})
                elif self.path=='/api/collecte/shutdown':
                    shutdown_requested.set()
                    cancel.set()
                    self.respond({'ok':True,'service':'jort-local','state':'stopping'})
                    threading.Thread(target=shutdown_service,args=(self.server,),daemon=True).start()
                elif self.path=='/api/collecte/stop':
                    stop_lanes(body.get('lane','all'))
                    self.respond({'job':public_job()})
                elif self.path=='/api/collecte/review':
                    with review_lock:
                        items=tickets()
                        value=items.get(body.get('id'))
                        if not value: raise ValueError('Intervention introuvable')
                        state=body.get('state'); note=str(body.get('note','')).strip()
                        if state not in ('valide','a_corriger','resolu') or not note: raise ValueError('Une note et un état valide sont requis')
                        if value['id'].startswith('missing:') and state in ('valide','resolu') and not any(f['number']==value.get('number') and f['status']=='present' for f in files_status()): raise ValueError('Déposez et vérifiez le PDF avant de résoudre ce fichier manquant')
                        revision=job.get('review_revision',0)+1; job['review_revision']=revision
                        decision=dict(state=state,note=note,entity_ids=list(value.get('entity_ids',[])),candidates=copy.deepcopy(value.get('candidates',[])),revision=revision,at=now())
                        value.setdefault('decisions',[]).append(decision)
                        value.update(state=state,note=note,updated_at=now(),revision=revision,year=year(),application_state='pending')
                        save_tickets(items)
                        save_coordinator()
                    # The publication waits for the current PDF without blocking note saves.
                    request_projection()
                    self.respond({'ok':True,'application':'pending','revision':revision})
                else:
                    self.respond({'error':'Action introuvable'},404)
        except Exception as exc:
            self.respond({'error':str(exc)},400)

if __name__=='__main__':
    # Réserver le port avant d'écrire les identifiants de processus. Un second
    # lancement direct ne doit pas écraser les enregistrements du premier.
    server = ThreadingHTTPServer(('127.0.0.1',8765),Handler)
    (ROOT/'data/logs').mkdir(parents=True,exist_ok=True)
    save_json(ROOT/'data/logs/serveur-processus.json',dict(pid=os.getpid(),started_epoch=time.time(),python=sys.executable,
        root=str(ROOT),script=str(Path(__file__).resolve()),role='server',process_label='JO-JORT-API-COLLECTE'))
    (ROOT/'data/logs/serveur.pid').write_text(str(os.getpid()),encoding='utf-8')
    save_coordinator()
    try:
        from maintenance_base import startup_check
        startup_report=startup_check(ROOT,budget_seconds=2)
    except Exception as exc:
        startup_report=dict(state='attention',message=str(exc))
    preflight=dict(state='ok',years_checked=[],warnings=[])
    for folder in sorted((ROOT/'data').glob('[0-9][0-9][0-9][0-9]')):
        if not (folder/'base.json').is_file():
            continue
        try:
            # Vérifier les bases locales sans relire tout le JSON ni ouvrir
            # des milliers de PDF Drive au démarrage.
            database=folder/f'jort-{folder.name}-fr.sqlite'
            with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                db.execute('SELECT id FROM sources LIMIT 1').fetchone()
                db.execute('SELECT id FROM announcements LIMIT 1').fetchone()
            preflight['years_checked'].append(int(folder.name))
        except Exception as exc:
            preflight['state']='failed'
            preflight['warnings'].append(f"{folder.name} : {type(exc).__name__} — {exc}")
    try:
        from restitution_index import Store
        consultation=Store(ROOT).metadata()
        marker=read_json(ROOT/'dist/publication.json',{})
        if consultation.get('revision')!=marker.get('revision') or marker.get('state')!='ready':
            raise ValueError('Vue de consultation à reconstruire depuis les bases annuelles.')
        preflight['consultation']=dict(state='ok',revision=consultation['revision'],version=consultation.get('version'))
    except Exception as exc:
        preflight['state']='failed'
        preflight['warnings'].append(f'Consultation : {exc}')
    startup_report['application_preflight']=preflight
    if preflight['warnings']:
        startup_report['state']='attention'
        startup_report.setdefault('warnings',[]).extend(preflight['warnings'])
    print('JORT privé — site et API : http://127.0.0.1:8765/',flush=True)
    marker=read_json(ROOT/'dist/publication.json',{})
    if marker.get('state')=='writing' and job.get('publication',{}).get('state') not in ('pending','publishing'):
        job['publication']=dict(state='pending',pending_journals=1,updated_at=now(),message='Publication interrompue à terminer sans réextraire les PDF.')
        save_coordinator()
    lane=job['lanes']['collection']
    if lane.get('state')=='running' and not lane.get('stop_requested'):
        lane_threads['collection']=threading.Thread(target=coordinator_collect,daemon=True)
        lane_threads['collection'].start()
    if job['lanes']['indexation'].get('state')=='running' and not job['tasks']:
        old_index=job['lanes']['indexation']
        submit_existing(old_index.get('years') or [old_index.get('year',2024)],old_index.get('limit',0))
    if job['lanes']['indexation'].get('state')=='running' or any(t.get('state')=='pending' for t in job['tasks']) or job.get('publication',{}).get('state') in ('pending','publishing'):
        start_index()
    if job.get('review_revision',0)>job.get('projection_revision',0) or job.get('publication',{}).get('state') in ('pending','publishing'): request_projection()
    try:
        server.serve_forever()
    finally:
        server.server_close()
