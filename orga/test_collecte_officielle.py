import copy
import json
import http.client
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch
from pypdf import PdfWriter
import collecter_journaux_officiels as collector


class CollecteTests(unittest.TestCase):
    def fixture(self, lang='french'):
        return dict(title='Official Gazette of the Republic of Tunisia; Année 159, No. 103',
                    date='2016-12-20', language=[lang],
                    url='https://www.loc.gov/item/ne63001157_20161220_01/',
                    resources=[{'pdf': 'https://tile.loc.gov/example.pdf'}])

    def test_filter_legal_announcements(self):
        item = self.fixture()
        item['title'] = 'Official Gazette of the Republic of Tunisia. Legal, Regulatory, and Judicial Announcements; No. 103'
        self.assertIsNone(collector.entry_from_loc(item))

    def test_languages_and_number(self):
        entry = collector.entry_from_loc(self.fixture())
        self.assertEqual((entry['language'], entry['number']), ('fr', 103))
        item = self.fixture('arabic')
        item['title'] = 'Official Gazette of the Republic of Tunisia; Sanah 159, ʻAdad 105'
        self.assertEqual(collector.entry_from_loc(item)['number'], 105)

    def test_unknown_and_ambiguous_languages(self):
        item = self.fixture('english')
        self.assertIsNone(collector.entry_from_loc(item))
        item['language'] = ['french', 'arabic']
        self.assertIsNone(collector.entry_from_loc(item))

    def test_wrong_pdf_url(self):
        item = self.fixture()
        item['resources'][0]['pdf'] = 'https://jort.tn/secret.pdf'
        with self.assertRaises(ValueError):
            collector.entry_from_loc(item)

    def test_pdf_html_and_broken(self):
        with tempfile.TemporaryDirectory(dir=collector.ROOT / 'tmp') as folder:
            path = Path(folder) / 'test.pdf'
            path.write_bytes(b'<html>login</html>')
            with self.assertRaises(ValueError):
                collector.verify_pdf(path)
            path.write_bytes(b'%PDF-1.7\ntruncated')
            with self.assertRaises(Exception):
                collector.verify_pdf(path)

    def test_resume_and_modified_original(self):
        with tempfile.TemporaryDirectory(dir=collector.ROOT / 'tmp') as folder:
            root = Path(folder)
            entry = collector.entry_from_loc(self.fixture())
            final = root / 'pdfs' / collector.relative_path(entry)
            final.parent.mkdir(parents=True)
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            writer.write(final)
            with closing(sqlite3.connect(root / 'state.sqlite')) as db:
                db.execute('CREATE TABLE downloads (id TEXT PRIMARY KEY, sha256 TEXT, path TEXT, proof TEXT)')
                collector.download(entry, root / 'pdfs', root, db, 0)
                with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
                    self.assertEqual(collector.download(entry, root / 'pdfs', root, db, 0), 'reutilise')
                    final.write_bytes(final.read_bytes() + b'\nchanged')
                    with self.assertRaises(ValueError):
                        collector.download(entry, root / 'pdfs', root, db, 0)

    def test_iort_report_distinguishes_unknown_coverage(self):
        with tempfile.TemporaryDirectory(dir=collector.ROOT / 'tmp') as folder:
            collector.write_iort_list(Path(folder), [collector.entry_from_loc(self.fixture())],
                                      {'errors': []}, 2015, 2016, ['ar', 'fr'])
            text = (Path(folder) / 'a-completer-sur-iort.csv').read_text(encoding='utf-8-sig')
            self.assertIn('parution_dans_cette_langue_a_verifier', text)
            self.assertIn('couverture_inconnue', text)

    def test_inventory_pagination_and_exclusion(self):
        legal = self.fixture()
        legal['title'] = 'Official Gazette of the Republic of Tunisia. Legal, Regulatory, and Judicial Announcements; No. 103'
        page1 = dict(results=[legal], pagination={'next': 'https://www.loc.gov/catalogue/page2'})
        page2 = dict(results=[self.fixture()], pagination={'next': None})
        with tempfile.TemporaryDirectory(dir=collector.ROOT / 'tmp') as folder:
            with patch.object(collector, 'fetch_json', side_effect=[page1, page2]):
                result = collector.inventory(Path(folder))
            self.assertEqual(result['pages'], 2)
            self.assertEqual(result['excluded'], 1)
            self.assertEqual(len(result['entries']), 1)
            self.assertFalse(result['archives_exhaustive'])

    def test_duplicate_writer_lock(self):
        with tempfile.TemporaryDirectory(dir=collector.ROOT / 'tmp') as folder:
            path = Path(folder) / 'collecte.lock'
            with collector.exclusive_lock(path):
                with self.assertRaises(RuntimeError):
                    with collector.exclusive_lock(path):
                        pass
            with collector.exclusive_lock(path):
                pass

    def test_truncated_catalogue_retried_without_caching_partial(self):
        with tempfile.TemporaryDirectory(dir=collector.ROOT / 'tmp') as folder:
            good = io.BytesIO(b'{"results": [], "pagination": {"next": null}}')
            with patch('urllib.request.urlopen', side_effect=[http.client.IncompleteRead(b'partial', 30), good]), patch('time.sleep'):
                result = collector.fetch_json(collector.CATALOGUE, Path(folder))
            self.assertEqual(result['results'], [])
            self.assertEqual(len(list(Path(folder).glob('*.json'))), 1)


if __name__ == '__main__':
    (collector.ROOT / 'tmp').mkdir(exist_ok=True)
    unittest.main()
