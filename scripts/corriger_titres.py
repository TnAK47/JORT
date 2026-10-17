"""Corriger les titres depuis les textes conservés, serveur arrêté.

python scripts/corriger_titres.py --dry-run
python scripts/corriger_titres.py --apply --publish
Sauvegardes compressées, point de reprise par année et rapport conservés.
"""
import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import zipfile

from collecter_2024 import save_json
from extraire_entites import completer_titre_annonce, TITLE_EXTRACTION_VERSION
from corriger_entites_et_liens import _source_digest
from maintenance_base import _maintenance_lock, _sync_preserving_links, _stats, _validated
from agregation_base import reconstruire, revision

ROOT = Path(__file__).resolve().parents[1]


def backup_zip(destination, paths):
    """La copie SQLite cohérente et le JSON permettent un retour arrière."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for path in paths:
            if not path.exists():
                continue
            name = str(path.relative_to(ROOT))
            if path.suffix == '.sqlite':
                with tempfile.TemporaryDirectory(dir=ROOT/'data/tmp', prefix='sauvegarde-titres-') as tmp:
                    target = Path(tmp)/path.name
                    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(target)) as db:
                        source.backup(db)
                    archive.write(target, name)
            else:
                archive.write(path, name)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise OSError('Sauvegarde compressée illisible ; correction annulée.')


def run(apply=False, publish=False, baseline=None):
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',8765)) == 0:
            raise RuntimeError('Arrêter JORT avant cette maintenance autonome.')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    directory = ROOT/'data/maintenance'/f'titres-{run_id}'
    directory.mkdir(parents=True, exist_ok=True)
    (ROOT/'data/tmp').mkdir(parents=True, exist_ok=True)
    report = dict(id=run_id, state='running', apply=apply, years=[], errors=[])
    with _maintenance_lock(ROOT):
        if apply:
            backup_zip(directory/'referentiel.zip', [ROOT/'data/referentiel-entites.sqlite'])
        for path in sorted((ROOT/'data').glob('[0-9][0-9][0-9][0-9]/base.json')):
            data = json.loads(path.read_text(encoding='utf-8'))
            # Rejouer une migration de titres depuis sa sauvegarde, uniquement
            # si les textes et références sources n'ont pas changé entre-temps.
            if baseline:
                snapshot=Path(baseline)/f'{path.parent.name}.zip'
                if snapshot.exists():
                    with zipfile.ZipFile(snapshot) as archive:
                        saved=json.loads(archive.read(path.relative_to(ROOT).as_posix()).decode('utf-8'))
                    if _source_digest(saved['announcements'])!=_source_digest(data['announcements']):
                        raise ValueError('La base a changé depuis la sauvegarde ; restauration des titres refusée.')
                    data=saved
            validation = dict(issues=[], issue_counts={}, severity_counts={})
            valid, _ = _validated(data, validation, int(path.parent.name))
            if not valid:
                raise ValueError(f'{path.parent.name} : références de source incohérentes.')
            evidence = _source_digest(data['announcements'])
            groups = defaultdict(list)
            for entity in data['entities']:
                groups[str(entity['announcement_id'])].append(entity)
            row = dict(year=int(path.parent.name), announcements=len(data['announcements']),
                       missing_before=0, recovered_entities=0, changed=0, uncertain=0, examples=[])
            affected = set()
            for announcement in data['announcements']:
                current = groups[str(announcement['id'])]
                before = json.dumps([announcement,current], sort_keys=True, ensure_ascii=False)
                missing = not any(e.get('type') in ('societe','association','organisation','fonds_commerce') for e in current)
                row['missing_before'] += int(missing)
                primary = completer_titre_annonce(announcement, current)
                row['recovered_entities'] += int(missing and primary is not None)
                row['uncertain'] += int(announcement.get('title_review_required',False))
                if len(row['examples']) < 8 and missing:
                    row['examples'].append(dict(reference=announcement.get('reference'), title=announcement['title'], confidence=announcement['title_confidence']))
                if json.dumps([announcement,current], sort_keys=True, ensure_ascii=False) != before:
                    row['changed'] += 1
                    affected.add(announcement['source_id'])
            if _source_digest(data['announcements']) != evidence:
                raise ValueError('Texte ou références sources modifiés : opération refusée.')
            if apply and affected:
                db_path = path.parent/f'jort-{path.parent.name}-fr.sqlite'
                backup_zip(directory/f'{path.parent.name}.zip', [path, db_path])
                data['entities'] = [entity for a in data['announcements'] for entity in groups[str(a['id'])]]
                _stats(data)
                data['revision'] = revision(data)
                # Même en reprise après interruption, seules les sources concernées sont synchronisées.
                numbers = {s['number'] for s in data['sources'] if s['id'] in affected}
                _sync_preserving_links(db_path,data,numbers)
                with closing(sqlite3.connect(db_path)) as db:
                    if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise ValueError('Échec du contrôle SQLite ; sauvegarde disponible.')
                    if db.execute('PRAGMA foreign_key_check').fetchone():
                        raise ValueError('Référence SQLite incohérente ; sauvegarde disponible.')
                save_json(path,data)
                row['saved'] = True
            report['years'].append(row)
            save_json(directory/'rapport.json',report)
            print(json.dumps(row),flush=True)
            del data, groups
        if apply and publish:
            published = reconstruire(ROOT)
            report['publication'] = dict(revision=published['revision'],stats=published['stats'])
        report.update(state='done', title_version=TITLE_EXTRACTION_VERSION)
        save_json(directory/'rapport.json', report)
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run',action='store_true')
    mode.add_argument('--apply',action='store_true')
    parser.add_argument('--publish',action='store_true')
    parser.add_argument('--baseline',type=Path)
    args=parser.parse_args()
    if args.baseline and not args.baseline.resolve().is_relative_to(ROOT/'data/maintenance'):
        parser.error('La sauvegarde doit être conservée dans data/maintenance.')
    run(args.apply,args.publish,args.baseline)
