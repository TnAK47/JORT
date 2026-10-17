"""Vérifications hors réseau, avec racine projet entièrement temporaire."""
import ast
import copy
import io
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout, closing
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import construire_base_2024 as builder
import importer_echantillon as importer
from extraire_entites import enrichir


def sample(number=1):
    sid, aid = f'source-{number}', f'annonce-{number}'
    return dict(sources=[dict(id=sid, number=number, date='2024-01-02', pdf='source.pdf',
        sha256=str(number), pages=1, importer_version=importer.VERSION,
        extractor_version=builder.EXTRACTION_VERSION, extraction_state='terminee')],
        announcements=[dict(id=aid, reference='2024R12345SANB2', source_id=sid,
            text='Société : Exemple SA\nNomination de Monsieur Mohamed Jerbi en qualité d’administrateur.',
            pages=[1], category='societe', indexation_state='terminee')], entities=[], relations=[])


def isolated_server(root):
    """Exécute le module réel, en remplaçant sa racine avant toute instruction."""
    tree = ast.parse((SCRIPTS / 'serveur_local.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ROOT' for t in node.targets):
            node.value = ast.Call(func=ast.Name(id='Path', ctx=ast.Load()),
                args=[ast.Constant(str(root))], keywords=[])
    module = types.ModuleType('serveur_test_temporaire')
    module.__file__ = str(SCRIPTS / 'serveur_local.py')
    exec(compile(ast.fix_missing_locations(tree), module.__file__, 'exec'), module.__dict__)
    return module


class RepriseTests(unittest.TestCase):
    def test_sqlite_incremental_atomic_rollback_and_future_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            dbpath = Path(folder) / 'base.sqlite'
            data = sample()
            builder.synchroniser_sqlite(dbpath, data)
            data['sources'] += sample(2)['sources']
            data['announcements'] += sample(2)['announcements']
            builder.synchroniser_sqlite(dbpath, data, {2})
            with closing(sqlite3.connect(dbpath)) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM announcements').fetchone()[0], 2)
                self.assertEqual(db.execute('SELECT visibility FROM publication_state').fetchone()[0], 'private')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM app_users').fetchone()[0], 0)
            broken = copy.deepcopy(data)
            broken['entities'] = [dict(id='same', announcement_id='annonce-2'), dict(id='same', announcement_id='annonce-2')]
            with self.assertRaises(sqlite3.IntegrityError):
                builder.synchroniser_sqlite(dbpath, broken, {2})
            with closing(sqlite3.connect(dbpath)) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM announcements').fetchone()[0], 2)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_empty_pdf_is_reviewable_not_reimported_and_interrupted_sqlite_is_repaired(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(builder, 'ROOT', Path(folder)):
            root = Path(folder)
            (root / 'data/2024').mkdir(parents=True)
            (root / 'source.pdf').write_bytes(b'pdf test mocked')
            manifest = dict(journals=[dict(number=1, date='2024-01-02', status='telecharge', path='source.pdf', sha256='1')])
            (root / 'data/inventaire-2024-fr.json').write_text(json.dumps(manifest))
            result = dict(source=sample()['sources'][0], announcements=[], warnings=[])
            with patch.object(builder, 'importer', return_value=result) as reader, redirect_stdout(io.StringIO()):
                builder.construire(defer_aggregate=True)
                builder.construire(defer_aggregate=True)
                self.assertEqual(reader.call_count, 1)
            data = json.loads((root / 'data/2024/base.json').read_text(encoding='utf-8'))
            self.assertEqual(data['sources'][0]['extraction_state'], 'vide_a_verifier')
            data['sources'][0]['warnings'].append('Modification durable avant arrêt')
            data['revision'] = builder.revision(data)
            (root / 'data/2024/base.json').write_text(json.dumps(data), encoding='utf-8')
            with closing(sqlite3.connect(root / 'data/2024/jort-2024-fr.sqlite')) as db:
                db.execute('DELETE FROM sources')
                db.commit()
            with patch.object(builder, 'importer') as reader, redirect_stdout(io.StringIO()):
                builder.construire(defer_aggregate=True)
                reader.assert_not_called()
            with closing(sqlite3.connect(root / 'data/2024/jort-2024-fr.sqlite')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM sources').fetchone()[0], 1)

    def test_importer_upgrade_reuses_pdf_but_reextracts_text_once(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(builder, 'ROOT', Path(folder)):
            root = Path(folder)
            (root / 'data/2024').mkdir(parents=True)
            data = sample()
            data['sources'][0]['importer_version'] = importer.VERSION - 1
            data['extractor_version'] = builder.EXTRACTION_VERSION
            data['revision'] = builder.revision(data)
            (root / 'data/2024/base.json').write_text(json.dumps(data))
            (root / 'data/inventaire-2024-fr.json').write_text(json.dumps(dict(journals=[dict(number=1,
                date='2024-01-02', status='telecharge', path='source.pdf', sha256='1')])))
            result = dict(source=sample()['sources'][0], announcements=sample()['announcements'], warnings=[])
            with patch.object(builder, 'importer', return_value=result) as reader, redirect_stdout(io.StringIO()):
                builder.construire(defer_aggregate=True)
                builder.construire(defer_aggregate=True)
                self.assertEqual(reader.call_count, 1)
            data = json.loads((root / 'data/2024/base.json').read_text(encoding='utf-8'))
            self.assertEqual(len(data['announcements']), 1)
            self.assertEqual(data['sources'][0]['importer_version'], importer.VERSION)

    def test_effect_date_is_proven_and_does_not_leak_to_next_person(self):
        text = ('Société : Exemple SA\nNomination de Monsieur Mohamed Jerbi en qualité d’administrateur '
                'à compter du 02/03/2024. Nomination de Monsieur Moncef Abidi en qualité d’administrateur.')
        edges = enrichir([dict(id='a', text=text, reference='2024R12345SANB2')])['relations']
        self.assertEqual(len(edges), 2)
        self.assertEqual(edges[0]['event_date'], '2024-03-02')
        self.assertIn(edges[0]['event_date_quote'], ' '.join(text.split()))
        self.assertIsNone(edges[1]['event_date'])

    def test_logs_status_worker_failure_and_retry_classes(self):
        with tempfile.TemporaryDirectory() as folder:
            server = isolated_server(Path(folder))
            server.log('Tâche unitaire terminée', number=1, task='indexation_pdf')
            handler = object.__new__(server.Handler)
            handler.path = '/api/collecte/status'
            handler.headers = {'Host':'127.0.0.1:8765'}
            responses = []
            handler.respond = lambda value, *args, **kwargs: responses.append(value)
            handler.do_GET()
            self.assertEqual(responses[0]['logs'][0]['number'], 1)
            handler.headers = {'Host':'example.invalid:8765'}
            handler.do_GET()
            self.assertEqual(responses[-1]['error'], 'Hôte local requis')
            self.assertTrue(server.recoverable_index_error(TimeoutError()))
            self.assertTrue(server.recoverable_index_error(sqlite3.OperationalError('database is locked')))
            self.assertFalse(server.recoverable_index_error(PermissionError()))
            self.assertFalse(server.recoverable_index_error(ValueError()))
            dead = types.SimpleNamespace(is_alive=lambda: False, join=lambda **kwargs: None)
            pending = server.queue.Queue(maxsize=1)
            with self.assertRaises(server.BatchPause):
                server.enqueue_index(pending, dead, 5)
            pending.put(6)
            server.finish_index_stream(pending, dead)
            self.assertEqual(pending.unfinished_tasks, 0)

    def test_first_page_notice_and_unknown_reference_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(importer, 'ROOT', Path(folder)):
            root = Path(folder)
            path = root / 'journal.pdf'
            path.write_bytes(b'%PDF-1.7\n test fixture \n%%EOF')
            reader = types.SimpleNamespace(pages=[object()])
            for text, expected_ref in [
                ('Nom : Association Exemple\nConstitution d’une association\n2024T12345APSF1', '2024T12345APSF1'),
                ('Nom : Association Exemple\nConstitution d’une association\nRéférence inconnue', None),
            ]:
                with patch.object(importer, 'PdfReader', return_value=reader), patch.object(importer, 'extract_page', return_value=(text, [])):
                    # Une date différente crée un cache distinct pour chaque cas synthétique.
                    result = importer.importer(path, year=2024, expected_date='2024-01-02' if expected_ref else '2024-01-03', expected_number=1)
                self.assertTrue(result['announcements'])
                self.assertEqual(result['announcements'][0]['reference'], expected_ref)

    def test_post_requires_local_origin_and_stops_accepting_new_work(self):
        with tempfile.TemporaryDirectory() as folder:
            server=isolated_server(Path(folder))
            handler=object.__new__(server.Handler)
            handler.path='/api/collecte/job'
            handler.rfile=io.BytesIO(b'{}')
            results=[]
            handler.respond=lambda value,status=200,**kw: results.append(status)
            handler.headers={'Host':'127.0.0.1:8765','Origin':'https://evil.invalid','X-JORT-Local':'1'}
            handler.do_POST()
            self.assertEqual(results[-1],403)
            handler.headers.update(Origin='http://127.0.0.1:8765')
            server.shutdown_requested.set()
            handler.do_POST()
            self.assertEqual(results[-1],503)

    def test_identity_tickets_span_years_but_extraction_tickets_stay_local(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            server=isolated_server(root)
            for year in (2024,2025):
                data=sample()
                sid,aid,eid=f's{year}',f'a{year}',f'e{year}'
                data['sources'][0].update(id=sid, warnings=['OCR requis pour la page 1'])
                data['announcements'][0].update(id=aid,source_id=sid)
                data['entities']=[dict(id=eid,type='personne',name='Mohamed Jerbi' if year==2024 else 'Moncef Jerbi',cin='12345678',announcement_id=aid)]
                # Une personne acheteuse se trouve à l'extrémité "to".
                data['relations']=[dict(id=f'r{year}',to=eid,announcement_id=aid,quote='Preuve acheteur')]
                (root/f'data/{year}').mkdir(parents=True,exist_ok=True)
                (root/f'data/{year}/base.json').write_text(json.dumps(data))
            server.refresh_tickets()
            values=list(server.tickets().values())
            identity=next(v for v in values if v['kind']=='identite')
            self.assertEqual(len(identity['candidates']),2)
            self.assertTrue(all(c['occurrences'][0]['quote']=='Preuve acheteur' for c in identity['candidates']))
            self.assertEqual([v['id'] for v in values if v['kind']=='extraction'],['ocr:s2024'])

    def test_worker_retries_only_twice_then_preserves_failed_file(self):
        with tempfile.TemporaryDirectory() as folder:
            server=isolated_server(Path(folder))
            server.job=dict(year=2024,state='running')
            pending=server.queue.Queue()
            pending.put(1)
            pending.put(None)
            with patch.object(server,'index_one_pdf',side_effect=TimeoutError('Délai temporaire')) as worker, patch.object(server.time,'sleep'):
                server.index_queue_worker(2024,pending)
                self.assertEqual(worker.call_count,3)
            self.assertEqual(server.job['file_tasks']['2024:1']['state'],'erreur')
            self.assertEqual(pending.unfinished_tasks,0)


if __name__ == '__main__':
    unittest.main()
