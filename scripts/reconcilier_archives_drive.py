"""Vérifier les PDF indexés sur Drive et reprendre leurs transferts sans télécharger.

--apply copie les sources locales manquantes sur Drive et corrige leurs chemins.
Une copie locale n'est retirée qu'après comparaison complète des empreintes.
Les fichiers illisibles sont consignés ; le contrôle continue au journal suivant.
"""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3

from chemins_drive import google_drive_jort, check_google_drive_access
from collecter_2024 import save_json
from corriger_titres import backup_zip
from agregation_base import revision
from maintenance_base import _maintenance_lock

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def local(reference):
    value=str(reference or '').replace('\\','/')
    if not value or value.startswith('drive/'): return None
    candidate=(ROOT/value).resolve()
    return candidate if candidate.is_relative_to(ROOT) else None


def run(apply=False,deep=False):
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',8765))==0: raise RuntimeError('Arrêter JORT avant cette réconciliation autonome.')
    report=dict(state='running',started_at=datetime.now(timezone.utc).isoformat(),apply=apply,deep_hashes=deep,years=[],totals={},errors=[])
    output=ROOT/'data/maintenance/rapport-archives-drive.json'
    try: drive=check_google_drive_access()
    except OSError as exc:
        report.update(state='drive_unavailable',error=str(exc)); save_json(output,report); return report
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    with _maintenance_lock(ROOT):
        for path in sorted((ROOT/'data').glob('[0-9][0-9][0-9][0-9]/base.json')):
            year=int(path.parent.name)
            data=json.loads(path.read_text(encoding='utf-8'))
            manifest_path=ROOT/f'data/inventaire-{year}-fr.json'
            manifest=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else dict(journals=[])
            notices={}
            for a in data['announcements']: notices.setdefault(a['source_id'],[]).append(a)
            stats=Counter(); changed=False; backed_up=False
            for s in data['sources']:
                rows=notices.get(s['id'],[])
                if s.get('extraction_state')!='terminee' or any(a.get('indexation_state')!='terminee' or a.get('indexation_error') for a in rows):
                    stats['not_fully_indexed']+=1; continue
                filename=f"jal-{year}-{int(s['number']):03d}-{s['date']}-fr.pdf"
                destination=drive/str(year)/filename
                journal=next((r for r in manifest['journals'] if r.get('number')==s['number']),None)
                candidates={local(s.get('pdf')),local(s.get('canonical_pdf')),local((journal or {}).get('path')),ROOT/f'documents/PDF/{year}/originaux/{filename}'}
                candidates=[p for p in candidates if p and p.is_file()]
                try:
                    expected=s.get('sha256')
                    if not destination.is_file():
                        if not candidates:
                            stats['missing']+=1; report['errors'].append(dict(year=year,number=s['number'],state='missing',message='PDF indexé absent des emplacements connus')); continue
                        source=next((p for p in candidates if not expected or sha(p)==expected),None)
                        if not source: raise ValueError('Aucune copie locale ne correspond à l’empreinte source.')
                        if not apply: stats['pending_transfer']+=1; continue
                        destination.parent.mkdir(parents=True,exist_ok=True)
                        temporary=destination.with_name(filename+'.jort-reconciliation.part')
                        try:
                            shutil.copyfile(source,temporary)
                            if sha(temporary)!=sha(source): raise ValueError('Copie Drive incohérente ; source locale conservée.')
                            os.replace(temporary,destination)
                        finally: temporary.unlink(missing_ok=True)
                        stats['copied']+=1
                    with destination.open('rb') as stream:
                        if stream.read(5)!=b'%PDF-': raise ValueError('Le fichier Drive ne contient pas un PDF.')
                    if destination.stat().st_size<=5: raise ValueError('PDF Drive vide.')
                    target_hash=sha(destination) if (deep or candidates) else None
                    if target_hash and expected and target_hash!=expected: raise ValueError('Empreinte Drive différente du journal indexé.')
                    stats['present_drive']+=1
                    stats['verified_hash' if target_hash else 'verified_presence']+=1
                    ref=f'drive/{year}/{filename}'
                    needs_update=s.get('canonical_pdf')!=ref or s.get('pdf')!=ref or (journal and journal.get('path')!=ref)
                    if apply and needs_update:
                        if not backed_up:
                            backup_zip(ROOT/f'data/maintenance/archives-drive-{stamp}/{year}.zip',[path,path.parent/f'jort-{year}-fr.sqlite',manifest_path]); backed_up=True
                        s['canonical_pdf']=s['pdf']=ref
                        if journal: journal.update(path=ref,status='telecharge',sha256=expected)
                        changed=True; stats['paths_corrected']+=1
                    # Retrait après publication durable des nouvelles références.
                    if apply and candidates:
                        stats['local_copies_verified']+=sum(sha(p)==target_hash for p in candidates)
                except (OSError,ValueError) as exc:
                    stats['unreadable_or_conflict']+=1
                    report['errors'].append(dict(year=year,number=s['number'],state='unavailable_or_conflict',message=str(exc)))
            if apply and changed:
                data['revision']=revision(data); save_json(path,data); save_json(manifest_path,manifest)
                dbpath=path.parent/f'jort-{year}-fr.sqlite'
                if dbpath.exists():
                    with closing(sqlite3.connect(dbpath)) as db:
                        db.executemany('UPDATE sources SET pdf=?,canonical_pdf=? WHERE id=?',[(s.get('pdf'),s.get('canonical_pdf'),s['id']) for s in data['sources']])
                        db.execute("UPDATE _jort_meta SET value=? WHERE key='revision'",(data['revision'],)); db.commit()
            # Ne retirer que les copies de PDF d'un journal indexé et intégralement vérifié.
            if apply:
                error_numbers={e['number'] for e in report['errors'] if e['year']==year}
                for s in data['sources']:
                    if s['number'] in error_numbers or s.get('extraction_state')!='terminee': continue
                    if any(a.get('indexation_state')!='terminee' or a.get('indexation_error') for a in notices.get(s['id'],[])): continue
                    ref=s.get('canonical_pdf','')
                    if not ref.startswith(f'drive/{year}/'): continue
                    destination=drive/str(year)/Path(ref).name
                    if not destination.is_file(): continue
                    # Les doublons de travail sont identifiés par nom ou empreinte.
                    folder=ROOT/f'documents/PDF/{year}'
                    for candidate in folder.rglob('*.pdf') if folder.exists() else []:
                        if candidate.parent.name in ('a-importer','a-verifier'): continue
                        if candidate.name not in (destination.name,Path(str(s.get('original_filename',''))).name) and not (s.get('sha256') or '')[:8] in candidate.stem: continue
                        try:
                            if sha(candidate)==sha(destination):
                                if not candidate.resolve().is_relative_to(folder.resolve()): raise ValueError('Copie hors du dossier autorisé.')
                                stats['bytes_freed']+=candidate.stat().st_size; candidate.unlink(); stats['duplicates_removed']+=1
                        except OSError as exc: report['errors'].append(dict(year=year,number=s['number'],state='cleanup_deferred',message=str(exc)))
            report['years'].append(dict(year=year,**stats))
            totals=Counter()
            for item in report['years']: totals.update({k:v for k,v in item.items() if k!='year'})
            report['totals']=dict(totals); save_json(output,report)
            print(json.dumps(dict(year=year,**stats)),flush=True)
    report.update(state='done_with_warnings' if report['errors'] else 'done',finished_at=datetime.now(timezone.utc).isoformat())
    save_json(output,report); return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true'); parser.add_argument('--deep-hashes',action='store_true')
    args=parser.parse_args(); result=run(args.apply,args.deep_hashes)
    print(json.dumps(dict(state=result['state'],totals=result['totals'],errors=len(result['errors']))))
