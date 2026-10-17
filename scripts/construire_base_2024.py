"""Construire une base privée pour une année à partir des PDF déjà collectés."""
import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

from importer_echantillon import importer, VERSION as IMPORT_VERSION
from collecter_2024 import save_json
from extraire_entites import enrichir
from agregation_base import reconstruire
from schema_evolution import appliquer_schema
from erreurs_traitement import erreur_temporaire, ressource_indisponible
from chemins_drive import resolve_drive_reference

ROOT = Path(__file__).resolve().parents[1]

def resolve_pdf_path(relative):
    value=str(relative or '').replace('\\','/')
    if value.lower().startswith('drive/'):
        return resolve_drive_reference(value)
    path=(ROOT/Path(value)).resolve()
    path.relative_to(ROOT.resolve())
    return path
EXTRACTION_VERSION = 5


def _replace_with_retry(source, target):
    for attempt in range(5):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(.1 * (attempt + 1))


def revision(data):
    """Empreinte stable pour savoir si la base publiée a changé."""
    snapshot = dict(data)
    snapshot.pop('revision', None)
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def checkpoint(path, data):
    """Sauvegarde atomique après chaque journal ou annonce traitée."""
    data['revision'] = revision(data)
    save_json(path, data)


def synchroniser_sqlite(db_path, data, changed_numbers=None, revision_only=False):
    """Met à jour SQLite par journal ; une base absente est créée entièrement."""
    db_path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(db_path,timeout=30)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=30000')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS sources(
              id TEXT PRIMARY KEY, number INTEGER, date TEXT, pdf TEXT,
              canonical_pdf TEXT, sha256 TEXT UNIQUE, source_url TEXT,
              extractor_version INTEGER, extraction_state TEXT, importer_version INTEGER);
            CREATE TABLE IF NOT EXISTS announcements(
              id TEXT PRIMARY KEY, reference TEXT, source_id TEXT REFERENCES sources(id),
              category TEXT, pages TEXT, text TEXT, status TEXT, source_date TEXT,
              indexation_state TEXT, indexation_error TEXT, warning TEXT);
            CREATE TABLE IF NOT EXISTS entities(
              id TEXT PRIMARY KEY, name TEXT, type TEXT, cin TEXT, announcement_id TEXT REFERENCES announcements(id),
              identifier TEXT, identifier_type TEXT, identity_match_status TEXT);
            CREATE TABLE IF NOT EXISTS relations(
              id TEXT PRIMARY KEY, from_id TEXT REFERENCES entities(id), to_id TEXT REFERENCES entities(id),
              role TEXT, type TEXT, announcement_id TEXT REFERENCES announcements(id), quote TEXT, status TEXT,
              event_type TEXT, event_date TEXT);
            CREATE TABLE IF NOT EXISTS _jort_meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE INDEX IF NOT EXISTS announcement_reference ON announcements(reference);
            CREATE INDEX IF NOT EXISTS announcement_source ON announcements(source_id);
            CREATE INDEX IF NOT EXISTS entity_name ON entities(name);
            CREATE INDEX IF NOT EXISTS entity_announcement ON entities(announcement_id);
            CREATE INDEX IF NOT EXISTS entity_cin ON entities(cin);
            CREATE INDEX IF NOT EXISTS relation_announcement ON relations(announcement_id);
            CREATE INDEX IF NOT EXISTS relation_from ON relations(from_id,id);
            CREATE INDEX IF NOT EXISTS relation_to ON relations(to_id,id);
        ''')
        additions={
          'sources': [('extractor_version','INTEGER'),('extraction_state','TEXT'),('importer_version','INTEGER')],
          'announcements': [('source_date','TEXT'),('indexation_state','TEXT'),('indexation_error','TEXT'),('warning','TEXT'),('title','TEXT'),('title_quote','TEXT'),('title_confidence','TEXT'),('title_review_required','INTEGER'),('title_extraction_version','INTEGER')],
          'entities': [('identifier','TEXT'),('identifier_type','TEXT'),('identity_match_status','TEXT'),('is_primary','INTEGER'),('entity_extraction_version','INTEGER'),('identifier_quote','TEXT')],
          'relations': [('event_type','TEXT'),('event_date','TEXT'),('event_date_quote','TEXT'),('role_family','TEXT')],
        }
        for table,columns in additions.items():
            existing={row[1] for row in db.execute(f'PRAGMA table_info({table})')}
            for name,kind in columns:
                if name not in existing:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
        appliquer_schema(db)
        meta=db.execute("SELECT value FROM _jort_meta WHERE key='revision'").fetchone()
        current_revision=data.get('revision') or revision(data)
        # Une empreinte différente est normale après un nouveau journal : les
        # appels incrémentaux remplacent seulement les numéros concernés.
        # Une reconstruction complète sert à créer/réparer la base ou quand
        # l'appelant ne sait pas quels journaux ont changé.
        full_rebuild=meta is None or (meta[0]!=current_revision and not revision_only and changed_numbers is None)
        db.execute('BEGIN IMMEDIATE')
        if full_rebuild:
            db.execute('DELETE FROM relations');db.execute('DELETE FROM entities')
            db.execute('DELETE FROM announcements');db.execute('DELETE FROM sources')
            selected_sources=data.get('sources',[])
        elif not revision_only:
            numbers={int(value) for value in (changed_numbers or [])}
            if numbers:
                # Les listes d'annonces d'une année ancienne dépassent parfois
                # la limite SQLite de paramètres (souvent 999). Lire et effacer
                # par lots évite qu'un seul journal empêche toute la correction.
                def batches(values, size=400):
                    values=list(values)
                    for start in range(0,len(values),size):
                        yield values[start:start+size]
                old_ids=[]
                for batch in batches(sorted(numbers)):
                    old_ids.extend(row[0] for row in db.execute(
                        f"SELECT id FROM sources WHERE number IN ({','.join('?' for _ in batch)})",batch))
                if old_ids:
                    announcement_ids=[]
                    for batch in batches(old_ids):
                        placeholders=','.join('?' for _ in batch)
                        announcement_ids.extend(row[0] for row in db.execute(
                            f'SELECT id FROM announcements WHERE source_id IN ({placeholders})',batch))
                    if announcement_ids:
                        for batch in batches(announcement_ids):
                            placeholders=','.join('?' for _ in batch)
                            db.execute(f'DELETE FROM relations WHERE announcement_id IN ({placeholders})',batch)
                            db.execute(f'DELETE FROM entities WHERE announcement_id IN ({placeholders})',batch)
                            db.execute(f'DELETE FROM announcements WHERE id IN ({placeholders})',batch)
                    for batch in batches(old_ids):
                        placeholders=','.join('?' for _ in batch)
                        db.execute(f'DELETE FROM sources WHERE id IN ({placeholders})',batch)
            selected_sources=[source for source in data.get('sources',[]) if source.get('number') in numbers]
        else:
            selected_sources=[]
        selected_ids={source['id'] for source in selected_sources}
        announcements=[a for a in data.get('announcements',[]) if a.get('source_id') in selected_ids]
        announcement_ids={a['id'] for a in announcements}
        entities=[e for e in data.get('entities',[]) if e.get('announcement_id') in announcement_ids]
        entity_ids={e['id'] for e in entities}
        relations=[r for r in data.get('relations',[]) if r.get('announcement_id') in announcement_ids
                   and r.get('from') in entity_ids and r.get('to') in entity_ids]
        db.executemany('''INSERT INTO sources
            (id,number,date,pdf,canonical_pdf,sha256,source_url,extractor_version,extraction_state,importer_version)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',[(s['id'],s.get('number'),s.get('date'),s.get('pdf'),s.get('canonical_pdf'),
                s.get('sha256'),s.get('source_url'),s.get('extractor_version'),s.get('extraction_state'),s.get('importer_version')) for s in selected_sources])
        db.executemany('''INSERT INTO announcements
            (id,reference,source_id,category,pages,text,status,source_date,indexation_state,indexation_error,warning,title,title_quote,title_confidence,title_review_required,title_extraction_version)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',[(a['id'],a.get('reference'),a.get('source_id'),a.get('category'),
                json.dumps(a.get('pages',[])),a.get('text',''),a.get('status'),a.get('source_date'),
                a.get('indexation_state'),a.get('indexation_error'),a.get('warning'),a.get('title'),a.get('title_quote'),a.get('title_confidence'),a.get('title_review_required'),a.get('title_extraction_version')) for a in announcements])
        db.executemany('''INSERT INTO entities
            (id,name,type,cin,announcement_id,identifier,identifier_type,identity_match_status,is_primary,entity_extraction_version,identifier_quote)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''',[(e['id'],e.get('name'),e.get('type'),e.get('cin'),e.get('announcement_id'),
                e.get('identifier'),e.get('identifier_type'),e.get('identity_match_status'),e.get('is_primary'),
                e.get('entity_extraction_version'),e.get('identifier_quote')) for e in entities])
        db.executemany('''INSERT INTO relations
            (id,from_id,to_id,role,type,announcement_id,quote,status,event_type,event_date,event_date_quote,role_family)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',[(r['id'],r.get('from'),r.get('to'),r.get('role'),r.get('type','role_organisation'),
                r.get('announcement_id'),r.get('quote'),r.get('status'),r.get('event_type'),r.get('event_date'),r.get('event_date_quote'),r.get('role_family')) for r in relations])
        db.execute("INSERT INTO _jort_meta(key,value) VALUES('revision',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(current_revision,))
        if full_rebuild and db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise sqlite3.DatabaseError('Contrôle d’intégrité SQLite échoué après synchronisation complète.')
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def construire(year=2024, limit=None, cancel_file=None, only_number=None, defer_aggregate=False, force_number=None):
    annual = ROOT / 'data' / str(year)
    annual.mkdir(parents=True, exist_ok=True)
    manifest_path = ROOT / f'data/inventaire-{year}-fr.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    base_path = annual / 'base.json'
    previous = json.loads(base_path.read_text(encoding='utf-8')) if base_path.exists() else {}
    db_path = annual / f'jort-{year}-fr.sqlite'
    database_needs_repair = True
    if db_path.exists():
        with closing(sqlite3.connect(db_path)) as previous_db:
            try:
                stored_revision = previous_db.execute("SELECT value FROM _jort_meta WHERE key='revision'").fetchone()
                database_needs_repair = not stored_revision or stored_revision[0] != previous.get('revision')
            except sqlite3.OperationalError:
                database_needs_repair = True
    data = dict(previous)
    for key in ('sources', 'announcements', 'entities', 'relations'):
        data.setdefault(key, [])
    if previous.get('extractor_version', 0) < EXTRACTION_VERSION:
        for announcement in data['announcements']:
            announcement['indexation_state'] = 'a_traiter'
    data['extractor_version'] = EXTRACTION_VERSION
    checkpoint(base_path, data)
    sources_by_number = {s['number']: s for s in data['sources']}
    known_hashes = {s['sha256'] for s in data['sources']}
    failures, residuals, new_imports = [], [], 0
    new_source_ids = set()
    skipped = 0
    existing_announcement_ids = {a['id'] for a in data['announcements']}
    residual_path = annual / f'fragments-sans-reference-{year}.json'
    if residual_path.exists():
        residuals = json.loads(residual_path.read_text(encoding='utf-8'))
    residual_ids = {a.get('id') for a in residuals}

    for row in manifest['journals']:
        if only_number is not None and row.get('number') != only_number:
            continue
        if cancel_file and Path(cancel_file).exists():
            raise RuntimeError(f'Construction annulée avant publication (fichier présent : {cancel_file})')
        if row.get('status') != 'telecharge':
            continue
        previous_source = sources_by_number.get(row['number'])
        local_pdf = resolve_pdf_path(row['path'])
        try:
            file_hash = row.get('sha256')
            if not file_hash and local_pdf.is_file():
                digest = hashlib.sha256()
                with local_pdf.open('rb') as pdf_file:
                    for chunk in iter(lambda: pdf_file.read(1024 * 1024), b''):
                        digest.update(chunk)
                file_hash = digest.hexdigest()
        except OSError as exc:
            failures.append(dict(number=row['number'], error=f'Lecture du PDF impossible : {exc}'))
            continue
        if (previous_source and file_hash and previous_source.get('sha256') == file_hash
                and previous_source.get('importer_version') == IMPORT_VERSION
                and force_number != row['number']):
            skipped += 1
            continue
        if limit is not None and new_imports >= limit:
            continue
        try:
            item = None
            for attempt in range(1, 4):
                try:
                    item = importer(local_pdf, year=year, expected_date=row.get('date'), expected_number=row['number'])
                    break
                except Exception as exc:
                    print(f'Tentative {attempt}/3 échouée pour JAL {row["number"]}: {exc}', flush=True)
                    if ressource_indisponible(exc):
                        raise
                    if not erreur_temporaire(exc):
                        break
                    if attempt < 3:
                        time.sleep(attempt)
            if item is None:
                raise RuntimeError('Import impossible après trois tentatives')
            source = item['source'].copy()
            if source['sha256'] in known_hashes and not (
                    previous_source and previous_source.get('sha256') == source['sha256']
                    and (previous_source.get('importer_version') != IMPORT_VERSION
                         or force_number == row['number'])):
                for previous_source in data['sources']:
                    if previous_source['sha256']==source['sha256']:
                        previous_source['warnings']=item.get('warnings',[])
                continue
            if source['number'] != row['number'] or source['date'] != row['date']:
                raise ValueError('Métadonnées importées incohérentes avec inventaire')
            previous_source = sources_by_number.get(source['number'])
            if previous_source:
                old_source_ids = {previous_source['id']}
                removed_announcement_ids = {a['id'] for a in data['announcements'] if a.get('source_id') in old_source_ids}
                data['sources'] = [s for s in data['sources'] if s['id'] not in old_source_ids]
                data['announcements'] = [a for a in data['announcements'] if a.get('source_id') not in old_source_ids]
                data['entities'] = [e for e in data['entities'] if e.get('announcement_id') not in removed_announcement_ids]
                data['relations'] = [r for r in data['relations'] if r.get('announcement_id') not in removed_announcement_ids]
                residuals = [a for a in residuals if a.get('source_id') not in old_source_ids]
                residual_ids = {a.get('id') for a in residuals}
                known_hashes.discard(previous_source.get('sha256'))
                sources_by_number.pop(source['number'], None)
                existing_announcement_ids = {a['id'] for a in data['announcements']}
            source.update(source_url=row.get('url'), download_url=row.get('download_url'),
                          downloaded_at=row.get('downloaded_at'), origin=row.get('provenance'),
                          canonical_pdf=row.get('path') or source.get('pdf'), warnings=item.get('warnings', []),
                          extraction_state='en_attente', importer_version=IMPORT_VERSION)
            data['sources'].append(source)
            sources_by_number[source['number']] = source
            new_source_ids.add(source['id'])
            known_hashes.add(source['sha256'])
            new_imports += 1
            for announcement in item['announcements']:
                if announcement['id'] in existing_announcement_ids:
                    continue
                existing_announcement_ids.add(announcement['id'])
                if announcement['reference']:
                    pending = announcement.copy()
                    pending['indexation_state'] = 'a_traiter'
                    data['announcements'].append(pending)
                else:
                    if announcement['id'] not in residual_ids:
                        residuals.append(announcement)
                        residual_ids.add(announcement['id'])
            if not any(a.get('reference') for a in item['announcements']):
                source['extraction_state']='vide_a_verifier'
                source['extractor_version']=EXTRACTION_VERSION
                source.setdefault('warnings',[]).append('Aucune annonce exploitable extraite ; vérifier l’OCR et les pages source.')
            save_json(residual_path,residuals)
            print(f'Import ou mise à jour {new_imports} : JAL {source["number"]}', flush=True)
            checkpoint(base_path, data)
        except Exception as exc:
            if ressource_indisponible(exc):
                raise
            failures.append(dict(number=row['number'], error=str(exc)))
            print(f'ERREUR JAL {row["number"]}: {exc}', flush=True)

    if cancel_file and Path(cancel_file).exists():
        raise RuntimeError(f'Construction annulée avant publication (fichier présent : {cancel_file})')
    all_pending = [a for a in data['announcements'] if a.get('indexation_state') == 'a_traiter']
    pending_by_source = {}
    for announcement in all_pending:
        pending_by_source.setdefault(announcement.get('source_id') or announcement['id'], []).append(announcement)
    selected_source_ids = set(new_source_ids)
    if only_number is not None:
        selected = sources_by_number.get(only_number)
        selected_source_ids = {selected['id']} if selected else set()
    elif limit is None:
        # Une nouvelle version d'extracteur marque l'historique comme en attente.
        # En mode complet, toutes ces annonces doivent être réindexées, même si
        # aucun nouveau PDF n'a été importé pendant cette exécution.
        selected_source_ids.update(pending_by_source)
    else:
        for source_id in pending_by_source:
            if len(selected_source_ids) >= limit:
                break
            selected_source_ids.add(source_id)
    pending_announcements = [a for a in all_pending if (a.get('source_id') or a['id']) in selected_source_ids]
    pending_by_source = {}
    for announcement in pending_announcements:
        pending_by_source.setdefault(announcement.get('source_id') or announcement['id'], []).append(announcement)
    position = 0
    entities_by_announcement={}
    relations_by_announcement={}
    for entity in data['entities']:
        entities_by_announcement.setdefault(entity.get('announcement_id'),[]).append(entity)
    for relation in data['relations']:
        relations_by_announcement.setdefault(relation.get('announcement_id'),[]).append(relation)
    touched_source_ids=set(new_source_ids)
    synced_source_ids=set()
    for source in data['sources']:
        if source['id'] in selected_source_ids and not any(a.get('source_id')==source['id'] for a in data['announcements']):
            source.update(extractor_version=EXTRACTION_VERSION, extraction_state='vide_a_verifier')
            touched_source_ids.add(source['id'])
    for source_id, source_announcements in pending_by_source.items():
        touched_source_ids.add(source_id)
        for announcement in source_announcements:
            position += 1
            if cancel_file and Path(cancel_file).exists():
                checkpoint(base_path, data)
                raise RuntimeError(f'Construction annulée après sauvegarde du point de reprise : {cancel_file}')
            aid = announcement['id']
            entities_by_announcement[aid]=[]
            relations_by_announcement[aid]=[]
            last_error = None
            for attempt in range(1, 4):
                try:
                    extracted = enrichir([announcement])
                    entities_by_announcement[aid]=extracted['entities']
                    relations_by_announcement[aid]=extracted['relations']
                    announcement.pop('indexation_error', None)
                    announcement['indexation_state'] = 'terminee'
                    print(f'Indexation {position}/{len(pending_announcements)} : {announcement.get("reference") or aid}', flush=True)
                    last_error = None
                    break
                except Exception as exc:
                    if ressource_indisponible(exc):
                        checkpoint(base_path,data)
                        raise
                    last_error = exc
                    print(f'Tentative {attempt}/3 échouée pour l’annonce {aid}: {exc}', flush=True)
                    if not erreur_temporaire(exc):
                        break
                    if attempt < 3:
                        time.sleep(attempt)
            if last_error is not None:
                announcement['indexation_error'] = str(last_error)
                announcement['indexation_state']='a_traiter'
                failures.append(dict(announcement_id=aid, error=str(last_error)))
        # Point de reprise et graphe actualisés après chaque PDF terminé.
        changed_announcement_ids={a['id'] for a in source_announcements}
        source_entities=[entity for aid in changed_announcement_ids for entity in entities_by_announcement.get(aid,[])]
        source_relations=[relation for aid in changed_announcement_ids for relation in relations_by_announcement.get(aid,[])]
        data['entities']=[entity for entity in data['entities'] if entity.get('announcement_id') not in changed_announcement_ids]+source_entities
        data['relations']=[relation for relation in data['relations'] if relation.get('announcement_id') not in changed_announcement_ids]+source_relations
        source_row=next((s for s in data['sources'] if s['id']==source_id),None)
        if source_row and source_announcements and all(a.get('indexation_state')=='terminee' and not a.get('indexation_error') for a in source_announcements):
            source_row['extractor_version']=EXTRACTION_VERSION
            source_row['extraction_state']='terminee'
        elif source_row and not source_announcements:
            source_row['extractor_version']=EXTRACTION_VERSION
            source_row['extraction_state']='vide_a_verifier'
        checkpoint(base_path, data)
        if source_row:
            synchroniser_sqlite(annual/f'jort-{year}-fr.sqlite',data,{source_row['number']})
            synced_source_ids.add(source_id)
        if not defer_aggregate:
            reconstruire(ROOT)
    unsynced_source_ids=touched_source_ids-synced_source_ids
    if unsynced_source_ids:
        checkpoint(base_path,data)
        unsynced_numbers={s['number'] for s in data['sources'] if s['id'] in unsynced_source_ids}
        synchroniser_sqlite(annual/f'jort-{year}-fr.sqlite',data,unsynced_numbers)
    by_announcement = {a['id']: a for a in data['announcements']}
    by_entity = {e['id']: e for e in data['entities']}
    for edge in data['relations']:
        assert edge['from'] in by_entity and edge['to'] in by_entity
        announcement = by_announcement[edge['announcement_id']]
        assert edge['quote'] in ' '.join(announcement['text'].split()), edge['id']
        assert by_entity[edge['from']]['announcement_id'] == edge['announcement_id']
        assert by_entity[edge['to']]['announcement_id'] == edge['announcement_id']
    imported = {s['number'] for s in data['sources']}
    inventoried = {r['number'] for r in manifest['journals']}
    data['coverage'] = dict(year=year, language='fr', expected=manifest.get('expected'),
        downloaded=sum(r.get('status') == 'telecharge' for r in manifest['journals']), imported=len(imported),
        inventory_complete=manifest.get('inventory_complete', False), missing=sorted(inventoried-imported),
        errors=failures+[dict(number=r['number'], error=r.get('error', 'Non téléchargé')) for r in manifest['journals'] if r.get('status') != 'telecharge'])
    extracted_stats = dict(
        annonces=len(data['announcements']),
        organisations=sum(e['type'] in ('societe', 'association') for e in data['entities']),
        personnes=sum(e['type'] == 'personne' for e in data['entities']),
        relations=len(data['relations']),
        a_classer=sum(a.get('category') == 'a_classer' for a in data['announcements']),
        sans_organisation_reconnue=sum('organisation non reconnue dans le texte' in a.get('warning', '') for a in data['announcements']),
        ventes_fonds_a_verifier=sum('Vente de fonds repérée' in a.get('warning', '') for a in data['announcements']),
    )
    data['stats'] = dict(extracted_stats, pages=sum(s['pages'] for s in data['sources']),
        categories=dict(Counter(a['category'] for a in data['announcements'])), residual_fragments=len(residuals),
        announcements=len(data['announcements']), entities=len(data['entities']), relations=len(data['relations']))
    data['revision'] = revision(data)
    save_json(residual_path, residuals)
    save_json(annual / f'bilan-{year}.json', dict(coverage=data['coverage'], stats=data['stats']))
    save_json(base_path, data)
    db_path = annual / f'jort-{year}-fr.sqlite'
    # Les enregistrements ont été synchronisés par journal ; actualiser ici
    # seulement l'empreinte qui indique que le snapshot annuel est à jour.
    synchroniser_sqlite(db_path,data,revision_only=not database_needs_repair)
    combined = None if defer_aggregate else reconstruire(ROOT)
    completed = len(pending_announcements) - sum(bool(a.get('indexation_error')) for a in pending_announcements)
    print(f'{skipped} journal(aux) inchangé(s) ignoré(s) avant lecture PDF ; {completed} annonce(s) indexée(s) avec point de reprise individuel.', flush=True)
    print(json.dumps(dict(year=year, annual_stats=data['stats'],
        all_years=combined['stats'] if combined else 'regroupement différé au serveur'), ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--process-label', help=argparse.SUPPRESS)
    parser.add_argument('--year', type=int, default=2024)
    parser.add_argument('--limit', type=int, help='Nombre maximal de nouveaux PDF à importer; les PDF déjà indexés sont conservés.')
    parser.add_argument('--number', type=int, help='Traiter uniquement ce numéro de journal, après son téléchargement.')
    parser.add_argument('--force-number', type=int, help='Réextraire entièrement un journal déjà importé, sans retraiter les autres.')
    parser.add_argument('--defer-aggregate', action='store_true', help='Laisser le serveur appliquer les validations puis reconstruire le graphe une fois.')
    parser.add_argument('--cancel-file', type=Path, help='Annuler avant publication si ce fichier existe.')
    args = parser.parse_args()
    from datetime import datetime
    if not 1956 <= args.year <= datetime.now().year:
        parser.error('--year doit être entre 1956 et l’année actuelle.')
    if args.limit is not None and args.limit < 0:
        parser.error('--limit doit être positif ou nul.')
    if args.number is not None and args.number < 1:
        parser.error('--number doit être un numéro positif.')
    if args.force_number is not None and (args.force_number < 1 or args.number != args.force_number):
        parser.error('--force-number doit correspondre au numéro donné avec --number.')
    cancel_file = args.cancel_file or (ROOT / 'data' / str(args.year) / 'logs' / 'annulation.flag')
    try:
        construire(args.year, None if args.limit == 0 else args.limit, cancel_file, args.number, args.defer_aggregate, args.force_number)
    except Exception as exc:
        kind = 'temporary' if erreur_temporaire(exc) else ('resource' if ressource_indisponible(exc) else 'permanent')
        print('JORT_WORKER_ERROR ' + json.dumps(dict(kind=kind, message=str(exc)), ensure_ascii=False), flush=True)
        raise


if __name__ == '__main__':
    main()
