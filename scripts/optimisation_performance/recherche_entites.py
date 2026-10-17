"""Remplacement préparé de Store.nodes. Installation explicite seulement."""
from extraction_structuree import normalize_name
from restitution_index import match


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
