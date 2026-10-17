import sqlite3
import unittest

from scripts.schema_evolution import appliquer_schema


class SchemaEvolutionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE sources(id TEXT PRIMARY KEY);
            CREATE TABLE announcements(id TEXT PRIMARY KEY, source_id TEXT REFERENCES sources(id));
            CREATE TABLE entities(id TEXT PRIMARY KEY, announcement_id TEXT REFERENCES announcements(id));
            CREATE TABLE relations(id TEXT PRIMARY KEY);
        ''')

    def tearDown(self):
        self.db.close()

    def test_migration_is_idempotent_and_private(self):
        self.assertEqual(appliquer_schema(self.db), 1)
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(appliquer_schema(self.db), 1)
        self.assertEqual(self.db.execute('SELECT visibility FROM publication_state').fetchone()[0], 'private')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM app_users').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM app_roles').fetchone()[0], 3)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT public_allowed FROM field_access_policies WHERE field_name='cin'").fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM public_records').fetchone()[0], 0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO public_records(id,record_type,source_record_id,source_revision,status) VALUES('p','personne','e','revision','approved')")

    def test_candidate_requires_two_distinct_source_occurrences(self):
        appliquer_schema(self.db)
        self.db.execute("INSERT INTO identity_records(id,entity_type) VALUES('i1','personne')")
        self.db.execute("INSERT INTO identity_occurrences(id,identity_id) VALUES('o1','i1')")
        self.db.execute("INSERT INTO identity_occurrences(id,identity_id) VALUES('o2','i1')")
        self.db.execute("INSERT INTO identity_candidates(id,occurrence_a,occurrence_b,method,strategy_version) VALUES('c1','o1','o2','exact_cin','1')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO identity_candidates(id,occurrence_a,occurrence_b,method,strategy_version) VALUES('c2','o1','o1','exact_cin','1')")

    def test_source_replacement_does_not_erase_identity_audit(self):
        appliquer_schema(self.db)
        self.db.execute("INSERT INTO sources(id) VALUES('s1')")
        self.db.execute("INSERT INTO announcements(id,source_id) VALUES('a1','s1')")
        self.db.execute("INSERT INTO entities(id,announcement_id) VALUES('e1','a1')")
        self.db.execute("INSERT INTO identity_records(id,entity_type) VALUES('i1','personne')")
        self.db.execute('''INSERT INTO identity_occurrences
            (id,identity_id,entity_id,announcement_id,source_id,source_reference,source_quote)
            VALUES('o1','i1','e1','a1','s1','JAL 1/2024','preuve conservée')''')
        self.db.execute("INSERT INTO identity_occurrences(id,identity_id) VALUES('o2','i1')")
        self.db.execute("INSERT INTO identity_candidates(id,occurrence_a,occurrence_b,method,strategy_version) VALUES('c1','o1','o2','exact_cin','1')")
        self.db.execute("INSERT INTO identity_decisions(id,candidate_id,decision,rationale) VALUES('d1','c1','reject','identité non établie')")
        self.db.execute("DELETE FROM entities WHERE id='e1'")
        self.db.execute("DELETE FROM announcements WHERE id='a1'")
        self.db.execute("DELETE FROM sources WHERE id='s1'")
        self.assertEqual(self.db.execute("SELECT source_quote FROM identity_occurrences WHERE id='o1'").fetchone()[0], 'preuve conservée')
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM identity_candidates WHERE id='c1'").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM identity_decisions WHERE id='d1'").fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
