import sys
from pathlib import Path
import unittest
import tempfile
import sqlite3
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from extraire_entites import enrichir
from construire_base_2024 import synchroniser_sqlite


class TitresTests(unittest.TestCase):
    def test_syndic_and_editorial_title_repair(self):
        from extraire_entites import completer_titre_annonce
        a=dict(id='syndic',text="Syndic Diar Hammamet\n(MF/IU : 1609341F)\nCf. au P.V de l’AGO du 2/8/2024")
        entities=[]; completer_titre_annonce(a,entities)
        self.assertEqual(a['title'],'Syndic Diar Hammamet')
        b=dict(id='pharma',text='GESTION DE SOCIETES\nHomologation de résolutions\nSOCIETES ANONYMES\nChangement de siège Social\nTaha Pharma des Produits Pharmaceutiques\nSociété Anonyme au capital de 10000\nMF : 1136890C')
        entities=[dict(id='o',announcement_id='pharma',type='societe',name='GESTION DE',is_primary=True)]
        completer_titre_annonce(b,entities)
        self.assertEqual(b['title'],'Taha Pharma des Produits Pharmaceutiques')

    def test_no_address_inferred_from_tax_field(self):
        from extraire_entites import completer_titre_annonce
        a=dict(id='address',text='Avis au public\nRue Habib Bourguiba 2033 Mégrine\nMF : 1234567A\nUne opération est annoncée.')
        entities=[]; completer_titre_annonce(a,entities)
        self.assertEqual(entities,[])
        self.assertTrue(a['title_review_required'])

    def test_fonds_and_joint_sellers(self):
        text = '''Vente d’un fonds de commerce
Suivant acte SSP du 14/08/2026 enregistré le 19/08/2026 à Fouchana
la Sté CHILAX LAND et Mohamed SELLAMI ont vendu à Monem El Hattabi
le fonds de commerce Café type 1 sis au Lot Elomri avenue de la Liberté
1145 El Mohamedia Ben Arous. Les oppositions doivent être adressées au local.'''
        announcement = dict(id='sample',reference='2026R02548FCCC2',text=text)
        result = enrichir([announcement])
        self.assertEqual(announcement['title'], 'Café — Lot Elomri avenue de la Liberté 1145 El Mohamedia Ben Arous')
        self.assertEqual(announcement['category'],'fonds_commerce')
        self.assertEqual(announcement['text'],text)
        self.assertFalse(announcement['title_review_required'])
        sellers = [r for r in result['relations'] if r['role']=='vendeur']
        names = {e['id']:e['name'] for e in result['entities']}
        self.assertEqual({names[r['from']] for r in sellers},{'CHILAX LAND','Mohamed SELLAMI'})
        self.assertTrue(all(r['announcement_id']=='sample' and r['quote'] for r in result['relations']))

    def test_multiline_company(self):
        a=dict(id='ipg',reference='2011G08265SANB2',text='Augmentation du capital\nSociété Industrielle de Production\ndes Glaces «IPG»\nSociété Anonyme au Capital de\n4.800.000 Dinars\nSiège social : Tunis')
        enrichir([a])
        self.assertIn('des Glaces',a['title'])
        self.assertIn('IPG',a['title'])

    def test_no_fabricated_entity(self):
        a=dict(id='unknown',text='Convocation\nLes associés sont invités à une réunion.')
        result=enrichir([a])
        self.assertTrue(a['title_review_required'])
        self.assertEqual(result['entities'],[])

    def test_wrapped_editorial_heading(self):
        a=dict(id='telnet',reference='2010G08915SRLB2',text='Agrément de cession de parts\nsociales\nEt augmentation de capital\nTelnet Incorporated «TELNET INC»\nSociété à responsabilité limitée\nCapital : 1000')
        enrichir([a])
        self.assertEqual(a['title'],'Telnet Incorporated TELNET INC')

    def test_labeled_name(self):
        a=dict(id='community',text='Avis au public\nDénomination sociale : Société communautaire Arts et Culture\nCatégorie de la Société : Régionale\nCapital Social : 10000')
        enrichir([a])
        self.assertEqual(a['title'],'Société communautaire Arts et Culture')

    def test_sqlite_title_and_source(self):
        a=dict(id='sale',reference='2026R02548FCCC2',source_id='journal',pages=[3],text='Vente de fonds de commerce\nSuivant acte, le fonds de commerce Café sis au centre de Tunis. Les oppositions sont reçues au local.')
        enriched=enrichir([a])
        data=dict(sources=[dict(id='journal',number=1,date='2026-08-28')],announcements=[a],entities=enriched['entities'],relations=enriched['relations'])
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'tmp') as directory:
            path=Path(directory)/'sample.sqlite'
            synchroniser_sqlite(path,data)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT title,source_id FROM announcements').fetchone(),(a['title'],'journal'))
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

if __name__=='__main__':
    unittest.main()
