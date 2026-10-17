import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from extraire_entites import enrichir


class StructureTests(unittest.TestCase):
    def test_one_tech_nomination_and_representation(self):
        a=dict(id='one',reference='2017401317SANB2',text='''Société ONE TECH BUSINESS
SOLUTIONS SA
Au capital de 5.676.000 dt
MF : 030540/R/A/M/000
Suivant procès-verbal du conseil d'administration en date du 13/1/2017,
enregistré le 26/1/2017, il a été décidé :
La nomination de Mr Khaled Abdeljaoued en qualité de Directeur Général pour trois ans.
Le renouvellement des fonctions de Mr Hedi Karim Sellami représentant
de la société One Tech Holding en qualité d'administrateur délégué.''')
        raw=a['text'];r=enrichir([a]);names={e['id']:e['name'] for e in r['entities']}
        links={(names[e['from']],names[e['to']],e['role']) for e in r['relations']}
        self.assertIn(('Khaled Abdeljaoued','Société ONE TECH BUSINESS SOLUTIONS','directeur général'),links)
        self.assertIn(('Hedi Karim Sellami','One Tech Holding','représentant'),links)
        self.assertTrue(any('administrateur délégué'==e['role'] for e in r['relations']))
        self.assertEqual(a['text'],raw)
        self.assertEqual(a['published_dates']['conseil']['value'],'2017-01-13')
        self.assertEqual(a['published_dates']['enregistrement']['value'],'2017-01-26')
        self.assertTrue(all(e['quote'] in ' '.join(raw.split()) for e in r['relations']))
        org=next(e for e in r['entities'] if e.get('is_primary'))
        self.assertEqual(org['identifier'],'030540/R/A/M/000')

    def test_convocation_does_not_assert_a_proposed_appointment(self):
        a=dict(id='call',text="Société : Exemple SA\nConvocation\nLes actionnaires sont invités à l'assemblée. Ordre du jour : nomination de Mr Khaled Abdeljaoued en qualité de Directeur Général.")
        r=enrichir([a]);self.assertEqual(a['publication_type'],'convocation');self.assertFalse(a['search_visible'])
        self.assertTrue(all(e.get('assertion_status')=='propose' for e in r['relations']))

    def test_decision_mentioning_an_old_convocation_stays_searchable(self):
        a=dict(id='decision',text="Société : Exemple SA\nSuivant procès-verbal, il a été décidé la nomination de Mr Khaled Abdeljaoued en qualité de Directeur Général. La convocation avait été publiée auparavant.")
        enrichir([a]);self.assertNotEqual(a['publication_type'],'convocation');self.assertTrue(a['search_visible'])

    def test_associates_fondateurs_are_people_with_documented_links(self):
        a=dict(id='founders',reference='2026R00001SANB2',text='''Société : HORIZONS TUNISIE SA
Constitution de société
Associés fondateurs : M. Hedi Karim Sellami, Mme Selma Elloumi Rekik et Monsieur Moncef Sellami.
Capital : 100 000 dinars.
Siège social : Tunis.''')
        result=enrichir([a])
        names={entity['id']:entity['name'] for entity in result['entities']}
        founders=[relation for relation in result['relations'] if relation['role']=='associé fondateur']
        self.assertEqual({names[relation['from']] for relation in founders},{'Hedi Karim Sellami','Selma Elloumi Rekik','Moncef Sellami'})
        self.assertTrue(all(relation['to']=='founders:organisation' for relation in founders))
        self.assertTrue(all(relation['role_family']=='capital' for relation in founders))
        self.assertTrue(all('Associés fondateurs' in relation['quote'] for relation in founders))

if __name__=='__main__':unittest.main()
