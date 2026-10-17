"""Vue SQLite de consultation : recherche complète et graphe chargé à la demande.

Cette vue est reconstructible ; les annonces annuelles et le référentiel restent
les sources durables. Chaque requête ouvre sa propre connexion en lecture seule.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
from qualite_entites import libelle_documentaire, nettoyer_libelle

VERSION = 1


def normalize(value):
    value = ''.join(c for c in unicodedata.normalize('NFD', str(value or '')) if not unicodedata.combining(c)).lower()
    return re.sub(r'[^\w]+', ' ', value.replace('_', ' '), flags=re.UNICODE).strip()


def match(fields, query, exact=False):
    words = list(dict.fromkeys(query.split()))
    if not words:
        return dict(score=1, matched=0, total=0, kind='', label='')
    best = dict(score=0, matched=0, total=len(words), kind='', label='')
    for text in fields:
        bounded = ' ' + text + ' '
        phrase = (' ' + query + ' ') in bounded
        if exact and not phrase:
            continue
        count = sum((' ' + word + ' ') in bounded for word in words)
        if len(words) == 1 and not exact and not count and words[0] in text:
            count = 1
        if not count:
            continue
        all_words = count == len(words)
        cursor, ordered = 0, all_words
        if ordered:
            for word in words:
                at = bounded.find(' ' + word + ' ', cursor)
                if at < 0:
                    ordered = False
                    break
                cursor = at + len(word) + 1
        score = count * 100 + (30 if phrase else 20 if ordered else 10 if all_words else 0)
        if score > best['score']:
            kind = ('ordered' if phrase or ordered else 'unordered') if all_words else ('partial-many' if count > 1 else 'partial-one')
            label = 'Expression exacte' if phrase else 'Tous les mots dans l’ordre' if ordered else 'Tous les mots, ordre différent' if all_words else f'{count}/{len(words)} mots retrouvés'
            best = dict(score=score, matched=count, total=len(words), kind=kind, label=label)
    return best


def kind(relation):
    if relation.get('type') == 'vente_fonds_commerce' or relation.get('event_type') == 'vente': return 'vente'
    if relation.get('type') == 'souscription_capital' or relation.get('event_type') == 'augmentation_capital': return 'capital'
    if relation.get('event_type') in ('resignation', 'revocation', 'cessation', 'demission'): return 'cessation'
    if relation.get('event_type') in ('appointment', 'nomination', 'renewal', 'renouvellement'): return 'nomination'
    return 'role'


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def build(root, data):
    root = Path(root)
    target = root/'data/restitution.sqlite'
    if target.exists() and data.get('annual_revisions'):
        with closing(sqlite3.connect(target)) as existing:
            record=existing.execute("SELECT payload FROM meta WHERE key='bootstrap'").fetchone()
            metadata=json.loads(record[0]) if record else {}
        if metadata.get('version')==VERSION and metadata.get('annual_revisions'):
            return update(root,data,metadata)
    temporary = target.with_name('restitution-construction.sqlite')
    temporary.unlink(missing_ok=True)
    entities_by_id = {str(row['id']): row for row in data['entities']}
    by_notice, groups = {}, {}
    for entity in data['entities']:
        by_notice.setdefault(str(entity['announcement_id']), []).append(entity)
        groups.setdefault(entity['canonical_id'], []).append(entity)
    with closing(sqlite3.connect(temporary)) as db:
        db.executescript('''
            PRAGMA journal_mode=DELETE;
            CREATE TABLE meta(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE sources(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE announcements(id TEXT PRIMARY KEY,source_id TEXT,date TEXT,category TEXT,payload TEXT NOT NULL,lite TEXT NOT NULL,fields TEXT NOT NULL);
            CREATE INDEX announcement_date ON announcements(date DESC,id DESC);
            CREATE INDEX announcement_category ON announcements(category,date DESC);
            CREATE INDEX announcement_source ON announcements(source_id,date DESC);
            CREATE VIRTUAL TABLE announcement_fts USING fts5(words,tokenize='unicode61 remove_diacritics 2');
            CREATE TABLE occurrences(id TEXT PRIMARY KEY,canonical_id TEXT,announcement_id TEXT,payload TEXT NOT NULL);
            CREATE INDEX occurrence_notice ON occurrences(announcement_id,canonical_id);
            CREATE INDEX occurrence_canonical ON occurrences(canonical_id,announcement_id);
            CREATE TABLE nodes(id TEXT PRIMARY KEY,name TEXT,payload TEXT NOT NULL,fields TEXT NOT NULL);
            CREATE INDEX node_name ON nodes(name);
            CREATE VIRTUAL TABLE node_fts USING fts5(words,tokenize='unicode61 remove_diacritics 2');
            CREATE TABLE edges(id TEXT PRIMARY KEY,a TEXT,b TEXT,kind TEXT,announcement_id TEXT,payload TEXT NOT NULL);
            CREATE INDEX edge_a ON edges(a,kind);
            CREATE INDEX edge_b ON edges(b,kind);
            CREATE INDEX edge_notice ON edges(announcement_id);
        ''')
        meta = dict(revision=data['revision'],stats=data['stats'],coverage=data['coverage'],identity_summary=data['identity_summary'],version=VERSION,annual_revisions=data.get('annual_revisions',{}))
        db.execute('INSERT INTO meta VALUES(?,?)', ('bootstrap', _json(meta)))
        db.executemany('INSERT INTO sources VALUES(?,?)', [(str(s['id']),_json(s)) for s in data['sources']])
        dates = {str(s['id']):s.get('date','') for s in data['sources']}
        for row in data['announcements']:
            eid = str(row['id'])
            fields = [normalize(row.get('title')),normalize(row.get('reference')),normalize(row.get('text'))]
            for e in by_notice.get(eid,[]):
                fields.extend(normalize(e.get(k)) for k in ('name','cin','identifier','public_entity_id'))
            lite = {k:v for k,v in row.items() if k != 'text'}
            lite['excerpt'] = ' '.join(str(row.get('text') or '').split())[:320]
            cursor = db.execute('INSERT INTO announcements VALUES(?,?,?,?,?,?,?)', (eid,row['source_id'],dates.get(row['source_id'],row.get('source_date','')),row.get('category'),_json(row),_json(lite),_json(fields)))
            db.execute('INSERT INTO announcement_fts(rowid,words) VALUES(?,?)', (cursor.lastrowid,' '.join(fields)))
        occurrence_fields=('id','name','type','cin','announcement_id','canonical_id','canonical_name','public_entity_id','identity_resolution','identifier','identifier_type','identity_match_status')
        db.executemany('INSERT INTO occurrences VALUES(?,?,?,?)', [(str(e['id']),e['canonical_id'],str(e['announcement_id']),_json({k:e[k] for k in occurrence_fields if k in e})) for e in data['entities']])
        for identifier, peers in groups.items():
            first = peers[0]
            node = dict({k:first[k] for k in occurrence_fields if k in first},id=identifier,name=first.get('canonical_name') or first['name'],occurrenceIds=[e['id'] for e in peers[:60]],occurrence_count=len(peers),nameVariants=sorted({e['name'] for e in peers}),resolutionMethods=sorted({e.get('identity_resolution','source_occurrence') for e in peers}))
            fields = list(dict.fromkeys(normalize(e.get(k)) for e in peers for k in ('name','cin','identifier','public_entity_id')))
            cursor = db.execute('INSERT INTO nodes VALUES(?,?,?,?)',(identifier,normalize(node['name']),_json(node),_json(fields)))
            db.execute('INSERT INTO node_fts(rowid,words) VALUES(?,?)',(cursor.lastrowid,' '.join(fields)))
        for r in data['relations']:
            if r.get('status') == 'a_corriger': continue
            a,b = entities_by_id[str(r['from'])]['canonical_id'],entities_by_id[str(r['to'])]['canonical_id']
            db.execute('INSERT INTO edges VALUES(?,?,?,?,?,?)',(str(r['id']),a,b,kind(r),str(r['announcement_id']),_json(r)))
        db.execute('INSERT INTO announcement_fts(announcement_fts) VALUES(\'optimize\')')
        db.execute('INSERT INTO node_fts(node_fts) VALUES(\'optimize\')')
        add_vocab(db)
        add_terms(db)
        qualify(db)
        db.execute('ANALYZE')
        db.commit()
        # L'optimisation FTS libère ses anciens segments : récupérer leurs pages.
        db.execute('VACUUM')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok': raise ValueError('Index de consultation incohérent.')
    for attempt in range(40):
        try:
            os.replace(temporary,target)
            break
        except PermissionError:
            if attempt == 39: raise
            time.sleep(.25)
    return dict(path=str(target),bytes=target.stat().st_size,revision=data['revision'])


def update(root,data,previous):
    """Transaction limitée aux années modifiées ; les lecteurs gardent leur vue.

    Les rattachements globaux sont réconciliés sans réindexer les textes des
    années inchangées. Un arrêt avant commit conserve la publication précédente.
    """
    path=Path(root)/'data/restitution.sqlite'
    changed={y for y,r in data['annual_revisions'].items() if previous['annual_revisions'].get(y)!=r}
    changed.update(set(previous['annual_revisions'])-set(data['annual_revisions']))
    meta=dict(revision=data['revision'],stats=data['stats'],coverage=data['coverage'],identity_summary=data['identity_summary'],version=VERSION,annual_revisions=data['annual_revisions'])
    by_notice,groups={},{}
    by_id={str(e['id']):e for e in data['entities']}
    for e in data['entities']:
        by_notice.setdefault(str(e['announcement_id']),[]).append(e)
        groups.setdefault(e['canonical_id'],[]).append(e)
    dates={str(s['id']):s.get('date','') for s in data['sources']}
    changed_sources={str(s['id']) for s in data['sources'] if str(s.get('date',''))[:4] in changed}
    fields=('id','name','type','cin','announcement_id','canonical_id','canonical_name','public_entity_id','identity_resolution','identifier','identifier_type','identity_match_status')
    with closing(sqlite3.connect(path,timeout=30)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA busy_timeout=30000')
        db.execute('BEGIN IMMEDIATE')
        for y in changed:
            old_ids=[r[0] for r in db.execute("SELECT id FROM sources WHERE substr(json_extract(payload,'$.date'),1,4)=?",(y,))]
            for source in old_ids:
                clause='SELECT id FROM announcements WHERE source_id=?'
                db.execute('DELETE FROM edges WHERE announcement_id IN ('+clause+')',(source,))
                db.execute('DELETE FROM occurrences WHERE announcement_id IN ('+clause+')',(source,))
                db.execute('DELETE FROM announcement_fts WHERE rowid IN (SELECT rowid FROM announcements WHERE source_id=?)',(source,))
                db.execute('DELETE FROM announcements WHERE source_id=?',(source,))
                db.execute('DELETE FROM sources WHERE id=?',(source,))
        db.executemany('INSERT INTO sources VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload WHERE payload<>excluded.payload',[(str(s['id']),_json(s)) for s in data['sources']])
        for a in data['announcements']:
            if str(a['source_id']) not in changed_sources: continue
            identifier=str(a['id'])
            search_fields=[normalize(a.get('title')),normalize(a.get('reference')),normalize(a.get('text'))]
            for e in by_notice.get(identifier,[]):search_fields.extend(normalize(e.get(k)) for k in ('name','cin','identifier','public_entity_id'))
            lite={k:v for k,v in a.items() if k!='text'}; lite['excerpt']=' '.join(str(a.get('text') or '').split())[:320]
            cursor=db.execute('INSERT INTO announcements VALUES(?,?,?,?,?,?,?)',(identifier,a['source_id'],dates.get(a['source_id'],a.get('source_date','')),a.get('category'),_json(a),_json(lite),_json(search_fields)))
            db.execute('INSERT INTO announcement_fts(rowid,words) VALUES(?,?)',(cursor.lastrowid,' '.join(search_fields)))
        db.executemany('''INSERT INTO occurrences VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET canonical_id=excluded.canonical_id,payload=excluded.payload
            WHERE payload<>excluded.payload''',[(str(e['id']),e['canonical_id'],str(e['announcement_id']),_json({k:e[k] for k in fields if k in e})) for e in data['entities']])
        old_nodes={r[0]:(r[1],r[2],r[3]) for r in db.execute('SELECT id,rowid,payload,fields FROM nodes')}
        for identifier,peers in groups.items():
            first=peers[0]
            node=dict({k:first[k] for k in fields if k in first},id=identifier,name=first.get('canonical_name') or first['name'],occurrenceIds=[e['id'] for e in peers[:60]],occurrence_count=len(peers),nameVariants=sorted({e['name'] for e in peers}),resolutionMethods=sorted({e.get('identity_resolution','source_occurrence') for e in peers}))
            search_fields=list(dict.fromkeys(normalize(e.get(k)) for e in peers for k in ('name','cin','identifier','public_entity_id')))
            payload,encoded=_json(node),_json(search_fields)
            old=old_nodes.pop(identifier,None)
            if old and old[1]==payload and old[2]==encoded: continue
            if old:
                db.execute('UPDATE nodes SET name=?,payload=?,fields=? WHERE id=?',(normalize(node['name']),payload,encoded,identifier))
                rowid=old[0]
                if old[2]==encoded: continue
                db.execute('DELETE FROM node_fts WHERE rowid=?',(rowid,))
            else: rowid=db.execute('INSERT INTO nodes VALUES(?,?,?,?)',(identifier,normalize(node['name']),payload,encoded)).lastrowid
            db.execute('INSERT INTO node_fts(rowid,words) VALUES(?,?)',(rowid,' '.join(search_fields)))
        for identifier,(rowid,_,__) in old_nodes.items():
            db.execute('DELETE FROM node_fts WHERE rowid=?',(rowid,)); db.execute('DELETE FROM nodes WHERE id=?',(identifier,))
        old_edges={r[0] for r in db.execute('SELECT id FROM edges')}
        for r in data['relations']:
            identifier=str(r['id'])
            if r.get('status')=='a_corriger': continue
            a,b=by_id[str(r['from'])]['canonical_id'],by_id[str(r['to'])]['canonical_id']
            db.execute('''INSERT INTO edges VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET a=excluded.a,b=excluded.b,kind=excluded.kind,payload=excluded.payload
                WHERE a<>excluded.a OR b<>excluded.b OR kind<>excluded.kind OR payload<>excluded.payload''',(identifier,a,b,kind(r),str(r['announcement_id']),_json(r)))
            old_edges.discard(identifier)
        db.executemany('DELETE FROM edges WHERE id=?',[(i,) for i in old_edges])
        db.execute('UPDATE meta SET payload=? WHERE key=\'bootstrap\'',(_json(meta),))
        if changed: add_terms(db)
        qualify(db)
        db.commit()
        db.execute('PRAGMA wal_checkpoint(PASSIVE)')
    return dict(path=str(path),bytes=path.stat().st_size,revision=data['revision'],incremental=True,updated_years=sorted(changed))


class Store:
    def __new__(cls,root):
        from postgresql_local import enabled
        if enabled(root):
            from consultation_postgresql import Store as PostgreSQLStore
            return PostgreSQLStore(root)
        return super().__new__(cls)

    def __init__(self, root): self.path = Path(root)/'data/restitution.sqlite'

    def connect(self):
        db = sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def metadata(self):
        with closing(self.connect()) as db:
            result = json.loads(db.execute("SELECT payload FROM meta WHERE key='bootstrap'").fetchone()[0])
            result['sources'] = [{key:value for key,value in json.loads(row[0]).items() if key in ('id','number','date','pdf','canonical_pdf','pages')} for row in db.execute('SELECT payload FROM sources')]
            result['stats']['consultable_entities']=db.execute('SELECT count(*) FROM nodes WHERE id NOT IN (SELECT id FROM node_quality WHERE displayable=0)').fetchone()[0]
            result['stats']['relations_extracted']=result['stats'].get('relations',db.execute('SELECT count(*) FROM edges').fetchone()[0])
            result['stats']['relations']=db.execute('SELECT count(*) FROM edges WHERE a NOT IN (SELECT id FROM node_quality WHERE displayable=0) AND b NOT IN (SELECT id FROM node_quality WHERE displayable=0)').fetchone()[0]
            return result

    def _candidates(self, db, table, query, exact, where='', args=()):
        words = list(dict.fromkeys(query.split()))
        # FTS trouve les candidats sur tout le texte. Le vocabulaire traite
        # la sous-chaîne historique « assad » -> « lassad » sans scan du corpus.
        fts = table[:-1]+'_fts' if table == 'announcements' else 'node_fts'
        if words:
            expression = ' OR '.join('"'+word+'"' for word in words)
            clause = f't.rowid IN (SELECT rowid FROM {fts} WHERE {fts} MATCH ?)'
            params = [expression]
            if len(words)==1 and not exact and len(words[0])>=3:
                # Les mots de vocabulaire sont petits ; leur résolution élargit
                # la recherche sans relire les 196 000 textes à chaque frappe.
                table='announcement_terms_search' if fts=='announcement_fts' else 'node_terms_search'
                try:
                    aliases=[r[0] for r in db.execute(f'SELECT term FROM {table} WHERE term LIKE ?',('%'+words[0]+'%',))]
                except sqlite3.OperationalError:
                    aliases=[r[0] for r in db.execute(f'SELECT term FROM {fts}_vocab WHERE instr(term,?)>0',(words[0],))]
                if aliases: params[0] = ' OR '.join('"'+term+'"' for term in aliases)
        else: clause,params='1',[]
        if table=='nodes':clause+=' AND t.id NOT IN (SELECT id FROM node_quality WHERE displayable=0)'
        if where: clause += ' AND '+where
        return clause, params+list(args)

    def search(self, query='', exact=False, page=0, limit=40, category='', source='', entity=''):
        query=normalize(query[:200])
        where,args=[],[]
        if category and category!='toutes': where.append('t.category=?'); args.append(category)
        if source and source!='tous': where.append('t.source_id=?'); args.append(source)
        if entity: where.append('EXISTS (SELECT 1 FROM occurrences o WHERE o.announcement_id=t.id AND o.canonical_id=?)'); args.append(entity)
        with closing(self.connect()) as db:
            clause,params=self._candidates(db,'announcements',query,exact,' AND '.join(where),args)
            rank=lambda fields: match(json.loads(fields),query,exact)['score']
            db.create_function('rank_match',1,rank,deterministic=True)
            if query: clause+=' AND rank_match(t.fields)>0'
            total=db.execute('SELECT count(*) FROM announcements t WHERE '+clause,params).fetchone()[0]
            page=min(max(0,page),max(0,(total-1)//limit))
            order='rank_match(t.fields) DESC,t.date DESC,t.id DESC' if query else 't.date DESC,t.id DESC'
            rows=db.execute('SELECT t.lite,t.fields FROM announcements t WHERE '+clause+' ORDER BY '+order+' LIMIT ? OFFSET ?',params+[limit,page*limit]).fetchall()
            items=[]
            for row in rows:
                item=json.loads(row['lite']); _notice_label(item); item['match']=match(json.loads(row['fields']),query,exact); items.append(item)
            return dict(items=items,total=total,page=page,limit=limit)

    def nodes(self, query='',exact=False,page=0,limit=100,entity_type='',featured=False):
        query=normalize(query[:200])
        with closing(self.connect()) as db:
            restriction,args='',[]
            if entity_type=='organisation': restriction="json_extract(t.payload,'$.type') IN ('societe','association','organisation')"
            elif entity_type in ('personne','societe','association','fonds_commerce'):
                restriction="json_extract(t.payload,'$.type')=?"; args=[entity_type]
            clause,params=self._candidates(db,'nodes',query,exact,restriction,args)
            db.create_function('rank_match',1,lambda fields:match(json.loads(fields),query,exact)['score'],deterministic=True)
            if query: clause+=' AND rank_match(t.fields)>0'
            total=db.execute('SELECT count(*) FROM nodes t WHERE '+clause,params).fetchone()[0]
            page=min(max(0,page),max(0,(total-1)//limit))
            order='rank_match(t.fields) DESC,t.name' if query else 'COALESCE(degree.count,0) DESC,t.name' if featured else 't.name'
            join=' LEFT JOIN (SELECT id,count(*) AS count FROM (SELECT a AS id FROM edges UNION ALL SELECT b AS id FROM edges) GROUP BY id) degree ON degree.id=t.id' if featured and not query else ''
            rows=db.execute('SELECT t.payload,t.fields FROM nodes t'+join+' WHERE '+clause+' ORDER BY '+order+' LIMIT ? OFFSET ?',params+[limit,page*limit])
            items=[]
            for r in rows:
                item=json.loads(r['payload']); item['name']=nettoyer_libelle(item['name'],item['type']); item['match']=match(json.loads(r['fields']),query,exact)
                item['relation_count']=db.execute('SELECT count(*) FROM edges WHERE a=? OR b=?',(item['id'],item['id'])).fetchone()[0]
                items.append(item)
            return dict(items=items,total=total,page=page,limit=limit)

    def announcement(self, identifier):
        with closing(self.connect()) as db:
            row=db.execute('SELECT payload FROM announcements WHERE id=?',(identifier,)).fetchone()
            if not row: return None
            announcement=json.loads(row[0])
            _notice_label(announcement)
            entities=[json.loads(r[0]) for r in db.execute('SELECT payload FROM occurrences WHERE announcement_id=?',(identifier,))]
            source=db.execute('SELECT payload FROM sources WHERE id=?',(announcement['source_id'],)).fetchone()
            count=db.execute('SELECT count(*) FROM edges WHERE announcement_id=?',(identifier,)).fetchone()[0]
            return dict(announcement=announcement,entities=entities,source=json.loads(source[0]) if source else None,relation_count=count)

    def graph(self, root,depth=2,event='all',limit=200):
        with closing(self.connect()) as db:
            # An occurrence ID, canonical ID or permanent public ID may be used.
            if not db.execute('SELECT 1 FROM nodes WHERE id=?',(root,)).fetchone():
                row=db.execute('SELECT canonical_id FROM occurrences WHERE id=?',(root,)).fetchone()
                if row: root=row[0]
            if not db.execute('SELECT 1 FROM nodes WHERE id=? AND id NOT IN (SELECT id FROM node_quality WHERE displayable=0)',(root,)).fetchone(): return dict(root=root,nodes=[],entities=[],relations=[],announcements=[],capped=False,proofs_capped=False)
            depths={root:0}; queue=[root]; capped=False; edges={}; proof_cap=3000
            for current in queue:
                if depths[current]>=depth: continue
                condition='(a=? OR b=?)'; params=[current,current]
                condition+=' AND a NOT IN (SELECT id FROM node_quality WHERE displayable=0) AND b NOT IN (SELECT id FROM node_quality WHERE displayable=0)'
                if event!='all': condition+=' AND kind=?'; params.append(event)
                for row in db.execute('SELECT * FROM edges WHERE '+condition+' ORDER BY announcement_id DESC,id',params):
                    other=row['b'] if row['a']==current else row['a']
                    if other not in depths:
                        if len(depths)>=limit: capped=True; continue
                        depths[other]=depths[current]+1; queue.append(other)
                    if len(edges)<proof_cap: edges[row['id']]=dict(row)
            # Include edges between retained nodes, without expanding the graph.
            placeholders=','.join('?' for _ in queue)
            params=queue+queue
            condition=f'a IN ({placeholders}) AND b IN ({placeholders})'
            if event!='all': condition+=' AND kind=?'; params.append(event)
            count=db.execute('SELECT count(*) FROM edges WHERE '+condition,params).fetchone()[0]
            rows=db.execute('SELECT * FROM edges WHERE '+condition+' ORDER BY announcement_id DESC,id LIMIT ?',params+[proof_cap]).fetchall()
            rels=[json.loads(r['payload']) for r in rows]
            occurrence_ids={str(r[k]) for r in rels for k in ('from','to')}
            entities=[]
            if occurrence_ids:
                ids=list(occurrence_ids)
                entities=[json.loads(r[0]) for r in db.execute('SELECT payload FROM occurrences WHERE id IN ('+','.join('?' for _ in ids)+')',ids)]
            aid=list({r['announcement_id'] for r in rels})
            announcements=[]
            if aid: announcements=[json.loads(r[0]) for r in db.execute('SELECT lite FROM announcements WHERE id IN ('+','.join('?' for _ in aid)+')',aid)]
            nodes=[json.loads(r[0]) for r in db.execute('SELECT payload FROM nodes WHERE id IN ('+placeholders+')',queue)]
            for n in nodes:n['name']=nettoyer_libelle(n['name'],n['type'])
            return dict(root=root,nodes=nodes,entities=entities,relations=rels,announcements=announcements,depths=depths,capped=capped,proofs_capped=count>proof_cap,total_relations=count)


def add_vocab(db):
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS announcement_fts_vocab USING fts5vocab(announcement_fts,'row')")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS node_fts_vocab USING fts5vocab(node_fts,'row')")


def _notice_label(row):
    title=nettoyer_libelle(row.get('title',''),row.get('category','organisation'))
    if libelle_documentaire(title,row.get('category','organisation')):
        row.update(title='Annonce — désignation à préciser',title_review_required=True,title_confidence='incertain')
    elif title:row['title']=title


def add_terms(db):
    """Index des fragments de mots, pour « assad » et « lassad » rapidement."""
    for prefix in ('announcement','node'):
        table=prefix+'_terms'
        search=table+'_search'
        db.execute(f'CREATE TABLE IF NOT EXISTS {table}(term TEXT NOT NULL UNIQUE)')
        db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {search} USING fts5(term,content='{table}',tokenize='trigram',detail='none')")
        rows=db.execute(f'SELECT term FROM {prefix}_fts_vocab WHERE term NOT IN (SELECT term FROM {table})').fetchall()
        for (term,) in rows:
            rowid=db.execute(f'INSERT INTO {table}(term) VALUES(?)',(term,)).lastrowid
            db.execute(f'INSERT INTO {search}(rowid,term) VALUES(?,?)',(rowid,term))


def qualify(db):
    db.execute('CREATE TABLE IF NOT EXISTS node_quality(id TEXT PRIMARY KEY,displayable INTEGER NOT NULL,reason TEXT)')
    db.execute('DELETE FROM node_quality')
    items=[]
    for identifier,payload in db.execute('SELECT id,payload FROM nodes'):
        node=json.loads(payload)
        if libelle_documentaire(node['name'],node['type']):items.append((identifier,0,'Libellé documentaire, preuves conservées'))
    db.executemany('INSERT INTO node_quality VALUES(?,?,?)',items)


if __name__ == '__main__':
    root=Path(__file__).resolve().parents[1]
    data=json.loads((root/'dist/data.json').read_text(encoding='utf-8'))
    print(_json(build(root,data)))
