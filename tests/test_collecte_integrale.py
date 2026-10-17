"""Contrôles isolés : Drive, publication différée et poursuite après PDF illisible."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_reprise_et_statuts import isolated_server, sample, builder
from preparer_collecte import reconcile_paths


class CollecteIntegraleTests(unittest.TestCase):
    def test_drive_access_failure_is_reported_without_missing_redownloads(self):
        with tempfile.TemporaryDirectory() as folder:
            server=isolated_server(Path(folder))
            with patch.object(server,'check_google_drive_access',side_effect=PermissionError('Drive inaccessible')):
                with self.assertRaises(server.BatchPause):
                    server.assert_drive_inventory_access(dict(journals=[]))

    def test_drive_pdf_already_indexed_is_not_queued_or_downloaded(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            server=isolated_server(root)
            drive=root/'drive-fixture'
            pdf=drive/'2024/jal-2024-001-2024-01-02-fr.pdf'
            pdf.parent.mkdir(parents=True)
            pdf.write_bytes(b'pdf fixture')
            data=sample()
            reference='drive/2024/'+pdf.name
            data['sources'][0].update(pdf=reference,canonical_pdf=reference)
            builder.synchroniser_sqlite(root/'data/2024/jort-2024-fr.sqlite',data)
            manifest=dict(journals=[dict(number=1,date='2024-01-02',path=reference,sha256='1',status='telecharge')])
            with patch.object(server,'google_drive_root',return_value=drive), patch.object(server,'resolve_drive_reference',side_effect=lambda ref:drive/ref.removeprefix('drive/')):
                files=server.files_status(m=manifest)
                self.assertTrue(files[0]['indexed'])
                self.assertEqual(files[0]['processing_state'],'termine')
                with patch.object(server,'enqueue_index') as enqueue, patch.object(server,'read_iort_pdf') as download, patch.object(server,'log'):
                    server.download_catalog(None,manifest,[dict(number=1,date='2024-01-02')],0,server.DurableSink())
                    enqueue.assert_not_called()
                    download.assert_not_called()

    def test_indexed_source_is_not_redownloaded_when_manifest_lacks_its_drive_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            server=isolated_server(root)
            data=sample()
            manifest=dict(journals=[dict(number=1,date='2024-01-02',status='inventorie')])
            with patch.object(server,'year_status_snapshot',return_value=data), \
                 patch.object(server,'enqueue_index') as enqueue, \
                 patch.object(server,'read_iort_pdf') as download, \
                 patch.object(server,'log'):
                server.download_catalog(None,manifest,[dict(number=1,date='2024-01-02',url='https://iort.invalid/1')],
                                        0,server.DurableSink())
                enqueue.assert_not_called()
                download.assert_not_called()
            # Une extraction conservée ne prouve pas que l'original est présent.
            self.assertEqual(manifest['journals'][0]['status'],'inventorie')

    def test_publication_lock_does_not_undo_indexing_and_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            server=isolated_server(Path(folder))
            server.job['publication']=dict(state='pending',pending_journals=10)
            with patch.object(server,'publish_boundary',side_effect=PermissionError('fichier occupé')), patch.object(server,'log'):
                self.assertFalse(server.publish_index_boundary(force=True))
            self.assertEqual(server.job['publication']['state'],'pending')
            with patch.object(server,'publish_index_boundary',side_effect=[False,True]) as publish, patch.object(server.shutdown_requested,'wait',return_value=False) as wait:
                server.projection_worker()
                self.assertEqual(publish.call_count,2)
                wait.assert_called_once_with(server.PUBLICATION_INTERVAL_SECONDS)

    def test_sqlite_completed_source_with_old_extractor_is_not_redownloaded(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            server=isolated_server(root)
            data=sample()
            data['sources'][0]['extractor_version']-=1
            builder.synchroniser_sqlite(root/'data/2024/jort-2024-fr.sqlite',data)
            self.assertEqual(server.year_status_snapshot(2024)['sources'][0]['date'],'2024-01-02')
            manifest=dict(journals=[dict(number=1,date='2024-01-02',status='inventorie')])
            with patch.object(server,'read_iort_pdf') as download, patch.object(server,'log'):
                server.download_catalog(object(),manifest,[dict(number=1,date='2024-01-02',url='fixture')],0)
                download.assert_not_called()

    def test_existing_pdf_without_source_is_queued_immediately(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            server=isolated_server(root)
            pdf=root/'received.pdf'; pdf.write_bytes(b'pdf fixture')
            manifest=dict(journals=[dict(number=1,date='2024-01-02',path=pdf.name,status='telecharge')])
            with patch.object(server,'enqueue_index',return_value=True) as enqueue, patch.object(server,'read_iort_pdf') as download, patch.object(server,'log'):
                server.download_catalog(object(),manifest,[dict(number=1,date='2024-01-02')],0,server.DurableSink())
                enqueue.assert_called_once()
                download.assert_not_called()

    def test_download_error_exhausts_retries_and_continues_to_next_journal(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            server=isolated_server(root)
            entries=[dict(number=n,date='2024-01-02',url=f'fixture:{n}') for n in (1,2)]
            manifest=dict(journals=[dict(e,status='inventorie') for e in entries])
            with patch.object(server,'read_iort_pdf',side_effect=[ConnectionResetError('reset')]*3+[(b'pdf fixture','fixture:2')]) as download, \
                 patch.object(server,'verify_pdf',return_value=dict(sha256='2',pages=1)), \
                 patch.object(server,'ticket'),patch.object(server,'resolve_missing'), \
                 patch.object(server.time,'sleep'),patch.object(server,'log'):
                server.download_catalog(object(),manifest,entries,0)
            self.assertEqual(download.call_count,4)
            states={r['number']:r['status'] for r in server.manifest()['journals']}
            self.assertEqual(states,{1:'erreur',2:'telecharge'})

    def test_completed_checkpoint_finishes_transfer_without_reextracting(self):
        with tempfile.TemporaryDirectory() as folder:
            server=isolated_server(Path(folder))
            server.job['tasks']=[dict(key='pdf:2024:1',kind='pdf',year=2024,number=1,state='pending')]
            status=[dict(number=1,indexation_current=True,indexed=True,processing_state='a_traiter')]
            with patch.object(server,'files_status',return_value=status),patch.object(server,'index_one_pdf') as index, \
                 patch.object(server,'transfer_indexed_pdfs',return_value={}) as transfer, \
                 patch.object(server,'publish_index_boundary',return_value=True),patch.object(server,'log'):
                server.coordinator_index()
            index.assert_not_called()
            transfer.assert_called_once()
            self.assertEqual(server.job['tasks'][0]['state'],'completed')

    def test_preparation_reconciles_old_source_path_with_drive_without_reimport(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'project'
            server=isolated_server(root)
            data=sample()
            data['sources'][0]['canonical_pdf']='documents/2024/fr/obsolete.pdf'
            annual=root/'data/2024/base.json'
            server.save_json(annual,data)
            builder.synchroniser_sqlite(root/'data/2024/jort-2024-fr.sqlite',data)
            drive=Path(folder)/'drive-fixture'
            pdf=drive/'2024/jal-2024-001-2024-01-02-fr.pdf'
            pdf.parent.mkdir(parents=True); pdf.write_bytes(b'pdf fixture')
            reference='drive/2024/'+pdf.name
            server.save_json(server.manifest_path(),dict(journals=[dict(number=1,date='2024-01-02',sha256='1',path=reference,status='telecharge')]))
            with patch.object(server,'google_drive_root',return_value=drive),patch.object(server,'resolve_drive_reference',side_effect=lambda ref:drive/ref.removeprefix('drive/')):
                changes=reconcile_paths(server,[2024])
            restored=server.read_json(annual,{})
            self.assertEqual(len(changes),1)
            self.assertEqual(restored['sources'][0]['canonical_pdf'],reference)
            self.assertEqual(restored['announcements'],data['announcements'])

    def test_global_publication_is_grouped_with_final_flush(self):
        with tempfile.TemporaryDirectory() as folder:
            server=isolated_server(Path(folder))
            server.publication_last_attempt=100
            server.job['publication']=dict(state='pending',pending_journals=2)
            with patch.object(server.time,'monotonic',return_value=101), patch.object(server,'publish_boundary') as publish:
                self.assertFalse(server.publish_index_boundary())
                publish.assert_not_called()
                self.assertTrue(server.publish_index_boundary(force=True))
                publish.assert_called_once()

    def test_unreadable_pdf_does_not_stop_next_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            server=isolated_server(Path(folder))
            server.job['tasks']=[dict(key=f'pdf:2024:{n}',kind='pdf',year=2024,number=n,state='pending') for n in (1,2)]
            with patch.object(server,'files_status',return_value=[]), patch.object(server,'index_one_pdf',side_effect=[RuntimeError('PDF illisible'),True]), patch.object(server,'transfer_indexed_pdfs',return_value={}), patch.object(server,'publish_index_boundary',return_value=True), patch.object(server,'log'):
                server.coordinator_index()
            self.assertEqual([t['state'] for t in server.job['tasks']],['error','completed'])
            self.assertFalse(server.lane_cancel['indexation'].is_set())
            with patch.object(server,'_invoke_builder',return_value=1):
                with self.assertRaises(RuntimeError): server.index_one_pdf(3,Path(folder)/'cancel.flag')


if __name__=='__main__':
    unittest.main()
