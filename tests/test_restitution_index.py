from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from restitution_index import build, Store


class ConsultationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'tmp')
        self.root=Path(self.temp.name); (self.root/'data').mkdir()
        announcements=[dict(id='old',source_id='s1',source_date='2012-01-01',text='x '*400+'Hédi Karim Sellami Lassad',title='Helioflex',category='societe'),dict(id='new',source_id='s2',source_date='2026-01-01',text='Hédi Sellami',title='COFAT',category='societe')]
        entities=[dict(id='o1',canonical_id='company',name='Helioflex',canonical_name='Helioflex',type='societe',announcement_id='old'),dict(id='p1',canonical_id='person',name='Hédi Karim Sellami',canonical_name='Hédi Karim Sellami',type='personne',announcement_id='old'),dict(id='p2',canonical_id='person',name='Hédi Karim Sellami',canonical_name='Hédi Karim Sellami',type='personne',announcement_id='new'),dict(id='o2',canonical_id='cofat',name='COFAT',canonical_name='COFAT',type='societe',announcement_id='new')]
        relations=[dict(id='r1',**{'from':'p1','to':'o1'},announcement_id='old',role='administrateur',quote='Hédi Karim Sellami'),dict(id='r2',**{'from':'p2','to':'o2'},announcement_id='new',role='directeur',quote='Hédi Karim Sellami')]
        self.data=dict(revision='test',annual_revisions={'2012':'a','2026':'b'},stats=dict(announcements=2),coverage={},identity_summary={},sources=[dict(id='s1',date='2012-01-01'),dict(id='s2',date='2026-01-01')],announcements=announcements,entities=entities,relations=relations)
        build(self.root,self.data); self.store=Store(self.root)

    def tearDown(self): self.temp.cleanup()

    def test_full_text_and_word_order(self):
        result=self.store.search('karim hedi sellami')
        self.assertEqual(result['total'],2)
        self.assertEqual(result['items'][0]['match']['kind'],'unordered')
        self.assertEqual(result['items'][0]['id'],'new')
        self.assertEqual(self.store.search('lassad',True)['total'],1)
        self.assertEqual(self.store.search('assad',True)['total'],0)
        self.assertEqual(self.store.search('assad')['total'],1)

    def test_pagination_and_latest_first(self):
        self.assertEqual(self.store.search(limit=1)['items'][0]['id'],'new')
        self.assertEqual(self.store.search(page=1,limit=1)['items'][0]['id'],'old')
        self.assertEqual(self.store.search(entity='cofat')['total'],1)
        self.assertIn('Hédi Karim Sellami',self.store.announcement('old')['announcement']['text'])

    def test_canonical_graph_recenter_and_depth(self):
        first=self.store.graph('company',1)
        self.assertEqual({n['id'] for n in first['nodes']},{'company','person'})
        second=self.store.graph('person',1)
        self.assertEqual({n['id'] for n in second['nodes']},{'company','person','cofat'})
        self.assertEqual(len(second['relations']),2)
        self.assertEqual(self.store.graph('company',2,limit=2)['capped'],True)
        self.assertEqual(self.store.graph('person',1,event='vente')['relations'],[])

    def test_rebuild_keeps_ids_and_proofs(self):
        self.data['revision']='next'; build(self.root,self.data)
        self.assertEqual(self.store.metadata()['revision'],'next')
        self.assertEqual(self.store.graph('person')['relations'][0]['quote'],'Hédi Karim Sellami')
        self.assertEqual(self.store.nodes('hedi sellami')['total'],1)

    def test_incremental_publication_updates_one_year_and_keeps_others(self):
        self.data['revision']='updated'
        self.data['annual_revisions']['2026']='c'
        self.data['announcements'][1]['text']='Nouvelle mention particulière'
        report=build(self.root,self.data)
        self.assertTrue(report['incremental'])
        self.assertEqual(report['updated_years'],['2026'])
        self.assertEqual(self.store.search('particuliere')['total'],1)
        self.assertEqual(self.store.search('lassad')['total'],1)
        self.assertEqual(self.store.graph('person')['total_relations'],2)


if __name__=='__main__': unittest.main()
