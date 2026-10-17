"""Contrôles de consultation et mesures, sans modifier le corpus."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

from restitution_index import Store
from collecter_2024 import save_json
from rapprochement_identites import normaliser_nom_personne

ROOT=Path(__file__).resolve().parents[1]


def run():
    store=Store(ROOT); report=dict(checks=[],timings=[],examples=[],errors=[])
    metadata=store.metadata()
    report.update(revision=metadata['revision'],counts=metadata['stats'],initial_response_bytes=len(json.dumps(metadata,ensure_ascii=False).encode('utf-8')))
    for q in ('','Hedi Sellami','Hedi Karim Sellami','karim hedi sellami','COFAT','assad'):
        start=time.perf_counter(); result=store.search(q)
        report['timings'].append(dict(query=q,seconds=round(time.perf_counter()-start,3),total=result['total'],page_size=len(result['items'])))
        if q in ('Hedi Karim Sellami','karim hedi sellami'): report.setdefault('order_totals',{})[q]=result['total']
        if not q:
            dates=[item.get('source_date','') for item in result['items']]
            report['checks'].append(dict(name='newest_first',ok=dates==sorted(dates,reverse=True)))
    report['checks'].append(dict(name='all_word_orders_same_candidates',ok=len(set(report['order_totals'].values()))==1))
    start=time.perf_counter(); exact=store.search('assad',exact=True)
    report['timings'].append(dict(query='assad (exact)',seconds=round(time.perf_counter()-start,3),total=exact['total']))
    for q in ('Faouzi Elloumi','Selma Elloumi Rekik','Hichem Elloumi','Hédi Karim Sellami','COFAT','HELIOFLEX'):
        nodes=store.nodes(q,exact=False)['items']
        exact_nodes=[n for n in nodes if normaliser_nom_personne(n['name'])==normaliser_nom_personne(q)]
        report['examples'].append(dict(query=q,exact_name_nodes=len(exact_nodes),nodes=[dict(id=n['id'],name=n['name'],occurrences=n['occurrence_count'],methods=n['resolutionMethods']) for n in exact_nodes]))
        if exact_nodes:
            start=time.perf_counter(); graph=store.graph(exact_nodes[0]['id'],depth=2)
            report['timings'].append(dict(graph=q,seconds=round(time.perf_counter()-start,3),nodes=len(graph['nodes']),relations=len(graph['relations']),capped=graph['capped']))
            if q in ('COFAT','Hédi Karim Sellami') and graph['relations']:
                report['checks'].append(dict(name='graph_'+q,ok=all(r.get('announcement_id') and r.get('quote') for r in graph['relations'])))
    with closing(store.connect()) as db:
        report['checks'].append(dict(name='sqlite_quick_check',ok=db.execute('PRAGMA quick_check').fetchone()[0]=='ok'))
        dangling=db.execute('''SELECT count(*) FROM edges e LEFT JOIN nodes a ON a.id=e.a LEFT JOIN nodes b ON b.id=e.b LEFT JOIN announcements n ON n.id=e.announcement_id WHERE a.id IS NULL OR b.id IS NULL OR n.id IS NULL''').fetchone()[0]
        report['checks'].append(dict(name='no_dangling_relations',ok=dangling==0))
    with closing(sqlite3.connect(ROOT/'data/referentiel-entites.sqlite')) as db:
        rows=db.execute('SELECT canonical_id,name,type,cin,identifier,identifier_type,resolution FROM entity_occurrences WHERE active=1')
        groups={}; canonical_identifiers={}
        for canonical,name,kind,cin,identifier,identifier_type,resolution in rows:
            if kind=='fonds_commerce': continue
            family='organisation' if kind in ('societe','association','organisation') else kind
            group=groups.setdefault((family,normaliser_nom_personne(name)),dict(ids=set(),cins=set(),official={},resolutions=set()))
            group['ids'].add(canonical); group['resolutions'].add(resolution)
            if cin and len(cin)==8:group['cins'].add(cin)
            if identifier and identifier_type:group['official'].setdefault(identifier_type,set()).add(identifier)
            values=canonical_identifiers.setdefault(canonical,dict(cins=set(),official={}))
            if cin and len(cin)==8:values['cins'].add(cin)
            if identifier and identifier_type:values['official'].setdefault(identifier_type,set()).add(''.join(c for c in identifier.upper() if c.isalnum()))
        unresolved=[];blocked=[]
        for (family,name),g in groups.items():
            if len(g['ids'])<=1:continue
            cins=set();official={}
            for canonical in g['ids']:
                cins.update(canonical_identifiers[canonical]['cins'])
                for kind,values in canonical_identifiers[canonical]['official'].items():official.setdefault(kind,set()).update(values)
            conflict=len(cins)>1 or any(len(v)>1 for v in official.values())
            deliberate=bool(g['resolutions']&{'human_confirmed','review_after_split','identity_conflict'})
            if not conflict and not deliberate:unresolved.append(dict(type=family,name=name,count=len(g['ids'])))
            elif conflict:blocked.append(dict(type=family,name=name,count=len(g['ids']),canonical_ids=sorted(g['ids']),reason='Identifiants officiels contradictoires dans les occurrences rattachées ; vérifier leur attribution.',identifiers={k:sorted(v) for k,v in official.items() if len(v)>1}))
        report['remaining_unmerged_exact_names']=len(unresolved)
        report['unmerged_examples']=unresolved[:20]
        report['merges_requiring_identifier_review']=blocked
        report['checks'].append(dict(name='automatic_exact_name_merges_complete',ok=not unresolved))
    report['state']='ok' if all(c['ok'] for c in report['checks']) else 'attention'
    save_json(ROOT/'data/maintenance/rapport-restitution.json',report)
    # Le rapport complet peut contenir de nombreux conflits d'identifiants.
    # Ne pas l'imprimer en entier : cela ralentit l'outil et noie le journal.
    compact=dict(
        state=report['state'],
        revision=report.get('revision'),
        checks=report.get('checks', []),
        timings=report.get('timings', []),
        examples=report.get('examples', []),
        remaining_unmerged_exact_names=report.get('remaining_unmerged_exact_names', 0),
        unmerged_examples=report.get('unmerged_examples', []),
        merges_requiring_identifier_review_count=len(report.get('merges_requiring_identifier_review', [])),
        report_path='data/maintenance/rapport-restitution.json',
    )
    print(json.dumps(compact,ensure_ascii=False))
    return report


if __name__=='__main__':run()
