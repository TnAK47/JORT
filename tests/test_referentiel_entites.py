"""Vérifier les identités durables et les preuves après plusieurs extractions."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from test_reprise_et_statuts import SCRIPTS
from agregation_base import identity_group_key
from referentiel_entites import synchroniser


class RegistryTests(unittest.TestCase):
    def test_legacy_split_due_to_identifier_punctuation_is_repaired(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            first=dict(id='e1',type='societe',name='Dalphimetal Tunisie',identifier='745173L',identifier_type='identifiant_fiscal_publie',announcement_id='a1')
            synchroniser(root,[first],identity_group_key)
            target=first['canonical_id']
            with closing(sqlite3.connect(root/'data/referentiel-entites.sqlite')) as db:
                db.execute("INSERT INTO canonical_entities(id,type,label,created_at,public_id) VALUES('legacy','societe','DALPHIMETAL TUNISIE','2020-01-01','JORT-E-99999999')")
                db.execute("INSERT INTO entity_occurrences VALUES('e2','legacy','a2','DALPHIMETAL TUNISIE','societe',NULL,'745173/L','identifiant_fiscal_publie','organization_name_presumed',1)")
                db.commit()
            second=dict(first,id='e2',announcement_id='a2',identifier='745173/L',name='DALPHIMETAL TUNISIE')
            synchroniser(root,[first,second],identity_group_key)
            self.assertEqual(second['canonical_id'],target)
            self.assertEqual(second['identifier'],'745173/L')
            with closing(sqlite3.connect(root/'data/referentiel-entites.sqlite')) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM entity_occurrences WHERE active=1').fetchone()[0],2)

    def test_repeated_sync_reuses_entity_and_keeps_each_announcement(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            people=[dict(id='p1',type='personne',name='Hichem Elloumi',cin='12345678',announcement_id='a1')]
            synchroniser(root,people,identity_group_key)
            canonical=people[0]['canonical_id']
            people.append(dict(id='p2',type='personne',name='HICHEM ELLOUMI',cin='12345678',announcement_id='a2'))
            synchroniser(root,people,identity_group_key)
            synchroniser(root,people,identity_group_key)
            self.assertEqual({p['canonical_id'] for p in people},{canonical})
            self.assertEqual({p['announcement_id'] for p in people},{'a1','a2'})
            with closing(sqlite3.connect(root/'data/referentiel-entites.sqlite')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM entity_occurrences').fetchone()[0],2)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM canonical_entities').fetchone()[0],1)

    def test_conflicting_cins_do_not_share_entity(self):
        with tempfile.TemporaryDirectory() as folder:
            people=[dict(id=f'p{n}',type='personne',name='Hichem Elloumi',cin=cin,announcement_id=f'a{n}')
                    for n,cin in enumerate(('12345678','87654321'))]
            synchroniser(Path(folder),people,identity_group_key)
            self.assertEqual(len({p['canonical_id'] for p in people}),2)
