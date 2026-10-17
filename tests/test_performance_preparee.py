"""Contrats de recherche : nouvelle requête équivalente, base isolée en mémoire."""
import hashlib
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from consultation_postgresql import Store
from optimisation_performance.recherche_entites import nodes
from ameliorer_performance_jort import prepared_content,method


class MemoryDB:
    def __init__(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.executescript('''
            CREATE TABLE jort_entities(id TEXT PRIMARY KEY,normalized_name TEXT,label TEXT,
                kind TEXT,nature TEXT,status TEXT,displayable INTEGER);
            CREATE TABLE jort_names(entity_id TEXT,normalized_name TEXT);
            CREATE TABLE jort_entity_metrics(entity_id TEXT PRIMARY KEY,occurrences INTEGER,relations INTEGER);
        ''')
        data=[('a','cofat tunis','COFAT Tunis','societe','personne_morale','active',1),
              ('b','cofat','COFAT','societe','personne_morale','active',1),
              ('c','ancienne tte','Ancienne TTE','societe','personne_morale','active',1),
              ('d','one tech','One Tech','societe','personne_morale','retired',1),
              ('e','one tech','One Tech masquée','societe','personne_morale','active',0),
              ('f','hedi karim sellami','Hédi Karim Sellami','personne','personne_physique','active',1),
              ('g','one tech','Sans métriques','societe','personne_morale','active',1),
              ('h','one tech club','One Tech Club','association','personne_morale','active',1)]
        self.db.executemany('INSERT INTO jort_entities VALUES(?,?,?,?,?,?,?)',data)
        self.db.executemany('INSERT INTO jort_entity_metrics VALUES(?,?,?)',[(key,7,score) for key,score in [('a',1),('b',8),('c',4),('d',9),('e',9),('f',2),('h',1)]])
        self.db.executemany('INSERT INTO jort_names VALUES(?,?)',[('a','cofat'),('a','cofat'),('c','one'),('c','tech business solutions'),('c','otbs'),('f','monsieur hedi sellami')])

    def execute(self,sql,params=()):return self.db.execute(sql.replace('%s','?'),params)
    def __enter__(self):return self
    def __exit__(self,*args):return False


class PreparedPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.db=MemoryDB();self.store=Store(ROOT)
        self.store.connect=lambda:self.db
        self.store._nodes=lambda db,rows:[dict(id=r['id']) for r in rows]

    def tearDown(self):self.db.db.close()

    def test_result_contract_matches_original(self):
        for query in ('','cofat','ONE TECH','tech one','one one tech','otbs','Hédi Sellami','karim hedi sellami','inexistant'):
            for exact in (False,True):
                for kind in ('','organisation','association','personne','societe'):
                    for page in (0,1,999):
                        with self.subTest(query=query,exact=exact,kind=kind,page=page):
                            args=dict(query=query,exact=exact,entity_type=kind,page=page,limit=2)
                            self.assertEqual(Store.nodes(self.store,**args),nodes(self.store,**args))

    def test_cross_alias_intersection_and_no_duplicates(self):
        result=nodes(self.store,query='one tech')
        self.assertEqual({x['id'] for x in result['items']},{'c','h'})
        self.assertEqual(nodes(self.store,query='cofat')['total'],2)
        self.assertEqual(nodes(self.store,query='cofat',exact=True)['total'],2)

    def test_featured_order_preserved(self):
        self.assertEqual(Store.nodes(self.store,featured=True),nodes(self.store,featured=True))

    def test_prepared_patch_is_idempotent_and_keeps_other_methods(self):
        original=(ROOT/'scripts/consultation_postgresql.py').read_bytes()
        updated=prepared_content(original)
        compile(updated,'proposed.py','exec')
        self.assertEqual(prepared_content(updated),updated)
        oldnode,_=method(original.decode(),'Store');newnode,_=method(updated.decode(),'Store')
        oldlines=original.decode().splitlines();newlines=updated.decode().splitlines()
        self.assertEqual(oldlines[:oldnode.lineno-1],newlines[:newnode.lineno-1])
        self.assertEqual(oldlines[oldnode.end_lineno:],newlines[newnode.end_lineno:])
        self.assertEqual(hashlib.sha256((ROOT/'scripts/consultation_postgresql.py').read_bytes()).digest(),hashlib.sha256(original).digest())

    def test_refuses_a_stale_patch(self):
        original=(ROOT/'scripts/consultation_postgresql.py').read_bytes()
        with self.assertRaises(RuntimeError):prepared_content(original.replace(b'limit=100,entity_type=',b'limit=77,entity_type='))

    def test_read_only_plan_never_connects_to_database(self):
        import ameliorer_performance_jort as installer
        with patch.object(sys,'argv',['ameliorer_performance_jort.py']),patch.object(installer,'connect',side_effect=AssertionError('Accès base interdit')):
            self.assertEqual(installer.main(),0)


if __name__=='__main__':unittest.main()
