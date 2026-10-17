"""Référentiel global : ingestion atomique par document et identité réversible."""
from collections import OrderedDict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from psycopg.types.json import Jsonb
from postgresql_local import connect
from extraction_structuree import normalize_name,organization_label
from qualite_entites import libelle_documentaire
from extraire_entites import enrichir,ENTITY_EXTRACTION_VERSION
from regles_identite import canonical_identifier,identifier_family,identifier_parts,conflict_reason,person_name,FISCAL_KINDS,RC_KINDS


def nature(kind):
    return 'personne_physique' if kind=='personne' else 'actif_commercial' if kind=='fonds_commerce' else 'personne_morale' if kind in ('societe','association') else 'organisation_a_qualifier'


def j(value):return Jsonb(value)


def record_name_history(db,entity_id,name,name_status,announcement_id=None,valid_from=None,valid_to=None,quote=None,method='source_explicit',observed_on=None):
    """Conserver un nom historique daté sans modifier la preuve d'origine."""
    normalized=normalize_name(name)
    key='|'.join(str(v or '') for v in (entity_id,normalized,name_status,announcement_id))
    hid='nom-'+hashlib.sha256(key.encode('utf-8')).hexdigest()
    db.execute('''INSERT INTO jort_entity_name_history
        (id,entity_id,name,normalized_name,name_status,valid_from,valid_to,announcement_id,quote,method,observed_on)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(entity_id,normalized_name,name_status,announcement_id) DO UPDATE SET
        name=excluded.name,valid_from=coalesce(excluded.valid_from,jort_entity_name_history.valid_from),
        valid_to=coalesce(excluded.valid_to,jort_entity_name_history.valid_to),
        quote=coalesce(excluded.quote,jort_entity_name_history.quote),method=excluded.method,
        observed_on=coalesce(excluded.observed_on,jort_entity_name_history.observed_on)''',
        (hid,entity_id,name,normalized,name_status,valid_from,valid_to,announcement_id,quote,method,observed_on))
    return hid


def bootstrap_registry(root):
    """Importer IDs et décisions existants avant de réextraire, sans les réinventer."""
    with connect(root,role='owner') as db:
        if db.execute("SELECT 1 FROM jort_meta WHERE key='registry_imported'").fetchone():return
        p=Path(root)/'data/referentiel-entites.sqlite'
        if p.exists():
            with closing(sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True)) as old:
                old.row_factory=sqlite3.Row
                with db.cursor().copy('COPY jort_entities(id,public_id,kind,nature,label,normalized_name,status,identity_status,label_locked,displayable,created_at) FROM STDIN') as out:
                    for e in old.execute('SELECT * FROM canonical_entities'):
                        label=organization_label(e['label']) if e['type']!='personne' else e['label']
                        out.write_row((e['id'],e['public_id'],e['type'],nature(e['type']),label,normalize_name(label),e['status'],'legacy_presumed',bool(e['label_locked']),not libelle_documentaire(label,e['type']),e['created_at']))
                with db.cursor().copy('COPY jort_identity_decisions(id,target_id,decision,method,reason,created_at,undone_at,snapshot) FROM STDIN') as out:
                    for e in old.execute('SELECT * FROM merge_events'):
                        out.write_row((e['id'],e['target_id'],'merge',e['method'],e['reason'],e['created_at'],e['undone_at'],e['snapshot_json']))
                db.cursor().executemany('UPDATE jort_entities SET redirect_to=%s WHERE id=%s',[(r['new_id'],r['old_id']) for r in old.execute('SELECT * FROM entity_redirects')])
                db.cursor().executemany('INSERT INTO jort_identity_exclusions VALUES(%s) ON CONFLICT DO NOTHING',[(r[0],) for r in old.execute('SELECT key FROM identity_exclusions')])
        maximum=db.execute("SELECT coalesce(max(substring(public_id from '[0-9]+$')::bigint),0) AS n FROM jort_entities").fetchone()['n']
        db.execute('SELECT setval(\'jort_public_id_seq\',%s,false)',(maximum+1,))
        db.execute("INSERT INTO jort_meta VALUES('registry_imported',%s)",(j({'preserved':True}),))


