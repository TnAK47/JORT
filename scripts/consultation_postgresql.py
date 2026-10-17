"""API de consultation : requêtes indexées et résultats bornés, tri chronologique."""
from datetime import date,datetime
import json
from pathlib import Path

from postgresql_local import connect
from extraction_structuree import normalize_name
from restitution_index import match


def clean_json(value):
    if isinstance(value,dict):return {k:clean_json(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [clean_json(v) for v in value]
    if isinstance(value,(date,datetime)):return value.isoformat()
    return value


class Store:
    def __init__(self,root):self.root=Path(root)
    def connect(self):return connect(self.root,role='reader')

    def metadata(self):
        with self.connect() as db:
            row=db.execute("SELECT value FROM jort_meta WHERE key='bootstrap'").fetchone()
            if not row:raise ValueError('Publication PostgreSQL non préparée.')
            result=row['value']
            result['sources']=[dict(r['payload'],id=r['id'],date=str(r['publication_date']),number=r['number'],pages=r['pages']) for r in db.execute('SELECT id,number,publication_date,pages,payload FROM jort_documents ORDER BY publication_date DESC,id DESC')]
            result['sources']=[{k:s[k] for k in ('id','number','date','pages','pdf','canonical_pdf') if k in s} for s in result['sources']]
            return result

    def search(self,query='',exact=False,page=0,limit=40,category='',source='',entity='',publication='',include_convocations=False):
        query=normalize_name(str(query)[:200]);params=[];where=['true']
        if publication=='convocation':where.append("a.publication_type='convocation'")
        elif not include_convocations:where.append('a.search_visible')
        if publication and publication!='convocation':where.append('a.publication_type=%s');params.append(publication)
        if category and category!='toutes':where.append('a.category=%s');params.append(category)
        if source and source!='tous':where.append('a.document_id=%s');params.append(source)
        if entity:
            where.append('EXISTS(SELECT 1 FROM jort_mentions m WHERE m.announcement_id=a.id AND m.entity_id=%s AND m.active)');params.append(entity)
        if query:
            parser='phraseto_tsquery' if exact else 'plainto_tsquery'
            where.append(f"a.search_vector @@ {parser}('simple',%s)");params.append(query)
        clause=' AND '.join(where)
        with self.connect() as db:
            total=db.execute('SELECT count(*) AS n FROM jort_announcements a WHERE '+clause,params).fetchone()['n']
            page=min(max(0,page),max(0,(total-1)//limit))
            rows=db.execute('SELECT a.id,a.title,a.reference,a.publication_date,a.payload,left(a.original_text,400) AS excerpt FROM jort_announcements a WHERE '+clause+' ORDER BY a.publication_date DESC NULLS LAST,a.id DESC LIMIT %s OFFSET %s',params+[limit,page*limit]).fetchall()
            items=[]
            for row in rows:
                a=dict(row['payload'],id=row['id'],title=row['title'],reference=row['reference'],source_date=str(row['publication_date']),excerpt=' '.join(row['excerpt'].split())[:320])
                a['match']=match([normalize_name(row['title']),normalize_name(row['excerpt'])],query,exact)
                items.append(a)
            return dict(items=items,total=total,page=page,limit=limit,sort='publication_date_desc',publication_type=publication)

    def _nodes(self,db,entity_rows):
        """Grouper les lectures de mentions et d'historique pour toute la page."""
        entity_rows=list(entity_rows)
        if not entity_rows:return []
        ids=[row['id'] for row in entity_rows]
        mentions=db.execute('''SELECT entity_id,id,resolution,name_as_published FROM (
            SELECT entity_id,id,resolution,name_as_published,
            row_number() OVER(PARTITION BY entity_id ORDER BY announcement_id DESC,id) AS rank
            FROM jort_mentions WHERE entity_id=ANY(%s) AND active) ranked WHERE rank<=60
            ORDER BY entity_id,rank''',(ids,)).fetchall()
        by_entity={value:[] for value in ids}
        for row in mentions:by_entity[row['entity_id']].append(row)
        history={value:[] for value in ids}
        if db.execute("SELECT to_regclass('public.jort_entity_name_history') AS name").fetchone()['name']:
            for row in db.execute('''SELECT h.entity_id,h.name,h.name_status,h.valid_from,h.valid_to,h.announcement_id,h.quote,
                to_jsonb(h)->>'observed_on' AS observed_on,a.reference,a.publication_date
                FROM jort_entity_name_history h LEFT JOIN jort_announcements a ON a.id=h.announcement_id WHERE h.entity_id=ANY(%s)
                ORDER BY h.entity_id,a.publication_date DESC NULLS LAST,h.name''',(ids,)):
                history[row['entity_id']].append(row)
        result=[]
        for row in entity_rows:
            rows=by_entity[row['id']]
            result.append(dict(id=row['id'],name=row['label'],type=row['kind'],nature=row['nature'],public_entity_id=row['public_id'],
                occurrenceIds=[r['id'] for r in rows],occurrence_count=int(row.get('occurrence_count') or len(rows)),
                nameVariants=list(dict.fromkeys(r['name_as_published'] for r in rows)),
                resolutionMethods=list(dict.fromkeys(r['resolution'] for r in rows)),
                historicalNames=clean_json([dict(r) for r in history[row['id']]]),
                relation_count=int(row.get('relation_count') or 0),identity_status=row['identity_status']))
        return result

    def _node(self,db,row):return self._nodes(db,[row])[0]

    def nodes(self,query='',exact=False,page=0,limit=100,entity_type='',featured=False):
        query=normalize_name(str(query)[:200])
        where=["e.status='active'",'e.displayable'];params=[];prefix='';candidate_join=''
        if query:
            # UNION dédoublonne nom principal et alias. INTERSECT exige chaque mot
            # sur la même entité, même si les mots figurent dans des alias distincts.
            # Aucun plafond sur les candidats : total et pagination restent exacts.
            words=[query] if exact else list(dict.fromkeys(query.split()))
            terms=[]
            for number,word in enumerate(words):
                operator='=' if exact else 'LIKE'
                value=word if exact else '%'+word+'%'
                terms.append(f'term_{number} AS (SELECT id AS entity_id FROM jort_entities WHERE normalized_name {operator} %s '
                             f'UNION SELECT entity_id FROM jort_names WHERE normalized_name {operator} %s)')
                params.extend((value,value))
            candidates=' INTERSECT '.join(f'SELECT entity_id FROM term_{number}' for number in range(len(words)))
            prefix='WITH '+', '.join(terms)+', candidates AS ('+candidates+') '
            candidate_join=' JOIN candidates candidates ON candidates.entity_id=e.id'
        if entity_type=='organisation':where.append("e.nature IN ('personne_morale','organisation_a_qualifier')")
        elif entity_type:where.append('e.kind=%s');params.append(entity_type)
        clause=' AND '.join(where)
        source=' FROM jort_entities e JOIN jort_entity_metrics metrics ON metrics.entity_id=e.id'+candidate_join+' WHERE '+clause
        with self.connect() as db:
            total=db.execute(prefix+'SELECT count(*) AS n'+source,params).fetchone()['n']
            page=min(max(0,page),max(0,(total-1)//limit))
            order='(e.normalized_name=%s) DESC,coalesce(metrics.relations,0) DESC,e.label,e.id' if query else 'coalesce(metrics.relations,0) DESC,e.label,e.id' if featured else 'e.label,e.id'
            rows=db.execute(prefix+'SELECT e.*,metrics.occurrences AS occurrence_count,metrics.relations AS relation_count'+source+' ORDER BY '+order+' LIMIT %s OFFSET %s',params+([query] if query else [])+[limit,page*limit]).fetchall()
            items=self._nodes(db,rows)
            for n,row in zip(items,rows):n['match']=match([row['normalized_name']],query,exact)
            return dict(items=items,total=total,page=page,limit=limit)

    def announcement(self,identifier):
        with self.connect() as db:
            row=db.execute('''SELECT a.id,a.title,a.original_text,a.payload,d.payload AS source,
                coalesce((SELECT jsonb_agg(m.payload || jsonb_build_object('canonical_id',m.entity_id,'canonical_name',e.label,'public_entity_id',e.public_id))
                    FROM jort_mentions m JOIN jort_entities e ON e.id=m.entity_id
                    WHERE m.announcement_id=a.id AND m.active),'[]'::jsonb) AS entities,
                coalesce((SELECT jsonb_agg(ev.payload || jsonb_build_object('kind',ev.kind,'event_date',ev.event_date,'assertion_status',ev.assertion_status))
                    FROM jort_events ev WHERE ev.announcement_id=a.id AND ev.active),'[]'::jsonb) AS events,
                (SELECT count(*) FROM jort_edges edge WHERE edge.announcement_id=a.id AND edge.active) AS relation_count
                FROM jort_announcements a JOIN jort_documents d ON d.id=a.document_id WHERE a.id=%s''',(identifier,)).fetchone()
            if not row:return None
            a=dict(row['payload'],text=row['original_text'],title=row['title'])
            return clean_json(dict(announcement=a,entities=row['entities'],source=row['source'],relation_count=row['relation_count'],events=row['events']))

    def graph(self,root,depth=2,event='all',limit=200):
        with self.connect() as db:
            node=db.execute('SELECT * FROM jort_entities WHERE id=%s OR public_id=%s',(root,root)).fetchone()
            if not node:
                occurrence=db.execute('SELECT entity_id FROM jort_mentions WHERE id=%s',(root,)).fetchone()
                if occurrence:node=db.execute('SELECT * FROM jort_entities WHERE id=%s',(occurrence['entity_id'],)).fetchone()
            if node and node['redirect_to']:node=db.execute('SELECT * FROM jort_entities WHERE id=%s',(node['redirect_to'],)).fetchone()
            if not node:return dict(root=root,nodes=[],entities=[],relations=[],announcements=[],depths={},capped=False,proofs_capped=False,total_relations=0)
            root=node['id'];queue=[root];depths={root:0};capped=False;frontier=[root]
            for level in range(depth):
                if not frontier:break
                clause='active AND (a=ANY(%s) OR b=ANY(%s))';params=[frontier,frontier]
                if event!='all':clause+=' AND kind=%s';params.append(event)
                following=[]
                for r in db.execute('SELECT a,b FROM jort_edges WHERE '+clause+' ORDER BY announcement_id DESC,id LIMIT 12001',params):
                    for other in (r['a'],r['b']):
                        if other in depths:continue
                        if len(queue)>=limit:capped=True;continue
                        depths[other]=level+1;queue.append(other);following.append(other)
                frontier=list(dict.fromkeys(following))
            clause='active AND a=ANY(%s) AND b=ANY(%s)';params=[queue,queue]
            if event!='all':clause+=' AND kind=%s';params.append(event)
            total=db.execute('SELECT count(*) AS n FROM jort_edges WHERE '+clause,params).fetchone()['n']
            rels=[r['payload'] for r in db.execute('SELECT payload FROM jort_edges WHERE '+clause+' ORDER BY announcement_id DESC,id LIMIT 3000',params)]
            ids=list({str(r[k]) for r in rels for k in ('from','to')})
            entities=[dict(r['payload'],canonical_id=r['entity_id'],canonical_name=r['label'],public_entity_id=r['public_id']) for r in db.execute('SELECT m.payload,m.entity_id,e.label,e.public_id FROM jort_mentions m JOIN jort_entities e ON e.id=m.entity_id WHERE m.id=ANY(%s)',(ids,))] if ids else []
            aid=list({r['announcement_id'] for r in rels})
            announcements=[dict(r['payload'],excerpt=' '.join(r['excerpt'].split())[:320]) for r in db.execute('SELECT payload,left(original_text,400) AS excerpt FROM jort_announcements WHERE id=ANY(%s)',(aid,))] if aid else []
            node_rows=db.execute('SELECT e.*,metrics.occurrences AS occurrence_count,metrics.relations AS relation_count FROM jort_entities e LEFT JOIN jort_entity_metrics metrics ON metrics.entity_id=e.id WHERE e.id=ANY(%s)',(queue,)).fetchall()
            nodes=self._nodes(db,node_rows)
            return dict(root=root,nodes=nodes,entities=entities,relations=rels,announcements=announcements,depths=depths,capped=capped,proofs_capped=total>3000,total_relations=total)

    def identities(self,query='',limit=80):
        nodes=self.nodes(query,limit=limit)['items'];items=[]
        with self.connect() as db:
            for n in nodes:
                evidence=[dict(r['payload'],resolution=r['resolution']) for r in db.execute('SELECT payload,resolution FROM jort_mentions WHERE entity_id=%s AND active LIMIT 30',(n['id'],))]
                items.append(dict(id=n['id'],public_id=n['public_entity_id'],type=n['type'],label=n['name'],occurrences=n['occurrence_count'],names=n['nameVariants'],evidence=evidence))
            merges=[dict(r) for r in db.execute("SELECT id,target_id,created_at,reason,method FROM jort_identity_decisions WHERE decision='merge' AND method='manual' AND undone_at IS NULL ORDER BY created_at DESC LIMIT 30")]
        return clean_json(dict(entities=items,merges=merges,ready=True))
