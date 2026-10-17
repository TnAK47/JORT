"""Réconciliation locale, reprenable, sans modèle ni téléchargement.

Simulation par défaut. --apply nécessite l'arrêt de l'application ; --resume
reprend le plan durable. Chaque groupe est validé séparément dans PostgreSQL.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import threading
import time
import uuid

from collecter_2024 import save_json
from extraction_structuree import normalize_name, organization_label
from postgresql_local import ROOT, connect, secret
from regles_identite import (RULE_VERSION, OFFICIAL_KINDS, FISCAL_KINDS, identifier_parts,
                            identifier_family, person_name, conflict_reason)


def now():
    return datetime.now(timezone.utc).isoformat()


def group_id(ids):
    return hashlib.sha256('\n'.join(sorted(ids)).encode()).hexdigest()


class Components:
    def __init__(self):
        self.parent = {}
        self.rank = {}

    def find(self, value):
        self.parent.setdefault(value, value)
        root = value
        while self.parent[root] != root:
            root = self.parent[root]
        while value != root:
            previous = self.parent[value]
            self.parent[value] = root
            value = previous
        return root

    def union(self, left, right):
        left, right = self.find(left), self.find(right)
        if left == right:
            return
        if self.rank.get(left, 0) < self.rank.get(right, 0):
            left, right = right, left
        self.parent[right] = left
        if self.rank.get(left, 0) == self.rank.get(right, 0):
            self.rank[left] = self.rank.get(left, 0) + 1


def extract_name_changes(text):
    """Exiger une phrase explicite ; une absorption seule n'est pas un renommage."""
    patterns = [
        r'(?:changement|modification)\s+de\s+(?:la\s+)?d[ée]nomination\s+sociale\s+'
        r'[^.;]{0,160}?(?:actuellement|anciennement)\s+d[ée]nomm[ée]e?\s+'
        r'(?P<old>[^,;\n]{2,180}?)\s*,?\s+(?:par|en)\s+(?P<new>[^\n;«»]{2,180})',
        r'ancienne\s+d[ée]nomination\s*[:=]\s*(?P<old>[^\n;]{2,180})\s*'
        r'(?:\n|;)\s*nouvelle\s+d[ée]nomination\s*[:=]\s*(?P<new>[^\n;«»]{2,180})',
    ]
    prepared = re.sub(r'(?<!\n)\n(?!\n|\s*[-•])', ' ', str(text or ''))
    seen = set()
    for pattern in patterns:
        for match in re.finditer(pattern, prepared, re.I):
            old = organization_label(match['old']).strip(' .,:"')
            new = organization_label(match['new']).strip(' .,:"')
            new = re.split(r'\s+[–—-]\s+|\s+\b(?:le|la)\s+modification\b', new, maxsplit=1, flags=re.I)[0].strip(' .,:"')
            if not (2 <= len(old) <= 150 and 2 <= len(new) <= 150):
                continue
            key = normalize_name(old), normalize_name(new)
            if key in seen or key[0] == key[1]:
                continue
            seen.add(key)
            yield dict(old_name=old, new_name=new, quote=' '.join(match[0].split())[:1200],
                       valid_from=None, date_precision='unknown')


class Progress:
    def __init__(self, directory, interval_seconds=1200):
        self.directory = directory
        self.path = directory / 'etat.json'
        self.log = directory / 'progression.log'
        self.state = dict(state='preparing', stage='initialisation', started_at=now(), updated_at=now(),
                          completed=0, total=None, merged=0, errors=0, blocked=0)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.interval = interval_seconds
        self.last_checkpoint = 0.0
        self.thread = threading.Thread(target=self._watch, name='JO-Reconciliation-Suivi', daemon=True)
        self.thread.start()

    def update(self, **values):
        with self.lock:
            self.state.update(values, updated_at=now())
            if time.monotonic() - self.last_checkpoint >= 2:
                save_json(self.path, self.state)
                self.last_checkpoint = time.monotonic()

    def emit(self, message=None):
        with self.lock:
            self.state['reported_at'] = now()
            save_json(self.path, self.state)
            state = dict(self.state)
        text = message or (f"{state['stage']} : {state.get('completed', 0)}/{state.get('total') or '?'} groupes ; "
                           f"{state.get('merged', 0)} fiches fusionnées ; {state.get('errors', 0)} erreurs ; "
                           f"{state.get('blocked', 0)} conflits ; reste estimé : {state.get('eta_minutes', 'non disponible')} min")
        line = f"{now()}  [JO-JORT] {text}"
        print(line, flush=True)
        with self.log.open('a', encoding='utf-8') as stream:
            stream.write(line + '\n')

    def _watch(self):
        while not self.stop_event.wait(self.interval):
            try:self.emit()
            except OSError as exc:print('JO-JORT : écriture du suivi impossible : '+str(exc),flush=True)

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=3)
        self.emit()


