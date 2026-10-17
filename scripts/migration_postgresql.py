"""Migration progressive et reprenable, sans téléchargement ni écrasement des archives."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from collecter_2024 import save_json
from postgresql_local import ROOT,connect,enabled
from base_postgresql import Resolver,bootstrap_registry,ingest_document,j,source_signature
from extraire_entites import ENTITY_EXTRACTION_VERSION


def source_rows(root,years=None,numbers=None):
    for path in sorted((Path(root)/'data').glob('[0-9][0-9][0-9][0-9]/jort-*-fr.sqlite'),reverse=True):
        year=int(path.parent.name)
        if years and year not in years:continue
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
            db.row_factory=sqlite3.Row
            for source in db.execute('SELECT * FROM sources ORDER BY number DESC'):
                if numbers and source['number'] not in numbers:continue
                records=[dict(a) for a in db.execute('SELECT * FROM announcements WHERE source_id=? ORDER BY id',(source['id'],))]
                if not records:continue
                for a in records:
                    a['pages']=json.loads(a.get('pages') or '[]')
                    a['source_date']=source['date']
                src=dict(source)
                src['pages']=max((p for a in records for p in a['pages']),default=0)
                signature=source_signature(src,records)
                src['_legacy_entities']=[dict(r) for r in db.execute('SELECT e.* FROM entities e JOIN announcements a ON a.id=e.announcement_id WHERE a.source_id=?',(source['id'],))]
                src['_legacy_relations']=[dict(r) for r in db.execute('SELECT r.* FROM relations r JOIN announcements a ON a.id=r.announcement_id WHERE a.source_id=?',(source['id'],))]
                yield src,records,signature


def publish(root=ROOT):
    with connect(root) as db:
        db.execute('''DELETE FROM jort_entity_metrics metrics WHERE NOT EXISTS
            (SELECT 1 FROM jort_mentions m WHERE m.entity_id=metrics.entity_id AND m.active)''')
        db.execute('''INSERT INTO jort_entity_metrics SELECT m.entity_id,count(*),coalesce(max(d.n),0)
            FROM jort_mentions m LEFT JOIN (SELECT id,count(*) AS n FROM (SELECT a AS id FROM jort_edges WHERE active UNION ALL SELECT b AS id FROM jort_edges WHERE active) x GROUP BY id) d ON d.id=m.entity_id
            WHERE m.active GROUP BY m.entity_id ON CONFLICT(entity_id) DO UPDATE SET occurrences=excluded.occurrences,relations=excluded.relations''')
        notices=db.execute('SELECT count(*) AS count,coalesce(sum((NOT search_visible)::integer),0) AS convocations,coalesce(sum((extraction_quality=\'partielle\')::integer),0) AS partial FROM jort_announcements').fetchone()
        types={r['category']:r['count'] for r in db.execute('SELECT category,count(*) AS count FROM jort_announcements GROUP BY category')}
        counts=db.execute("SELECT count(*) AS entities,count(*) FILTER(WHERE kind='personne') AS persons,count(*) FILTER(WHERE nature='personne_morale') AS organizations FROM jort_entities e WHERE status='active' AND displayable AND EXISTS(SELECT 1 FROM jort_mentions m WHERE m.entity_id=e.id AND m.active)").fetchone()
        edges=db.execute('SELECT count(*) AS count FROM jort_edges WHERE active').fetchone()['count']
        docs=db.execute('SELECT count(*) AS count,sum(pages) AS pages,array_agg(DISTINCT year ORDER BY year) AS years FROM jort_documents').fetchone()
        revision=db.execute('SELECT max(updated_at) AS time,count(*) AS n FROM jort_migration_checkpoints').fetchone()
        identity_revision=db.execute('SELECT max(created_at) AS time,count(*) AS n FROM jort_identity_decisions').fetchone()
        stamp=hashlib.sha256((str(revision)+str(identity_revision)).encode()).hexdigest()
        stats=dict(announcements=notices['count'],entities=db.execute('SELECT count(*) AS n FROM jort_mentions WHERE active').fetchone()['n'],relations=edges,
            unique_entities=counts['entities'],consultable_entities=counts['entities'],unique_persons=counts['persons'],persons=counts['persons'],organizations=counts['organizations'],
            categories=types,pages=docs['pages'] or 0,residual_fragments=0,convocations=notices['convocations'],partial_extractions=notices['partial'])
        metadata=dict(version=2,engine='postgresql',revision=stamp,stats=stats,coverage=dict(years=docs['years'] or [],available_years=docs['years'] or [],known=docs['count'],imported=docs['count'],complete=False,inventory_complete=False,expected=None),identity_summary=dict(presumed=True,official_identifiers_preserved=True),updated_at=datetime.now(timezone.utc).isoformat())
        db.execute('INSERT INTO jort_meta VALUES(\'bootstrap\',%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(j(metadata),))
    marker=dict(state='ready',engine='postgresql',revision=stamp,years=docs['years'] or [],counts=dict(sources=docs['count'],announcements=notices['count'],entities=stats['entities'],relations=edges))
    save_json(Path(root)/'data/maintenance/publication-postgresql.json',marker)
    if enabled(root):save_json(Path(root)/'dist/publication.json',marker)
    return metadata


def migrate(root=ROOT,years=None,limit=0,numbers=None):
    root=Path(root);bootstrap_registry(root);resolver=Resolver(root)
    report=dict(state='running',started_at=datetime.now(timezone.utc).isoformat(),documents=0,skipped=0,announcements=0,errors=[],extraction_version=ENTITY_EXTRACTION_VERSION)
    output=root/'data/maintenance/migration-postgresql.json'
    save_json(output,report)
    try:
        for source,records,signature in source_rows(root,years,numbers):
            if (root/'data/maintenance/pause-migration-postgresql.flag').exists():report['state']='paused';break
            with connect(root,role='reader') as db:
                old=db.execute('SELECT * FROM jort_migration_checkpoints WHERE document_id=%s',(source['id'],)).fetchone()
            if old and old['source_signature']==signature and old['extraction_version']==ENTITY_EXTRACTION_VERSION and old['state']=='done':report['skipped']+=1;continue
            try:
                result=ingest_document(root,source,records,resolver,signature)
                report['documents']+=1;report['announcements']+=result['announcements']
            except Exception as exc:
                resolver.clear();report['errors'].append(dict(year=int(str(source['date'])[:4]),number=source['number'],source_id=source['id'],error=str(exc)))
            report.update(year=int(str(source['date'])[:4]),number=source['number'],updated_at=datetime.now(timezone.utc).isoformat())
            save_json(output,report)
            print(json.dumps({k:report[k] for k in ('documents','skipped','announcements','year','number')}|{'errors':len(report['errors'])}),flush=True)
            if limit and report['documents']>=limit:break
        if report['state']=='running':report['state']='attention' if report['errors'] else 'sample' if years or numbers or limit else 'done'
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        if report['documents']:report['publication_revision']=publish(root)['revision']
    finally:
        resolver.close();save_json(output,report)
    return report


def sync_changed(root=ROOT):
    """Réutiliser les années inchangées et leurs points de reprise par journal."""
    root=Path(root)
    revisions={}
    for path in (root/'data').glob('[0-9][0-9][0-9][0-9]/jort-*-fr.sqlite'):
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute("SELECT value FROM _jort_meta WHERE key='revision'").fetchone()
            revisions[path.parent.name]=row[0] if row else str(path.stat().st_mtime_ns)
    with connect(root,role='reader') as db:
        record=db.execute("SELECT value FROM jort_meta WHERE key='annual_revisions'").fetchone()
        previous=record['value'] if record else {}
    changed=[int(y) for y,v in revisions.items() if previous.get(y)!=v]
    resolver=Resolver(root)
    try:
        for source,records,signature in source_rows(root,changed) if changed else []:
            with connect(root,role='reader') as db:
                existing=db.execute('SELECT source_signature,extraction_version FROM jort_migration_checkpoints WHERE document_id=%s',(source['id'],)).fetchone()
            if existing and existing['source_signature']==signature and existing['extraction_version']==ENTITY_EXTRACTION_VERSION:
                with connect(root) as db:db.execute('UPDATE jort_documents SET payload=%s WHERE id=%s',(j({k:v for k,v in source.items() if not k.startswith('_')}),source['id']))
                continue
            ingest_document(root,source,records,resolver,signature)
    finally:resolver.close()
    with connect(root) as db:db.execute('INSERT INTO jort_meta VALUES(\'annual_revisions\',%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(j(revisions),))
    return publish(root)


def validate(root=ROOT):
    with connect(root,role='reader') as db:
        checks={
            'source_proofs':db.execute('SELECT count(*) AS n FROM jort_edges WHERE active AND coalesce(payload->>\'quote\',\'\')=\'\'').fetchone()['n']==0,
            'public_ids':db.execute("SELECT count(*) AS n FROM jort_entities WHERE public_id='' OR public_id IS NULL").fetchone()['n']==0,
            'source_text':db.execute("SELECT count(*) AS n FROM jort_announcements WHERE original_text IS NULL").fetchone()['n']==0,
        }
        examples=[]
        for reference in ('2017401317SANB2','2012G07567SANB2','2026R02548FCCC2'):
            a=db.execute('SELECT id,title FROM jort_announcements WHERE reference=%s LIMIT 1',(reference,)).fetchone()
            if a:
                edges=db.execute('SELECT payload FROM jort_edges WHERE announcement_id=%s AND active',(a['id'],)).fetchall()
                names=db.execute('SELECT name_as_published FROM jort_mentions WHERE announcement_id=%s AND active',(a['id'],)).fetchall()
                examples.append(dict(reference=reference,title=a['title'],names=[x['name_as_published'] for x in names],roles=[x['payload'].get('role') for x in edges]))
                if reference=='2017401317SANB2':checks['one_tech_dg']=any(x['payload'].get('role')=='directeur général' for x in edges) and any('Khaled Abdeljaoued' in x['name_as_published'] for x in names)
        total=db.execute('SELECT count(*) AS n FROM jort_announcements').fetchone()['n']
    report=dict(state='ok' if all(checks.values()) else 'attention',checks=checks,examples=examples,announcements=total)
    save_json(Path(root)/'data/maintenance/validation-postgresql.json',report);return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--years',nargs='*',type=int);parser.add_argument('--numbers',nargs='*',type=int);parser.add_argument('--limit',type=int,default=0);parser.add_argument('--validate',action='store_true');parser.add_argument('--activate',action='store_true');parser.add_argument('--resume',action='store_true');parser.add_argument('--label',default='JO-JORT-MIGRATION')
    args=parser.parse_args()
    if args.validate:print(json.dumps(validate(),ensure_ascii=True))
    elif args.activate:
        readiness=ROOT/'data/maintenance/integration-postgresql-validee.json'
        if not readiness.exists() or not json.loads(readiness.read_text(encoding='utf-8')).get('ready'):
            raise RuntimeError('Bascule reportée : les parcours API et administration doivent encore être validés.')
        report=validate()
        if report['state']!='ok':raise RuntimeError('Validation PostgreSQL échouée : bascule refusée.')
        saved=ROOT/'data/maintenance/migration-postgresql.json'
        state=json.loads(saved.read_text(encoding='utf-8'))
        if state.get('errors') or state.get('state')!='done':raise RuntimeError('Migration incomplète : bascule refusée.')
        save_json(ROOT/'data/config/base.json',dict(engine='postgresql',state='active',schema_version=1))
        publish();print('{"engine":"postgresql","state":"active"}')
    else:
        if args.resume:(ROOT/'data/maintenance/pause-migration-postgresql.flag').unlink(missing_ok=True)
        migrate(years=args.years,limit=args.limit,numbers=args.numbers)