class Resolver:
    def __init__(self,root):
        self.root=Path(root);self.cache=OrderedDict()
        p=self.root/'data/referentiel-entites.sqlite'
        self.legacy=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True) if p.exists() else None

    def close(self):
        if self.legacy:self.legacy.close()

    def clear(self):self.cache.clear()

    def resolve(self,db,e):
        kind=e['type'];label=e.get('canonical_name_candidate') or (organization_label(e['name']) if kind!='personne' else e['name']);normalized=normalize_name(label)
        observation=[]
        if e.get('cin') and kind=='personne':observation.append(('cin',str(e['cin']),e.get('cin_quote','')))
        if e.get('identifiers'):observation.extend((v['kind'],v['value'],v.get('quote','')) for v in e['identifiers'])
        elif e.get('identifier'):observation.append((e.get('identifier_type') or 'non_qualifie',str(e['identifier']),''))
        normalized_ids=[(k,canonical_identifier(k,v)) for k,v,q in observation]
        old=self.legacy.execute('SELECT canonical_id,name,resolution FROM entity_occurrences WHERE id=?',(e['id'],)).fetchone() if self.legacy else None
        if self.legacy and (not old or normalize_name(organization_label(old[1]) if kind!='personne' else old[1])!=normalized):
            old=next((r for r in self.legacy.execute('SELECT canonical_id,name,resolution FROM entity_occurrences WHERE announcement_id=? AND type=?',(e['announcement_id'],kind))
                      if normalize_name(organization_label(r[1]) if kind!='personne' else r[1])==normalized),None)
        original=old[0] if old and normalize_name(organization_label(old[1]) if kind!='personne' else old[1])==normalized else None
        key=(kind,normalized,tuple(normalized_ids),e['announcement_id'] if kind=='fonds_commerce' else '')
        if key in self.cache and not original:
            cid,resolution=self.cache[key];self.cache.move_to_end(key);return cid,resolution,observation
        chosen=None;resolution='source_occurrence'
        for k,v in normalized_ids:
            raw_value=next((raw for observed_kind,raw,_ in observation if observed_kind==k and canonical_identifier(k,raw)==v),'')
            parts=identifier_parts(k,raw_value)
            if not parts:continue
            kinds=FISCAL_KINDS if identifier_family(k)=='fiscal' else RC_KINDS if identifier_family(k)=='rc' else (k,)
            rows=db.execute('''SELECT DISTINCT e.id,e.label,e.normalized_name FROM jort_identifier_observations i JOIN jort_entities e ON e.id=i.entity_id
                WHERE i.kind=ANY(%s) AND i.normalized_value=%s AND i.active AND i.attribution='explicite' AND e.status='active' AND e.kind=%s LIMIT 3''',(list(kinds),v,kind)).fetchall()
            # CIN + exactly normalized name is sufficient under the project's private rule.
            # Incompatible names never merge solely by a nearby extracted number.
            compatible=[r for r in rows if k!='cin' or person_name(r['label'])==person_name(label)]
            if identifier_family(k)=='fiscal' and parts[1]:
                filtered=[]
                for candidate in compatible:
                    letters={p[1] for row in db.execute("SELECT kind,raw_value FROM jort_identifier_observations WHERE entity_id=%s AND active AND attribution='explicite'",(candidate['id'],))
                             if (p:=identifier_parts(row['kind'],row['raw_value'])) and identifier_family(row['kind'])=='fiscal' and p[1]}
                    if not letters or letters=={parts[1]}:filtered.append(candidate)
                compatible=filtered
            if len(compatible)==1:chosen=compatible[0]['id'];resolution='cin_and_exact_name' if k=='cin' else 'official_identifier';break
        if not chosen and kind=='personne':
            peers=db.execute("SELECT id FROM jort_entities WHERE normalized_name=%s AND kind='personne' AND status='active' LIMIT 10",(normalized,)).fetchall()
            ids=[r['id'] for r in peers]
            trusted_cins=[]
            if ids:trusted_cins=db.execute("SELECT DISTINCT entity_id,normalized_value FROM jort_identifier_observations WHERE entity_id=ANY(%s) AND kind='cin' AND active AND attribution='explicite'",(ids,)).fetchall()
            incoming=next((v for k,v in normalized_ids if k=='cin'),None)
            if incoming:ids=[cid for cid in ids if not any(r['entity_id']==cid and r['normalized_value']!=incoming for r in trusted_cins)]
            if len(ids)==1:chosen=ids[0];resolution='same_name_presumed'
        if original:
            row=db.execute('SELECT id,redirect_to,kind,identity_status,label_locked FROM jort_entities WHERE id=%s',(original,)).fetchone()
            if row:
                original=row['redirect_to'] or original
                if row['label_locked']:chosen=original;resolution=old[2]
                elif chosen and chosen!=original and kind!='fonds_commerce':
                    if not db.execute('SELECT 1 FROM jort_identity_exclusions WHERE key=%s',('entity:'+original,)).fetchone():
                        incompatible=db.execute('''SELECT 1 FROM jort_identifier_observations a JOIN jort_identifier_observations b ON a.kind=b.kind
                            WHERE a.entity_id=%s AND b.entity_id=%s AND a.active AND b.active AND a.attribution='explicite' AND b.attribution='explicite' AND a.normalized_value<>b.normalized_value LIMIT 1''',(original,chosen)).fetchone()
                        if not incompatible:
                            try:
                                merge_entities(db,[chosen,original],'Identifiant documenté commun',method=resolution)
                                self.clear()
                            except ValueError:
                                chosen=original;resolution='identity_conflict'
                        else:chosen=original;resolution='identity_conflict'
                    else:chosen=original;resolution=old[2]
                else:chosen=original;resolution=old[2] if old else resolution
        if not chosen:
            chosen='ent-'+uuid.uuid4().hex
            value=db.execute("SELECT nextval('jort_public_id_seq') AS n").fetchone()['n']
            db.execute('INSERT INTO jort_entities(id,public_id,kind,nature,label,normalized_name,identity_status,displayable) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                (chosen,f'JORT-E-{value:08d}',kind,nature(kind),label,normalized,resolution,not libelle_documentaire(label,kind)))
        # Réextraire un ancien journal ne doit pas rétablir un ancien nom comme
        # libellé principal. Les anciennes graphies restent dans jort_names.
        db.execute('''UPDATE jort_entities entity SET label=%s,normalized_name=%s,identity_status=%s,displayable=%s
            WHERE entity.id=%s AND NOT entity.label_locked AND NOT EXISTS (
                SELECT 1 FROM jort_mentions mention JOIN jort_announcements published ON published.id=mention.announcement_id
                WHERE mention.entity_id=entity.id AND mention.active AND published.publication_date>
                    (SELECT incoming.publication_date FROM jort_announcements incoming WHERE incoming.id=%s))''',
            (label,normalized,resolution,not libelle_documentaire(label,kind),chosen,e['announcement_id']))
        if kind=='personne':db.execute('INSERT INTO jort_persons VALUES(%s) ON CONFLICT DO NOTHING',(chosen,))
        elif kind!='fonds_commerce':db.execute('INSERT INTO jort_organizations VALUES(%s,%s) ON CONFLICT DO NOTHING',(chosen,kind))
        self.cache[key]=(chosen,resolution)
        if len(self.cache)>20000:self.cache.popitem(last=False)
        return chosen,resolution,observation


def merge_entities(db,ids,reason,method='manual',label=None):
    ids=list(dict.fromkeys(ids))
    if len(ids)<2:return
    rows=db.execute('SELECT * FROM jort_entities WHERE id=ANY(%s) ORDER BY id FOR UPDATE',(ids,)).fetchall()
    if len(rows)!=len(ids) or any(r['status']!='active' for r in rows) or len({r['nature'] for r in rows})!=1:raise ValueError('Entités absentes, retirées ou natures incompatibles.')
    observations=db.execute("SELECT entity_id,kind,raw_value,normalized_value,attribution,active FROM jort_identifier_observations WHERE entity_id=ANY(%s) AND active AND attribution='explicite'",(ids,)).fetchall()
    conflict=conflict_reason(rows,observations)
    if conflict and not (method=='manual' and conflict.startswith('Même CIN mais noms différents')):raise ValueError(conflict)
    target=ids[0];eid='fusion-'+uuid.uuid4().hex
    snapshot={'entities':rows,'mentions':[dict(r) for r in db.execute('SELECT id,entity_id,resolution FROM jort_mentions WHERE entity_id=ANY(%s)',(ids,))],
              'identifiers':observations,'aliases':[dict(r) for r in db.execute('SELECT * FROM jort_names WHERE entity_id=ANY(%s)',(ids,))]}
    # JSON-safe timestamps; proof rows are never deleted.
    snapshot=json.loads(json.dumps(snapshot,default=str))
    db.execute('INSERT INTO jort_identity_decisions(id,target_id,decision,method,reason,snapshot) VALUES(%s,%s,\'merge\',%s,%s,%s)',(eid,target,method,reason,j(snapshot)))
    has_history=bool(db.execute("SELECT to_regclass('public.jort_entity_name_history') AS name").fetchone()['name'])
    for old in ids[1:]:
        db.execute('UPDATE jort_mentions SET entity_id=%s,resolution=%s WHERE entity_id=%s',(target,'human_confirmed' if method=='manual' else method,old))
        # Les variantes publiées suivent la fiche retenue ; les annonces et
        # leurs preuves restent inchangées.
        db.execute('DELETE FROM jort_names n USING jort_names keep WHERE n.entity_id=%s AND keep.entity_id=%s AND n.normalized_name=keep.normalized_name AND n.mention_id=keep.mention_id',(old,target))
        db.execute('UPDATE jort_names SET entity_id=%s WHERE entity_id=%s',(target,old))
        if has_history:
            db.execute('''DELETE FROM jort_entity_name_history h USING jort_entity_name_history keep
                WHERE h.entity_id=%s AND keep.entity_id=%s AND h.normalized_name=keep.normalized_name
                AND h.name_status=keep.name_status AND h.announcement_id IS NOT DISTINCT FROM keep.announcement_id''',(old,target))
            db.execute('UPDATE jort_entity_name_history SET entity_id=%s WHERE entity_id=%s',(target,old))
        db.execute('UPDATE jort_identifier_observations SET entity_id=%s WHERE entity_id=%s',(target,old))
        db.execute('UPDATE jort_participants SET entity_id=%s WHERE entity_id=%s',(target,old))
        db.execute('UPDATE jort_edges SET a=%s WHERE a=%s',(target,old));db.execute('UPDATE jort_edges SET b=%s WHERE b=%s',(target,old))
        db.execute("UPDATE jort_entities SET status='retired',redirect_to=%s WHERE id=%s",(target,old))
        db.execute('UPDATE jort_entities SET redirect_to=%s WHERE redirect_to=%s',(target,old))
    if label:db.execute('UPDATE jort_entities SET label=%s,normalized_name=%s,label_locked=(label_locked OR %s),identity_status=%s WHERE id=%s',
                        (label,normalize_name(label),method=='manual',method,target))
    return dict(event_id=eid,canonical_id=target,evidence_preserved=True)


def source_signature(source,notices):
    from functools import lru_cache
    stable_rules=rules_signature()
    stable={k:source.get(k) for k in ('id','number','date','sha256')}
    return hashlib.sha256(json.dumps({'rules':stable_rules,'source':stable,'announcements':[{k:a.get(k) for k in ('id','reference','text','pages','title','indexation_state')} for a in notices]},sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


from functools import lru_cache
@lru_cache(maxsize=1)
def rules_signature():
    folder=Path(__file__).resolve().parent
    return hashlib.sha256(b''.join((folder/name).read_bytes() for name in ('extraire_entites.py','extraction_structuree.py','qualite_entites.py'))).hexdigest()


def ingest_document(root,source,notices,resolver,signature=None):
    """Une seule transaction : aucune publication partielle d'un PDF."""
    year=int(str(source['date'])[:4]);signature=signature or source_signature(source,notices)
    copies=[dict(a) for a in notices]
    extracted=enrichir(copies)
    by_notice={a['id']:[] for a in copies}
    for e in extracted['entities']:by_notice[e['announcement_id']].append(e)
    from restitution_index import kind as relation_kind
    with connect(root) as db:
        checkpoint=db.execute('SELECT * FROM jort_migration_checkpoints WHERE document_id=%s',(source['id'],)).fetchone()
        if checkpoint and checkpoint['source_signature']==signature and checkpoint['extraction_version']==ENTITY_EXTRACTION_VERSION and checkpoint['state']=='done':return {'skipped':True,'announcements':len(notices)}
        db.execute('INSERT INTO jort_documents(id,year,number,publication_date,sha256,language,pages,payload,source_revision) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,source_revision=excluded.source_revision',
            (source['id'],year,source['number'],source.get('date'),source.get('sha256'),'fr',source.get('pages',0),j({k:v for k,v in source.items() if not k.startswith('_')}),signature))
        for record_type,key in [('entity','_legacy_entities'),('relation','_legacy_relations')]:
            db.cursor().executemany('INSERT INTO jort_legacy_records VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',[(record_type,r['id'],j(r)) for r in source.get(key,[])])
        for value in {source.get('pdf'),source.get('canonical_pdf')} - {None,''}:
            db.execute('INSERT INTO jort_document_locations(document_id,location,storage) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(source['id'],value,'drive' if value.startswith('drive/') else 'local'))
        old_ids=[r['id'] for r in db.execute('SELECT id FROM jort_announcements WHERE document_id=%s',(source['id'],))]
        if old_ids:
            db.execute('UPDATE jort_mentions SET active=false WHERE announcement_id=ANY(%s)',(old_ids,));db.execute('UPDATE jort_events SET active=false WHERE announcement_id=ANY(%s)',(old_ids,));db.execute('UPDATE jort_edges SET active=false WHERE announcement_id=ANY(%s)',(old_ids,))
            db.execute('UPDATE jort_identifier_observations SET active=false WHERE mention_id IN (SELECT id FROM jort_mentions WHERE announcement_id=ANY(%s))',(old_ids,))
        original={a['id']:a for a in notices}
        for a in copies:
            fields=[a.get('title',''),a.get('reference',''),a.get('text','')]+[e['name'] for e in by_notice[a['id']]]
            payload={k:v for k,v in a.items() if k!='text'};legacy={k:v for k,v in original[a['id']].items() if k!='text'}
            db.execute('''INSERT INTO jort_announcements(id,document_id,reference,publication_date,category,publication_type,original_text,title,normalized_title,search_vector,search_visible,extraction_quality,extraction_version,original_sha256,payload,legacy_payload)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,to_tsvector('simple',%s),%s,%s,%s,%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,normalized_title=excluded.normalized_title,category=excluded.category,publication_type=excluded.publication_type,search_vector=excluded.search_vector,search_visible=excluded.search_visible,extraction_quality=excluded.extraction_quality,extraction_version=excluded.extraction_version,payload=excluded.payload
                WHERE jort_announcements.original_sha256=excluded.original_sha256''',
                (a['id'],source['id'],a.get('reference'),source.get('date'),a.get('category','a_classer'),a['publication_type'],a['text'],a.get('title','Annonce à préciser'),normalize_name(a.get('title','')),normalize_name(' '.join(fields)),a['search_visible'],a['extraction_quality'],ENTITY_EXTRACTION_VERSION,hashlib.sha256(a['text'].encode()).hexdigest(),j(payload),j(legacy)))
            actual=db.execute('SELECT original_sha256 FROM jort_announcements WHERE id=%s',(a['id'],)).fetchone()
            if actual['original_sha256']!=hashlib.sha256(a['text'].encode()).hexdigest():raise ValueError('Texte source changé pour un ID existant ; conserver une version distincte.')
        mappings={}
        for e in extracted['entities']:
            cid,resolution,observations=resolver.resolve(db,e);mappings[e['id']]=cid
            payload=dict(e,canonical_id=cid,identity_resolution=resolution)
            db.execute('INSERT INTO jort_mentions VALUES(%s,%s,%s,%s,%s,%s,%s,true,%s) ON CONFLICT(id) DO UPDATE SET entity_id=excluded.entity_id,payload=excluded.payload,resolution=excluded.resolution,active=true,extraction_version=excluded.extraction_version',
                (e['id'],cid,e['announcement_id'],e.get('name_as_published') or e['name'],e['type'],resolution,j(payload),ENTITY_EXTRACTION_VERSION))
            db.execute('INSERT INTO jort_names(entity_id,normalized_name,label,mention_id) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',(cid,normalize_name(e['name']),e['name'],e['id']))
            for k,v,q in observations:
                oid=hashlib.sha256((e['id']+'|'+k+'|'+v).encode()).hexdigest()
                db.execute('INSERT INTO jort_identifier_observations VALUES(%s,%s,%s,%s,%s,%s,\'TN\',%s,%s,true) ON CONFLICT(id) DO UPDATE SET entity_id=excluded.entity_id,active=true',
                    (oid,cid,e['id'],k,v,canonical_identifier(k,v),'explicite' if q else 'a_verifier',q))
            evidence=e.get('evidence')
            if evidence:
                a=next(a for a in copies if a['id']==e['announcement_id'])
                db.execute('INSERT INTO jort_proofs VALUES(%s,%s,%s,NULL,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',(e['id']+':preuve',e['announcement_id'],e['id'],j(a.get('pages',[])),evidence.get('start'),evidence.get('end'),evidence['quote']))
            if e['type']=='fonds_commerce':
                description=e.get('title_quote') or e['name'];parts=re_split_asset(description)
                db.execute('INSERT INTO jort_assets(entity_id,activity,address,trade_name,description,description_quote) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(entity_id) DO UPDATE SET activity=excluded.activity,address=excluded.address,description=excluded.description',
                    (cid,parts['activity'],parts['address'],parts['trade_name'],description,e.get('title_quote')))
        for r in extracted['relations']:
            if r.get('status')=='a_corriger' or not r.get('quote'):continue
            event_id=r['announcement_id']+':cession' if r.get('type')=='vente_fonds_commerce' else r['id']+':evenement'
            assertion=r.get('assertion_status','publie')
            db.execute('INSERT INTO jort_events VALUES(%s,%s,%s,%s,%s,%s,%s,%s,true) ON CONFLICT(id) DO UPDATE SET active=true,payload=excluded.payload',
                (event_id,r['announcement_id'],r.get('event_type','role_publie'),r.get('event_date'),'day' if r.get('event_date') else 'unknown',assertion,r['quote'],j(r)))
            db.execute('INSERT INTO jort_edges VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET a=excluded.a,b=excluded.b,payload=excluded.payload,active=excluded.active',
                (r['id'],event_id,mappings[r['from']],mappings[r['to']],relation_kind(r),r['announcement_id'],j(r),assertion=='publie'))
            for side in ('from','to'):
                e=next(e for e in extracted['entities'] if e['id']==r[side])
                if e['type']=='fonds_commerce':db.execute('INSERT INTO jort_event_objects VALUES(%s,%s) ON CONFLICT DO NOTHING',(event_id,mappings[e['id']]))
                else:db.execute('INSERT INTO jort_participants VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',(event_id,mappings[e['id']],e['id'],r['role'] if r.get('type')=='vente_fonds_commerce' else r['role'] if side=='from' else 'organisation_concernee'))
            a=next(a for a in copies if a['id']==r['announcement_id'])
            db.execute('INSERT INTO jort_proofs VALUES(%s,%s,NULL,%s,%s,NULL,NULL,%s) ON CONFLICT DO NOTHING',(r['id']+':preuve',r['announcement_id'],event_id,j(a.get('pages',[])),r['quote']))
        db.execute('INSERT INTO jort_migration_checkpoints VALUES(%s,%s,%s,\'done\',now()) ON CONFLICT(document_id) DO UPDATE SET source_signature=excluded.source_signature,extraction_version=excluded.extraction_version,state=\'done\',updated_at=now()',
            (source['id'],signature,ENTITY_EXTRACTION_VERSION))
    return {'skipped':False,'announcements':len(copies),'mentions':len(extracted['entities']),'relations':len(extracted['relations'])}


def re_split_asset(description):
    import re
    value=description
    found=re.search(r'\b(?:sis|situ[eé]|exploit[eé])\b| — ',value,re.I)
    activity=value[:found.start()].strip() if found else value.strip()
    address=value[found.end():].strip() if found else None
    trade=re.search(r'(?:enseigne|d[eé]nomm[eé])\s*[«"]([^»"]+)',value,re.I)
    return dict(activity=activity,address=address,trade_name=trade.group(1) if trade else None)