def open_plan(directory):
    directory.mkdir(parents=True, exist_ok=True)
    temp_directory=directory/'tmp'
    temp_directory.mkdir(exist_ok=True)
    os.environ['SQLITE_TMPDIR']=str(temp_directory)
    db = sqlite3.connect(directory / 'plan.sqlite')
    db.row_factory = sqlite3.Row
    db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        PRAGMA cache_size=-16384;
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY,public_id TEXT,nature TEXT,kind TEXT,
            label TEXT,normalized_name TEXT,person_key TEXT,created_at TEXT,label_locked INTEGER);
        CREATE INDEX IF NOT EXISTS entity_person ON entities(nature,person_key);
        CREATE INDEX IF NOT EXISTS entity_name ON entities(normalized_name);
        CREATE TABLE IF NOT EXISTS identifiers(entity_id TEXT,kind TEXT,raw_value TEXT,jurisdiction TEXT,
            family TEXT,value TEXT,letter TEXT,valid INTEGER,quote TEXT);
        CREATE INDEX IF NOT EXISTS identifier_key ON identifiers(family,value,jurisdiction);
        CREATE INDEX IF NOT EXISTS identifier_entity ON identifiers(entity_id);
        CREATE TABLE IF NOT EXISTS exclusions(key TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS changes(id TEXT PRIMARY KEY,announcement_id TEXT,publication_date TEXT,
            old_id TEXT,new_id TEXT,payload TEXT,state TEXT);
        CREATE TABLE IF NOT EXISTS groups(id TEXT PRIMARY KEY,payload TEXT,state TEXT NOT NULL,
            message TEXT,attempts INTEGER NOT NULL DEFAULT 0,result TEXT);
        CREATE INDEX IF NOT EXISTS group_state ON groups(state,id);
    ''')
    return db


def meta(db, key, value=None):
    if value is None:
        row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None
    db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))
    db.commit()
    return value


def stream_rows(db, sql, params=()):
    with db.cursor(name='jo_reconciliation_' + uuid.uuid4().hex) as cursor:
        cursor.execute(sql, params)
        while True:
            rows = cursor.fetchmany(1000)
            if not rows:break
            yield rows


def should_pause(directory):
    if (directory / 'pause.flag').exists():
        raise InterruptedError('Pause demandée ; reprise disponible.')


def snapshot(root, local, progress):
    progress.update(stage='lecture des entités et des identifiants', completed=0)
    meta(local,'plan_ready',False)
    for table in ('entities', 'identifiers', 'exclusions', 'changes', 'groups'):
        local.execute('DELETE FROM ' + table)
    local.commit()
    scanned = dict(entities=0, identifiers=0, invalid_identifiers=0)
    with connect(root, role='reader') as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        for rows in stream_rows(db, """SELECT id,public_id,nature,kind,label,normalized_name,created_at,label_locked
                FROM jort_entities WHERE status='active' AND nature IN
                ('personne_physique','personne_morale','organisation_a_qualifier') ORDER BY id"""):
            local.executemany('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?)', [
                (r['id'], r['public_id'], r['nature'], r['kind'], r['label'],
                 normalize_name(organization_label(r['label'])) if r['nature']!='personne_physique' else normalize_name(r['label']),
                 person_name(r['label']), str(r['created_at']), bool(r['label_locked'])) for r in rows])
            local.commit();scanned['entities']+=len(rows)
            progress.update(scanned_entities=scanned['entities']);should_pause(progress.directory)
        for rows in stream_rows(db, '''SELECT i.entity_id,i.kind,i.raw_value,i.jurisdiction,i.quote,e.nature
                FROM jort_identifier_observations i JOIN jort_mentions m ON m.id=i.mention_id
                JOIN jort_entities e ON e.id=i.entity_id
                WHERE i.active AND m.active AND i.attribution='explicite' AND e.status='active'
                AND i.kind=ANY(%s)''', (list(OFFICIAL_KINDS),)):
            values=[]
            for row in rows:
                parts=identifier_parts(row['kind'],row['raw_value'])
                expected_family=('cin',) if row['nature']=='personne_physique' else ('fiscal','rc') if row['nature']=='personne_morale' else ()
                valid=bool(parts and identifier_family(row['kind']) in expected_family and str(row['quote'] or '').strip())
                scanned['invalid_identifiers']+=not valid
                values.append((row['entity_id'],row['kind'],row['raw_value'],row['jurisdiction'],identifier_family(row['kind']),
                               parts[0] if parts else '',parts[1] if parts else '',valid,row['quote']))
            local.executemany('INSERT INTO identifiers VALUES(?,?,?,?,?,?,?,?,?)',values)
            local.commit();scanned['identifiers']+=len(rows)
            progress.update(scanned_identifiers=scanned['identifiers']);should_pause(progress.directory)
        local.executemany('INSERT OR IGNORE INTO exclusions VALUES(?)',[(r['key'],) for r in db.execute('SELECT key FROM jort_identity_exclusions')])
        meta(local,'source_counts',{table:db.execute('SELECT count(*) AS n FROM '+table).fetchone()['n']
                                   for table in ('jort_documents','jort_announcements','jort_mentions','jort_proofs','jort_edges')})
    meta(local,'snapshot_counts',scanned)


def _observations(local, ids):
    return [dict(row) for row in local.execute("SELECT *,1 AS active,'explicite' AS attribution FROM identifiers WHERE entity_id IN ("+
            ','.join('?' for _ in ids)+') AND valid',ids)]


def make_plan(root,local,progress):
    components=Components()
    progress.update(stage='regroupements par CIN, MF, RC et noms exacts')
    keys=local.execute('SELECT family,value,jurisdiction FROM identifiers WHERE valid GROUP BY family,value,jurisdiction HAVING count(DISTINCT entity_id)>1')
    for key in keys:
        ids=[r[0] for r in local.execute('SELECT DISTINCT entity_id FROM identifiers WHERE family=? AND value=? AND jurisdiction=? AND valid',tuple(key))
             if local.execute('SELECT 1 FROM entities WHERE id=?',(r[0],)).fetchone()]
        for other in ids[1:]:components.union(ids[0],other)
        should_pause(progress.directory)
    names=local.execute("SELECT person_key FROM entities WHERE nature='personne_physique' AND person_key<>'' GROUP BY person_key HAVING count(*)>1")
    for key in names:
        ids=[r[0] for r in local.execute("SELECT id FROM entities WHERE nature='personne_physique' AND person_key=?",tuple(key))]
        for other in ids[1:]:components.union(ids[0],other)
        should_pause(progress.directory)
    # Une variante purement rédactionnelle « Société/Ste X » ne désigne pas
    # une nouvelle personne morale. La fusion reste soumise aux contrôles MF,
    # RC et nature ci-dessous ; une différence de dénomination substantielle
    # ne passe jamais par cette règle.
    organizations=local.execute("""SELECT normalized_name FROM entities
        WHERE nature IN ('personne_morale','organisation_a_qualifier') AND normalized_name<>''
        GROUP BY normalized_name HAVING count(*)>1""")
    for row in organizations:
        key=row['normalized_name']
        rows=list(local.execute("""SELECT id,label FROM entities WHERE normalized_name=?
            AND nature IN ('personne_morale','organisation_a_qualifier') ORDER BY id""",(key,)))
        if not rows or not any(normalize_name(item['label'])!=key for item in rows):
            continue
        for other in rows[1:]:components.union(rows[0]['id'],other['id'])
        should_pause(progress.directory)
    progress.update(stage='lecture des changements de dénomination explicites')
    with connect(root,role='reader') as db:
        for rows in stream_rows(db, '''SELECT a.id,a.publication_date,a.original_text,
                coalesce((SELECT jsonb_agg(jsonb_build_object('id',m.entity_id,'name',m.name_as_published,'label',e.label))
                    FROM jort_mentions m JOIN jort_entities e ON e.id=m.entity_id
                    WHERE m.announcement_id=a.id AND m.active AND e.status='active' AND e.nature='personne_morale'),'[]'::jsonb) AS companies
                FROM jort_announcements a WHERE a.original_text ~* 'd[ée]nomination|anciennement[[:space:]]+d[ée]nomm' '''):
            for row in rows:
                for change in extract_name_changes(row['original_text']):
                    oldkey=normalize_name(change['old_name']);newkey=normalize_name(change['new_name'])
                    old_ids={r['id'] for r in row['companies'] if oldkey in (normalize_name(r['name']),normalize_name(r['label'])) or
                             (' '+normalize_name(r['name'])+' ').endswith(' '+oldkey+' ')}
                    new_ids={r[0] for r in local.execute("SELECT id FROM entities WHERE normalized_name=? AND nature='personne_morale'",(newkey,))}
                    state='candidate' if len({components.find(v) for v in old_ids})==1 and new_ids else 'review'
                    origin=min(old_ids) if old_ids else None
                    if state=='candidate':
                        for other in new_ids:components.union(origin,other)
                    change.update(announcement_id=row['id'],publication_date=str(row['publication_date']),old_entity_ids=sorted(old_ids),new_entity_ids=sorted(new_ids))
                    local.execute('INSERT OR REPLACE INTO changes VALUES(?,?,?,?,?,?,?)',
                                  (group_id([row['id'],oldkey,newkey]),row['id'],str(row['publication_date']),origin,
                                   min(new_ids) if new_ids else None,json.dumps(change,ensure_ascii=False),state))
            local.commit();should_pause(progress.directory)
    grouped=defaultdict(list)
    for value in components.parent:grouped[components.find(value)].append(value)
    for number,ids in enumerate(grouped.values(),1):
        should_pause(progress.directory)
        if len(ids)<2:continue
        rows=[];observations=[];reason=None
        if len(ids)>500:reason='Groupe anormalement volumineux : contrôle nécessaire'
        else:
            rows=[dict(r) for r in local.execute('SELECT * FROM entities WHERE id IN ('+','.join('?' for _ in ids)+')',ids)]
            observations=_observations(local,ids)
            reason=conflict_reason(rows,observations)
            keys=['entity:'+value for value in ids]
            if rows[0]['nature']=='personne_physique':keys.append('personne:nom-exact-presume:'+person_name(rows[0]['label']))
            if any(local.execute('SELECT 1 FROM exclusions WHERE key=?',(key,)).fetchone() for key in keys):reason='Séparation ou exclusion d’identité déjà décidée'
            if len({r['kind'] for r in rows})>1:reason='Types différents : société/association/organisation à vérifier'
        target=min(rows,key=lambda r:int(re.search(r'\d+$',r['public_id'])[0]))['id'] if rows else min(ids)
        presumed=bool(rows and rows[0]['nature']=='personne_physique' and not any(r['family']=='cin' for r in observations))
        cosmetic_organization=bool(rows and rows[0]['nature']!='personne_physique'
            and len({normalize_name(organization_label(r['label'])) for r in rows})==1
            and any(normalize_name(r['label'])!=normalize_name(organization_label(r['label'])) for r in rows))
        method=('same_name_presumed' if presumed else 'organization_prefix_normalized'
                if cosmetic_organization else 'official_identifier_reconciliation')
        item=dict(entity_ids=sorted(ids),target_id=target,method=method,
                  fingerprints={r['id']:dict(label=r['label'],kind=r['kind'],nature=r['nature']) for r in rows},reason=reason,
                  labels=sorted({r['label'] for r in rows}))
        local.execute('INSERT OR REPLACE INTO groups(id,payload,state,message) VALUES(?,?,?,?)',
                      (group_id(ids),json.dumps(item,ensure_ascii=False),'blocked' if reason else 'pending',reason))
        if number%100==0:local.commit();progress.update(planned_groups=number)
    local.commit();meta(local,'plan_ready',True);meta(local,'phase','planned')


@contextmanager
def exclusive_run(directory):
    with (directory/'execution.lock').open('a+b') as stream:
        if not stream.tell():stream.write(b'0');stream.flush()
        stream.seek(0)
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as exc:raise RuntimeError('Une réconciliation est déjà active.') from exc
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


def assert_application_stopped():
    with socket.socket() as probe:
        probe.settimeout(.5)
        if probe.connect_ex(('127.0.0.1',8765))==0:
            raise RuntimeError('Arrêter le site et ses batchs avant --apply. PostgreSQL seul doit rester démarré.')


def verify_backup(root,path):
    if not path.is_file() or not path.stat().st_size:return False
    check=subprocess.run([str(root/'tools/pgsql/bin/pg_restore.exe'),'--list',str(path)],stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return check.returncode==0


def backup(root,local,progress):
    old=meta(local,'backup')
    if old and verify_backup(root,Path(old)):return old
    if old:raise RuntimeError('Sauvegarde du point de reprise introuvable ou illisible ; intervention nécessaire.')
    progress.update(stage='sauvegarde PostgreSQL préalable')
    folder=root/'data/backups/postgresql';folder.mkdir(parents=True,exist_ok=True)
    destination=folder/('avant-reconciliation-'+meta(local,'run_id')+'.dump');partial=destination.with_suffix('.dump.partial')
    config=secret(root)
    with connect(root,role='reader') as db:size=db.execute('SELECT pg_database_size(current_database()) AS n').fetchone()['n']
    if shutil.disk_usage(folder).free<size*1.1:raise RuntimeError('Espace disque insuffisant pour garantir la sauvegarde préalable.')
    environment=dict(os.environ,PGHOST='127.0.0.1',PGPORT=str(config['port']),PGUSER=config['roles']['owner']['user'],
                     PGPASSWORD=config['roles']['owner']['password'],PGDATABASE='jort',PGAPPNAME='JO-JORT-RECONCILIATION-BACKUP')
    result=subprocess.run([str(root/'tools/pgsql/bin/pg_dump.exe'),'-Fc','-f',str(partial)],env=environment,
                          stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError('Sauvegarde PostgreSQL échouée ; aucune fusion lancée.')
    if not verify_backup(root,partial):raise RuntimeError('Sauvegarde illisible ; aucune fusion lancée.')
    os.replace(partial,destination);meta(local,'backup',str(destination));return str(destination)


def ensure_schema(root):
    with connect(root,role='owner') as db:
        sql=(root/'scripts/schema_postgresql.sql').read_text(encoding='utf-8')
        start=sql.index('CREATE TABLE IF NOT EXISTS jort_entity_name_history(')
        end=sql.index('CREATE TABLE IF NOT EXISTS jort_identifier_observations(',start)
        db.execute(sql[start:end],prepare=False)
        db.execute('ALTER TABLE jort_entity_name_history ADD COLUMN IF NOT EXISTS observed_on date')
        db.execute('''CREATE TABLE IF NOT EXISTS jort_reconciliation_checkpoints(run_id text NOT NULL,group_id text NOT NULL,
            result jsonb NOT NULL,committed_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(run_id,group_id))''')
        db.execute('GRANT SELECT ON jort_entity_name_history,jort_reconciliation_checkpoints TO jort_reader')
        db.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON jort_entity_name_history TO jort_writer')
    with connect(root,role='owner') as db:
        db.autocommit=True;db.execute("SET statement_timeout='30min'")
        invalid=db.execute("SELECT NOT indisvalid AS invalid FROM pg_index WHERE indexrelid=to_regclass('public.jort_names_search_trgm')").fetchone()
        if invalid and invalid['invalid']:db.execute('DROP INDEX CONCURRENTLY jort_names_search_trgm')
        db.execute('CREATE INDEX CONCURRENTLY IF NOT EXISTS jort_names_search_trgm ON jort_names USING gin(normalized_name gin_trgm_ops)')


def normalize_identifiers(root):
    # Mise à jour ensembliste et idempotente ; les raw_value et citations restent intacts.
    with connect(root,role='owner') as db:
        db.execute("SET LOCAL statement_timeout='10min'")
        db.execute('''WITH normalized AS (SELECT id,ltrim(substring(regexp_replace(upper(raw_value),'[[:space:]]','','g') from '^[0-9]+'),'0') AS value
            FROM jort_identifier_observations WHERE kind=ANY(%s)
            AND regexp_replace(upper(raw_value),'[[:space:]]','','g') ~ '^[0-9]{5,8}(/?[A-Z])?(/[A-Z0-9]{1,6}){0,4}$')
            UPDATE jort_identifier_observations i SET normalized_value=n.value FROM normalized n
            WHERE i.id=n.id AND n.value<>'' AND i.normalized_value IS DISTINCT FROM n.value''',(list(FISCAL_KINDS),))


def apply_one(root,local,run_id,key,item):
    from base_postgresql import merge_entities,record_name_history,j
    from qualite_entites import libelle_documentaire
    with connect(root,role='owner') as db:
        db.execute("SET LOCAL application_name='JO-JORT-RECONCILIATION'")
        committed=db.execute('SELECT result FROM jort_reconciliation_checkpoints WHERE run_id=%s AND group_id=%s',(run_id,key)).fetchone()
        if committed:return committed['result']
        ids=item['entity_ids'];current=db.execute('SELECT * FROM jort_entities WHERE id=ANY(%s) ORDER BY id FOR UPDATE',(ids,)).fetchall()
        if len(current)!=len(ids) or any(r['status']!='active' for r in current):raise ValueError('Plan périmé pour ce groupe ; des fiches ont déjà changé.')
        for row in current:
            expected=item['fingerprints'][row['id']]
            if any(row[k]!=expected[k] for k in expected):raise ValueError('Identité modifiée depuis la préparation du plan.')
        observations=db.execute("SELECT * FROM jort_identifier_observations WHERE entity_id=ANY(%s) AND active AND attribution='explicite'",(ids,)).fetchall()
        reason=conflict_reason(current,observations)
        if reason:raise ValueError(reason)
        target=item['target_id'];byid={r['id']:r for r in current};locked=[r for r in current if r['label_locked']]
        if len({normalize_name(r['label']) for r in locked})>1:raise ValueError('Plusieurs noms verrouillés : arbitrage humain nécessaire.')
        candidates=db.execute('''SELECT m.name_as_published,a.publication_date FROM jort_mentions m
            JOIN jort_announcements a ON a.id=m.announcement_id WHERE m.entity_id=ANY(%s) AND m.active
            ORDER BY a.publication_date DESC NULLS LAST,m.id LIMIT 120''',(ids,)).fetchall()
        label=locked[0]['label'] if locked else next((r['name_as_published'] for r in candidates if not libelle_documentaire(r['name_as_published'],byid[target]['kind'])),byid[target]['label'])
        changes=[json.loads(r['payload']) for r in local.execute("SELECT payload FROM changes WHERE state='candidate' AND old_id IN ("+','.join('?' for _ in ids)+')',ids)]
        if changes and not locked:
            latest=max(changes,key=lambda c:c['publication_date'])
            # Ne pas remplacer un nom publié plus récemment par un ancien renommage.
            recent_date=str(candidates[0]['publication_date']) if candidates else ''
            if latest['publication_date']>=recent_date:label=latest['new_name']
        result=merge_entities(db,[target,*[v for v in ids if v!=target]],
                              'Réconciliation locale '+run_id+' : identifiants ou dénomination explicite ; preuves conservées.',method=item['method'],label=label)
        # Les dates de publication ne sont pas des dates de prise d'effet.
        for row in db.execute('''SELECT DISTINCT m.name_as_published,a.id,a.publication_date
                FROM jort_mentions m JOIN jort_announcements a ON a.id=m.announcement_id WHERE m.entity_id=%s AND m.active''',(target,)):
            record_name_history(db,target,row['name_as_published'],'published',row['id'],observed_on=row['publication_date'],method='published_name')
        for change in changes:
            for name,status in ((change['old_name'],'former'),(change['new_name'],'published')):
                record_name_history(db,target,name,status,change['announcement_id'],quote=change['quote'],method='source_explicit',observed_on=change['publication_date'])
        result.update(merged=len(ids)-1,label=label)
        db.execute('INSERT INTO jort_reconciliation_checkpoints(run_id,group_id,result) VALUES(%s,%s,%s)',(run_id,key,j(result)))
        return result


def counts(local):
    states={r[0]:r[1] for r in local.execute('SELECT state,count(*) FROM groups GROUP BY state')}
    merged=local.execute("SELECT coalesce(sum(json_extract(result,'$.merged')),0) FROM groups WHERE state='done'").fetchone()[0]
    return dict(total=sum(states.values()),completed=states.get('done',0),merged=merged,pending=states.get('pending',0),
                errors=states.get('error',0),blocked=states.get('blocked',0))


def apply_plan(root,local,progress,limit_groups=0,retry_errors=False,retry_blocked=False):
    assert_application_stopped()
    if retry_errors:local.execute("UPDATE groups SET state='pending' WHERE state='error'");local.commit()
    if retry_blocked:local.execute("UPDATE groups SET state='pending', message=NULL WHERE state='blocked'");local.commit()
    backup(root,local,progress);should_pause(progress.directory)
    progress.update(stage='création des index et normalisation')
    ensure_schema(root);normalize_identifiers(root);meta(local,'phase','applying')
    started=time.monotonic();processed=0;current=counts(local);run_id=meta(local,'run_id')
    rows=local.execute("SELECT id,payload FROM groups WHERE state='pending' ORDER BY id")
    for row in rows:
        should_pause(progress.directory)
        if limit_groups and processed>=limit_groups:break
        try:
            result=apply_one(root,local,run_id,row['id'],json.loads(row['payload']))
            local.execute("UPDATE groups SET state='done',attempts=attempts+1,result=?,message=NULL WHERE id=?",(json.dumps(result),row['id']))
            current['completed']+=1;current['merged']+=result['merged']
        except Exception as exc:
            # Une panne de base/disque est globale : conserver les groupes
            # suivants en attente, sans multiplier des milliers d'échecs.
            from psycopg import OperationalError,InterfaceError
            if isinstance(exc,(OperationalError,InterfaceError,OSError)) or str(getattr(exc,'sqlstate','')).startswith(('08','28','53','57','58')):
                raise
            local.execute("UPDATE groups SET state='error',attempts=attempts+1,message=? WHERE id=?",(f'{type(exc).__name__}: {exc}'[:1500],row['id']))
            current['errors']+=1
        local.commit();processed+=1;current['pending']-=1
        progress.update(stage='fusion des entités',**current,current_group=row['id'],
                        eta_minutes=round((time.monotonic()-started)/processed*current['pending']/60,1))
    progress.update(stage='contrôle des preuves et publication')
    with connect(root,role='reader') as db:
        for table,old in meta(local,'source_counts').items():
            if db.execute('SELECT count(*) AS n FROM '+table).fetchone()['n']!=old:
                raise RuntimeError('Nombre de preuves ou sources modifié : '+table+' ; publication différée.')
    from migration_postgresql import publish
    publish(root)
    with connect(root,role='owner') as db:
        for table in ('jort_entities','jort_names','jort_mentions','jort_edges','jort_announcements','jort_entity_name_history'):db.execute('ANALYZE '+table)
    current=counts(local);phase='paused' if current['pending'] else 'done_with_issues' if current['errors'] or current['blocked'] else 'done'
    meta(local,'phase',phase);progress.update(state=phase,stage='terminé' if not current['pending'] else 'échantillon terminé — reprise disponible',**current)


def report(local,directory):
    result=dict(run_id=meta(local,'run_id'),rules=RULE_VERSION,phase=meta(local,'phase'),updated_at=now(),
                backup=meta(local,'backup'),**counts(local),snapshot=meta(local,'snapshot_counts'))
    result['changes']={r[0]:r[1] for r in local.execute('SELECT state,count(*) FROM changes GROUP BY state')}
    result['details']='plan.sqlite ; tables groups et changes (inclut les conflits et erreurs)'
    # Rapport lisible dans un tableur, limité aux vrais arbitrages et erreurs.
    # Aucun CIN/MF brut n'est recopié dans ce fichier de synthèse.
    with (directory/'a-verifier.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        out=csv.writer(stream,delimiter=';');out.writerow(('type','motif','identifiants_entites','noms','annonce'))
        def safe_cell(value):
            value=str(value or '')
            return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
        for row in local.execute("SELECT payload,state,message FROM groups WHERE state IN ('blocked','error') ORDER BY state,id"):
            item=json.loads(row['payload'])
            out.writerow(tuple(safe_cell(v) for v in (row['state'],row['message'],' | '.join(item['entity_ids']),' | '.join(item['labels']),'')))
        for row in local.execute("SELECT announcement_id,payload FROM changes WHERE state='review'"):
            item=json.loads(row['payload'])
            out.writerow(tuple(safe_cell(v) for v in ('denomination','Rattachement historique à confirmer','',' → '.join(filter(None,(item.get('old_name'),item.get('new_name')))),row['announcement_id'])))
    result['manual_review']='a-verifier.csv'
    save_json(directory/'rapport.json',result);return result


def run(root=ROOT,apply=False,resume=False,interval_minutes=20,limit_groups=0,retry_errors=False,retry_blocked=False):
    root=Path(root).resolve()
    if root!=ROOT.resolve():raise ValueError('Ce lanceur est limité au dossier du projet JORT.')
    directory=root/'data/maintenance/reconciliation-entites';directory.mkdir(parents=True,exist_ok=True)
    with exclusive_run(directory),closing(open_plan(directory)) as local:
        if not resume and meta(local,'phase') in ('applying','paused','failed'):
            raise RuntimeError('Un plan attend une reprise : utiliser --resume.')
        if resume and meta(local,'rules')!=RULE_VERSION:raise RuntimeError('Version de règles différente ; réexaminer le plan avant reprise.')
        if resume:(directory/'pause.flag').unlink(missing_ok=True)
        if apply:assert_application_stopped()
        progress=Progress(directory,interval_minutes*60)
        try:
            if not resume:
                meta(local,'run_id',datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8])
                meta(local,'rules',RULE_VERSION);meta(local,'phase','planning');meta(local,'backup','')
                snapshot(root,local,progress);make_plan(root,local,progress)
            elif not meta(local,'plan_ready'):
                snapshot(root,local,progress);make_plan(root,local,progress)
            progress.update(state='running' if apply else 'planned',run_id=meta(local,'run_id'),**counts(local))
            progress.emit('Plan préparé. '+('Application des groupes compatibles.' if apply else 'Simulation seulement ; aucune fusion exécutée.'))
            if apply:apply_plan(root,local,progress,limit_groups,retry_errors,retry_blocked)
            else:progress.update(state='planned',stage='simulation terminée',**counts(local))
        except (InterruptedError,KeyboardInterrupt):
            meta(local,'phase','paused');progress.update(state='paused',stage='pause — point de reprise conservé',**counts(local))
        except Exception as exc:
            meta(local,'phase','failed');progress.update(state='failed',stage='arrêt sur erreur de structure ou de connexion',error=f'{type(exc).__name__}: {exc}',**counts(local))
            raise
        finally:report(local,directory);progress.close()
    return result_from_file(directory/'rapport.json')


def result_from_file(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'state':'not_started'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true',help='Sauvegarder puis appliquer les groupes compatibles.')
    parser.add_argument('--resume',action='store_true',help='Reprendre le plan durable, sans rescanner les groupes terminés.')
    parser.add_argument('--status',action='store_true',help='Afficher l’état local sans accéder à PostgreSQL.')
    parser.add_argument('--pause',action='store_true',help='Demander une pause après le groupe actif.')
    parser.add_argument('--interval-minutes',type=int,default=20)
    parser.add_argument('--limit-groups',type=int,default=0,help='Premier passage limité à N groupes ; 0 = tous.')
    parser.add_argument('--retry-errors',action='store_true',help='Retenter les groupes en erreur pendant une reprise.')
    parser.add_argument('--retry-blocked',action='store_true',help='Retenter les groupes bloques apres evolution des regles.')
    args=parser.parse_args()
    if args.interval_minutes<1 or args.limit_groups<0:parser.error('Intervalle positif et limite supérieure ou égale à zéro requis.')
    if (args.retry_errors or args.retry_blocked) and not (args.apply and args.resume):parser.error('--retry-errors/--retry-blocked exige --apply --resume.')
    directory=ROOT/'data/maintenance/reconciliation-entites'
    if args.status:print(json.dumps(result_from_file(directory/'etat.json'),ensure_ascii=False,indent=2));return 0
    if args.pause:directory.mkdir(parents=True,exist_ok=True);(directory/'pause.flag').touch();print('Pause demandée au prochain point de reprise.');return 0
    def pause_signal(*_):
        directory.mkdir(parents=True,exist_ok=True);(directory/'pause.flag').touch()
    signal.signal(signal.SIGINT,pause_signal)
    signal.signal(signal.SIGTERM,pause_signal)
    try:
        result=run(apply=args.apply,resume=args.resume,interval_minutes=args.interval_minutes,
                   limit_groups=args.limit_groups,retry_errors=args.retry_errors,retry_blocked=args.retry_blocked)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['phase'] in ('done','planned','paused') else 2
    except Exception as exc:print(f'JO-JORT : {type(exc).__name__}: {exc}',flush=True);return 1


if __name__=='__main__':
    raise SystemExit(main())
