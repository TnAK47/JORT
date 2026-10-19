"""Contrôler et réparer les données locales sans réimporter les PDF.

Le serveur appelle ce module entre deux extractions, sous son verrou d'écriture.
La commande autonome refuse une réparation pendant que le serveur fonctionne.
"""
import argparse
from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import time
import traceback
import uuid

from collecter_2024 import save_json, atomic_write_bytes
from agregation_base import identity_group_key, revision, reconstruire
from construire_base_2024 import synchroniser_sqlite
from extraire_entites import nom_entite_entete, reparer_parties_vente, reparer_souscriptions_capital, completer_titre_annonce
from rapprochement_identites import cle_prenom_nom, normaliser_nom_personne
from chemins_drive import check_google_drive_access, google_drive_jort, resolve_drive_reference

RULE_VERSION = 'base-repair-5'
TABLES = ('sources', 'announcements', 'entities', 'relations')
ROOT = Path(__file__).resolve().parents[1]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default


def _connection(path):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    return db


def _years(root, selected):
    available = sorted(int(p.parent.name) for p in (root / 'data').glob('[0-9][0-9][0-9][0-9]/base.json'))
    if selected is None:
        return available
    selected = sorted(set(int(y) for y in selected))
    if any(not 1956 <= y <= datetime.now().year for y in selected):
        raise ValueError('Année hors de la période 1956 à aujourd’hui')
    return selected


def _issue(report, code, message, year=None, severity='warning', **refs):
    report['issue_counts'][code] = report['issue_counts'].get(code, 0) + 1
    report['severity_counts'][severity] = report['severity_counts'].get(severity, 0) + 1
    if len(report['issues']) < 2000:
        report['issues'].append(dict(code=code, message=message, year=year, severity=severity, **refs))
    else:
        report['issues_truncated'] = True


def _validated(data, report, selected_year):
    valid = True
    maps = {}
    for table in TABLES:
        rows = data.get(table)
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not row.get('id') for row in rows):
            _issue(report, 'structure', f'Liste {table} invalide.', selected_year, 'error')
            valid = False
            maps[table] = {}
            continue
        maps[table] = {str(row['id']): row for row in rows}
        if len(maps[table]) != len(rows):
            _issue(report, 'duplicate_id', f'Identifiant répété dans {table}.', selected_year, 'error')
            valid = False
    for a in maps['announcements'].values():
        if str(a.get('source_id')) not in maps['sources']:
            _issue(report, 'missing_source', 'Annonce sans journal conservé.', selected_year, 'error', announcement_id=a['id'])
            valid = False
        else:
            maximum = maps['sources'][str(a['source_id'])].get('pages')
            pages = a.get('pages', [])
            if not isinstance(pages, list) or any(not isinstance(p, int) or p < 1 or (isinstance(maximum, int) and p > maximum) for p in pages):
                _issue(report, 'invalid_pages', 'Référence de page invalide.', selected_year, announcement_id=a['id'])
    for entity in maps['entities'].values():
        if str(entity.get('announcement_id')) not in maps['announcements']:
            _issue(report, 'missing_announcement', 'Occurrence sans annonce.', selected_year, 'error', entity_id=entity['id'])
            valid = False
        if entity.get('cin') and entity.get('type') != 'personne':
            _issue(report, 'cin_on_organisation', 'CIN attribuée à une entité qui n’est pas une personne.', selected_year, entity_id=entity['id'])
    for rel in maps['relations'].values():
        aid = str(rel.get('announcement_id'))
        ends = [maps['entities'].get(str(rel.get(k))) for k in ('from', 'to')]
        if aid not in maps['announcements'] or any(e is None or str(e.get('announcement_id')) != aid for e in ends):
            _issue(report, 'broken_relation', 'Relation sans annonce ou extrémités cohérentes.', selected_year, 'error', relation_id=rel['id'])
            valid = False
        if not str(rel.get('quote') or '').strip():
            _issue(report, 'missing_quote', 'Relation sans passage justificatif.', selected_year, relation_id=rel['id'])
    return valid, maps


def _pdf_checks(root, sources, report, selected_year, deep=False, stopped=lambda: False):
    hashes = {}
    drive_sources = [source for source in sources
                     if str(source.get('canonical_pdf') or source.get('pdf') or '').replace('\\', '/').lower().startswith('drive/')]
    drive_available = True
    if drive_sources:
        try:
            check_google_drive_access()
        except (OSError, ValueError) as exc:
            drive_available = False
            _issue(report, 'drive_unavailable',
                   f'Google Drive non accessible via le chemin configuré ; les PDF Drive ne sont pas déclarés manquants : {exc}',
                   selected_year, 'warning', drive_root=os.environ.get('JORT_GOOGLE_DRIVE_ROOT', r'G:\Mon Drive'))
    for source in sources:
        if stopped():
            return
        sha = source.get('sha256')
        if sha and sha in hashes:
            _issue(report, 'duplicate_pdf', 'Deux journaux ont la même empreinte PDF.', selected_year, source_id=source['id'], other_source_id=hashes[sha])
        elif sha:
            hashes[sha] = source['id']
        relative = source.get('canonical_pdf') or source.get('pdf')
        if not relative:
            _issue(report, 'missing_pdf_path', 'Journal sans chemin PDF.', selected_year, source_id=source['id'])
            continue
        normalized=str(relative).replace('\\','/')
        if normalized.lower().startswith('drive/'):
            if not drive_available:
                continue
            try:
                path = resolve_drive_reference(normalized)
                allowed_root = google_drive_jort().resolve()
            except (ValueError, OSError):
                _issue(report, 'external_path', 'Référence Google Drive invalide ou hors du dossier JORT.',
                       selected_year, 'error', source_id=source['id'])
                continue
        else:
            allowed_root=root.resolve()
            path = root / relative
        if not source.get('canonical_pdf') and normalized.startswith('sources/'):
            path = root / 'dist' / relative
            allowed_root=(root/'dist').resolve()
        try:
            path = path.resolve()
            path.relative_to(allowed_root)
        except (ValueError, OSError):
            _issue(report, 'external_path', 'Chemin PDF en dehors du projet.', selected_year, 'error', source_id=source['id'])
            continue
        if not path.is_file():
            _issue(report, 'missing_pdf', 'PDF source introuvable ; annonces conservées.', selected_year, source_id=source['id'], path=str(path))
        elif deep and sha:
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    if stopped():
                        return
                    digest.update(chunk)
            if digest.hexdigest() != sha:
                _issue(report, 'pdf_hash', 'Empreinte du PDF différente de la source indexée.', selected_year, 'error', source_id=source['id'])


def _sqlite_checks(path, data, report, selected_year, full):
    if not path.exists():
        _issue(report, 'sqlite_missing', 'Base SQLite absente ; peut être créée depuis la base annuelle.', selected_year)
        return True, True
    drift = False
    try:
        with closing(_connection(path)) as db:
            for pragma in ('quick_check', 'foreign_key_check') + (('integrity_check',) if full else ()):
                result = db.execute('PRAGMA ' + pragma).fetchmany(10)
                if (pragma != 'foreign_key_check' and (len(result) != 1 or result[0][0] != 'ok')) or (pragma == 'foreign_key_check' and result):
                    _issue(report, 'sqlite_integrity', f'Échec du contrôle {pragma}.', selected_year, 'error')
                    return False, True
            meta = db.execute("SELECT value FROM _jort_meta WHERE key='revision'").fetchone()
            if not meta or meta[0] != (data.get('revision') or revision(data)):
                drift = True
            for table in TABLES:
                expected = {str(row['id']): row for row in data[table]}
                columns = [row[1] for row in db.execute('PRAGMA table_info(' + table + ')')]
                rows = db.execute('SELECT * FROM ' + table)
                seen = set()
                for record in rows:
                    identifier = str(record['id'])
                    seen.add(identifier)
                    original = expected.get(identifier)
                    if original is None:
                        drift = True
                        continue
                    for col in columns:
                        key = {'from_id': 'from', 'to_id': 'to'}.get(col, col)
                        value = record[col]
                        if key == 'pages':
                            try:
                                value = json.loads(value or '[]')
                            except (ValueError, TypeError):
                                drift = True
                        if value != original.get(key):
                            drift = True
                if seen != set(expected):
                    drift = True
            if drift:
                _issue(report, 'sqlite_drift', 'SQLite ne correspond pas exactement à la base annuelle.', selected_year)
    except sqlite3.OperationalError as exc:
        if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
            _issue(report, 'sqlite_busy', 'Base occupée ; contrôle à reprendre.', selected_year)
            return False, False
        _issue(report, 'sqlite_schema', 'Schéma SQLite incomplet ou illisible ; réparation nécessaire.', selected_year, 'error')
        return True, True
    except sqlite3.DatabaseError:
        _issue(report, 'sqlite_corrupt', 'SQLite endommagée ; conserver le fichier pour récupération.', selected_year, 'error')
        return False, True
    return True, drift


def _backup(root, paths, run_id):
    existing = [p for p in paths if p.exists()]
    required = sum(p.stat().st_size for p in existing) * 2 + 32 * 1024 * 1024
    if shutil.disk_usage(root).free < required:
        raise OSError('Espace disque insuffisant pour sauvegarder puis réparer la base.')
    directory = root / 'data' / 'maintenance' / 'sauvegardes' / run_id
    directory.mkdir(parents=True, exist_ok=True)
    for path in existing:
        relative = path.resolve().relative_to(root)
        destination = directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == '.sqlite':
            try:
                with closing(_connection(path)) as source, closing(sqlite3.connect(destination)) as target:
                    source.backup(target)
            except sqlite3.DatabaseError:
                shutil.copy2(path, destination)
        else:
            atomic_write_bytes(destination, path.read_bytes())
    return str(directory.relative_to(root))


def _sync_preserving_links(path, data, numbers=None):
    links = []
    if path.exists():
        with closing(_connection(path)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='identity_occurrences'").fetchone():
                links = [dict(row) for row in db.execute('SELECT id,entity_id,announcement_id,source_id FROM identity_occurrences')]
    synchroniser_sqlite(path, data, changed_numbers=numbers)
    if links:
        valid = {table: {str(row['id']) for row in data[table]} for table in TABLES}
        with closing(sqlite3.connect(path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            for link in links:
                for field, table in (('entity_id', 'entities'), ('announcement_id', 'announcements'), ('source_id', 'sources')):
                    if link[field] is not None and str(link[field]) in valid[table]:
                        db.execute(f'UPDATE identity_occurrences SET {field}=? WHERE id=?', (link[field], link['id']))
            db.commit()


def _stats(data):
    stats = data.setdefault('stats', {})
    entities = data['entities']
    stats.update(annonces=len(data['announcements']), announcements=len(data['announcements']), entities=len(entities),
                 organisations=sum(e.get('type') in ('societe', 'association') for e in entities),
                 personnes=sum(e.get('type') == 'personne' for e in entities), relations=len(data['relations']))
    stats['categories'] = dict(Counter(a.get('category', 'a_classer') for a in data['announcements']))


def _special_maintenance(root, mode, progress=None, cancelled=None, publish=True,
                        identity_entity_ids=None, identity_name=None):
    """Nettoyage exact ou regroupement global sans détruire les preuves."""
    root = Path(root).resolve()
    selected = _years(root, None)
    if not selected:
        raise ValueError('Aucune base annuelle disponible pour cette opération globale.')
    directory = root / 'data' / 'maintenance'
    directory.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    report = dict(id=run_id, mode=mode, years=selected, full=True, started_at=_now(), state='running',
                  issues=[], issue_counts={}, severity_counts={}, repairs=[], completed_years=[],
                  remaining_years=selected[:], backups=[], can_publish=False)
    stopped = cancelled if callable(cancelled) else (cancelled.is_set if cancelled is not None else lambda: False)
    global_ids = {table: {} for table in TABLES}
    identity_groups = Counter()
    safe_publication = True

    def checkpoint():
        save_json(directory / 'etat.json', report)

    try:
        for position, selected_year in enumerate(selected, 1):
            if stopped():
                report['state'] = 'cancelled'
                break
            report['current_year'] = selected_year
            if progress:
                progress(dict(year=selected_year, current=position, total=len(selected),
                              message=f'Entretien global de la base {selected_year}'))
            annual_path = root / f'data/{selected_year}/base.json'
            db_path = root / f'data/{selected_year}/jort-{selected_year}-fr.sqlite'
            try:
                data = _read(annual_path)
                if not isinstance(data, dict):
                    raise ValueError('Base annuelle invalide')
            except (OSError, ValueError) as exc:
                _issue(report, 'annual_unreadable', f'Base annuelle absente ou illisible : {exc}', selected_year, 'error')
                safe_publication = False
                report['completed_years'].append(selected_year)
                report['remaining_years'].remove(selected_year)
                checkpoint()
                continue

            if mode == 'deduplicate_announcements':
                kept, signatures, removed = [], {}, 0
                announcements = data.get('announcements')
                if isinstance(announcements, list):
                    for row in announcements:
                        if not isinstance(row, dict) or not row.get('id'):
                            kept.append(row)
                            continue
                        identifier = str(row['id'])
                        signature = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                        seen = signatures.setdefault(identifier, set())
                        if signature in seen:
                            removed += 1
                            continue
                        seen.add(signature)
                        kept.append(row)
                conflicting = sum(len(values) > 1 for values in signatures.values())
                totals = report.setdefault('duplicate_cleanup', dict(exact_rows_removed=0, conflicting_ids=0))
                totals['exact_rows_removed'] += removed
                totals['conflicting_ids'] += conflicting
                if removed and isinstance(announcements, list):
                    report['backups'].append(_backup(root, [annual_path, db_path], run_id))
                    data['announcements'] = kept
                    _stats(data)
                    data.setdefault('maintenance', {})['announcement-dedupe-v1'] = dict(at=_now(), rows_removed=removed)
                    data['revision'] = revision(data)
                    report['pending_commit'] = dict(year=selected_year, revision=data['revision'])
                    checkpoint()
                    _sync_preserving_links(db_path, data)
                    save_json(annual_path, data)
                    report.pop('pending_commit', None)
                    report['repairs'].append(dict(rule='announcement-dedupe-v1', year=selected_year,
                                                  exact_rows_removed=removed, conflicting_ids_kept=conflicting))

            valid, maps = _validated(data, report, selected_year)
            if mode == 'merge_duplicates':
                identity_groups.update(key for entity in data.get('entities', [])
                                       if (key := identity_group_key(entity)))
            if mode == 'merge_person':
                requested_ids = set(str(value) for value in (identity_entity_ids or []))
                for entity in data.get('entities', []):
                    entity_id = str(entity.get('id') or '')
                    if entity_id not in requested_ids:
                        continue
                    if (entity.get('type') == 'personne'
                            and normaliser_nom_personne(entity.get('name')) == normaliser_nom_personne(identity_name)):
                        identity_groups[entity_id] += 1
                    else:
                        _issue(report, 'identity_target_mismatch',
                               'Une occurrence ciblée ne correspond pas à la personne indiquée.', selected_year,
                               'error', entity_id=entity_id)
                        safe_publication = False
            for table in TABLES:
                for identifier in maps[table]:
                    if identifier in global_ids[table]:
                        _issue(report, 'cross_year_duplicate', f'Identifiant {table} partagé entre années.',
                               selected_year, 'error', other_year=global_ids[table][identifier])
                        valid = False
                    global_ids[table][identifier] = selected_year
            safe_publication = safe_publication and valid
            report['completed_years'].append(selected_year)
            report['remaining_years'].remove(selected_year)
            checkpoint()

        if report['state'] == 'running':
            if mode == 'merge_person':
                requested_ids = set(str(value) for value in (identity_entity_ids or []))
                found_ids = set(identity_groups)
                missing_ids = sorted(requested_ids - found_ids)
                if missing_ids:
                    _issue(report, 'identity_target_missing',
                           'Certaines occurrences ciblées ne figurent plus dans les bases annuelles.',
                           severity='error', entity_ids=missing_ids)
                    safe_publication = False
                if len(requested_ids) < 2 or len(requested_ids) != len(identity_entity_ids or []):
                    _issue(report, 'identity_target_invalid', 'La fusion doit cibler au moins deux occurrences distinctes.', severity='error')
                    safe_publication = False
                if safe_publication:
                    merge_path = root / 'data' / 'identity-merges.json'
                    merge_data = _read(merge_path, {'version': 1, 'groups': []})
                    group_id = 'selma-elloumi-rekik-confirmed-v1'
                    group = dict(id=group_id, name=identity_name, entity_ids=sorted(requested_ids),
                                 confirmed_by='instruction utilisateur', reason='Fusion explicitement demandée ; les annonces sources sont conservées.',
                                 confirmed_at=_now())
                    existing = next((row for row in merge_data.get('groups', []) if row.get('id') == group_id), None)
                    same_confirmation = bool(existing and existing.get('name') == identity_name
                                             and sorted(str(value) for value in existing.get('entity_ids', [])) == sorted(requested_ids))
                    if not same_confirmation:
                        if merge_path.exists():
                            report['backups'].append(_backup(root, [merge_path], run_id))
                        groups = [row for row in merge_data.get('groups', []) if row.get('id') != group_id]
                        groups.append(group)
                        merge_data.update(version=1, groups=groups)
                        save_json(merge_path, merge_data)
                        report['repairs'].append(dict(rule='manual-identity-merge-v1', name=identity_name,
                                                      occurrences=len(requested_ids), evidence_preserved=True))
                    report['identity_merge'] = dict(groups=1, occurrences_grouped=len(requested_ids),
                        manual=True, name=identity_name, published=False,
                        handling='confirmation humaine ciblée par identifiants d’occurrence ; annonces sources conservées')
            report['can_publish'] = safe_publication
            if mode == 'merge_duplicates':
                report['identity_merge'] = dict(
                    groups=sum(size > 1 for size in identity_groups.values()),
                    occurrences_grouped=sum(size for size in identity_groups.values() if size > 1),
                    people_groups=sum(size > 1 for key, size in identity_groups.items() if key.startswith('personne:')),
                    organization_groups=sum(size > 1 for key, size in identity_groups.items() if key.startswith('organisation:')),
                    handling='regroupement de restitution ; bases annuelles, occurrences et annonces sources conservées')
            if mode == 'merge_duplicates' and publish and safe_publication:
                published = reconstruire(root)
                report['published_revision'] = published['revision']
                report['identity_merge']['published'] = True
            elif mode == 'merge_person' and publish and safe_publication:
                published = reconstruire(root)
                report['published_revision'] = published['revision']
                report['identity_merge']['published'] = True
            elif mode == 'deduplicate_announcements' and publish and safe_publication:
                published = reconstruire(root)
                report['published_revision'] = published['revision']
            report['state'] = 'done'
        report['finished_at'] = _now()
    except Exception as exc:
        report.update(state='failed', finished_at=_now(), error=f'{type(exc).__name__}: {exc}',
                      error_traceback=traceback.format_exc())
        _issue(report, 'maintenance_error', report['error'], report.get('current_year'), 'error')
    checkpoint()
    save_json(directory / f'rapport-{run_id}.json', report)
    save_json(directory / 'dernier-rapport.json', report)
    return report


@contextmanager
def _maintenance_lock(root):
    path = Path(root) / 'data/maintenance/maintenance.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError('Une maintenance fonctionne déjà ; attendez sa fin.') from exc
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def maintenance(root, mode='check', years=None, full=True, progress=None, cancelled=None, publish=True, deep_hashes=False,
                identity_entity_ids=None, identity_name=None, identity_options=None):
    with _maintenance_lock(root):
        if mode in ('reextract_entities', 'clean_parasitic_extractions', 'merge_entities', 'undo_merge'):
            from corriger_entites_et_liens import executer
            return executer(root, mode, years, progress, cancelled, publish, identity_options)
        return _maintenance(root, mode, years, full, progress, cancelled, publish, deep_hashes,
                            identity_entity_ids, identity_name)


def _maintenance(root, mode='check', years=None, full=True, progress=None, cancelled=None, publish=True, deep_hashes=False,
                 identity_entity_ids=None, identity_name=None):
    """Rapport durable. Les réparations sont ciblées et les PDF ne sont pas relus."""
    root = Path(root).resolve()
    if mode not in ('check', 'repair', 'connections', 'deduplicate_announcements', 'merge_duplicates', 'merge_person', 'optimize', 'backup'):
        raise ValueError('Mode de maintenance inconnu')
    if mode in ('deduplicate_announcements', 'merge_duplicates', 'merge_person'):
        return _special_maintenance(root, mode, progress, cancelled, publish, identity_entity_ids, identity_name)
    selected = _years(root, years)
    global_scope = set(selected) == set(_years(root, None))
    if mode == 'optimize':
        return _optimize(root, selected, progress, cancelled)
    if mode == 'backup':
        return _backup_report(root, selected, progress, cancelled)
    directory = root / 'data' / 'maintenance'
    directory.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    state_path = directory / 'etat.json'
    previous = _read(state_path, {})
    report = dict(id=run_id, mode=mode, years=selected, full=full, deep_hashes=deep_hashes, started_at=_now(), state='running', issues=[],
                  issue_counts={}, severity_counts={}, repairs=[], completed_years=[], remaining_years=selected[:], identity_groups=[], title_recoveries=[], title_audit=[],
                  previous_run=previous.get('id'), year_revisions={}, resumed_from=previous.get('id') if previous.get('state') in ('running', 'cancelled', 'failed') else None)
    stopped = cancelled if callable(cancelled) else (cancelled.is_set if cancelled is not None else lambda: False)
    global_ids = {table: {} for table in TABLES}
    safe_publication = True

    def checkpoint():
        save_json(state_path, report)

    checkpoint()
    try:
        for position, selected_year in enumerate(selected, 1):
            if stopped():
                report['state'] = 'cancelled'
                break
            report['current_year'] = selected_year
            if progress:
                progress(dict(year=selected_year, current=position, total=len(selected), message=f'Contrôle de la base {selected_year}'))
            annual_path = root / f'data/{selected_year}/base.json'
            db_path = root / f'data/{selected_year}/jort-{selected_year}-fr.sqlite'
            try:
                data = _read(annual_path)
                if not isinstance(data, dict):
                    raise ValueError('Base absente ou invalide')
            except (OSError, ValueError):
                _issue(report, 'annual_unreadable', 'Base annuelle absente ou illisible.', selected_year, 'error')
                safe_publication = False
                report['completed_years'].append(selected_year)
                report['remaining_years'].remove(selected_year)
                checkpoint()
                continue
            valid, maps = _validated(data, report, selected_year)
            # Les occurrences restent toutes dans les données avec leur annonce
            # et leur preuve. Le graphe les regroupe déjà par nom ; cette mesure
            # rend visible le regroupement automatique CIN + nom dans le rapport.
            identity_groups = {}
            for entity in data.get('entities', []):
                if entity.get('type') != 'personne' or not entity.get('cin'):
                    continue
                cin = ''.join(ch for ch in str(entity['cin']) if ch.isdigit())
                name = cle_prenom_nom(entity.get('name'))
                if len(cin) == 8 and name:
                    identity_groups.setdefault((cin, name), []).append(entity)
            safe_groups = [rows for rows in identity_groups.values() if len(rows) > 1]
            if safe_groups:
                report['identity_groups'].append(dict(
                    year=selected_year,
                    groups=len(safe_groups),
                    extra_occurrences=sum(len(rows) - 1 for rows in safe_groups),
                    handling='même CIN et mêmes prénom/nom regroupés automatiquement ; occurrences et annonces sources conservées'))
            for table in TABLES:
                for identifier in maps[table]:
                    if identifier in global_ids[table]:
                        _issue(report, 'cross_year_duplicate', f'Identifiant {table} partagé entre années.', selected_year, 'error', other_year=global_ids[table][identifier])
                        valid = False
                    global_ids[table][identifier] = selected_year
            safe_publication = safe_publication and valid
            if not valid:
                report['completed_years'].append(selected_year)
                report['remaining_years'].remove(selected_year)
                checkpoint()
                continue
            if (report['resumed_from'] and previous.get('mode') == mode and previous.get('years') == selected
                    and previous.get('full') == full and previous.get('deep_hashes') == deep_hashes
                    and previous.get('year_revisions', {}).get(str(selected_year)) == data.get('revision')
                    and selected_year in previous.get('completed_years', [])):
                report.setdefault('reused_years', []).append(selected_year)
                report['year_revisions'][str(selected_year)] = data.get('revision')
                report['completed_years'].append(selected_year)
                report['remaining_years'].remove(selected_year)
                checkpoint()
                continue
            _pdf_checks(root, data['sources'], report, selected_year, deep_hashes, stopped)
            can_sync, drift = _sqlite_checks(db_path, data, report, selected_year, full)
            entities, relations = data['entities'], data['relations']
            changed_announcements = set()
            by_announcement = {}
            relations_by_announcement = {}
            repair_details = {}
            known_entity_ids = {str(entity.get('id')) for entity in entities}
            title_audit = dict(year=selected_year, without_named_entity=0, recoverable=0,
                               unresolved=0, examples=[])
            for entity in entities:
                by_announcement.setdefault(entity.get('announcement_id'), []).append(entity)
            for relation in relations:
                relations_by_announcement.setdefault(relation.get('announcement_id'), []).append(relation)
            for announcement in data['announcements']:
                if stopped():
                    break
                current = by_announcement.get(announcement['id'], [])
                if mode == 'repair':
                    previous_title = json.dumps([announcement, current], sort_keys=True, ensure_ascii=False)
                    completer_titre_annonce(announcement, current)
                    if json.dumps([announcement, current], sort_keys=True, ensure_ascii=False) != previous_title:
                        new_entities = [e for e in current if str(e['id']) not in known_entity_ids]
                        entities.extend(new_entities)
                        known_entity_ids.update(str(e['id']) for e in new_entities)
                        by_announcement[announcement['id']] = current
                        changed_announcements.add(announcement['id'])
                        repair_details.setdefault(announcement['id'], []).append(dict(title=announcement.get('title'), confidence=announcement.get('title_confidence')))
                has_named_organization = any(
                    entity.get('type') in ('societe', 'association', 'organisation', 'fonds_commerce')
                    and str(entity.get('name') or '').strip()
                    and str(entity.get('name') or '').strip().casefold() != 'entité non identifiée dans l’extrait'
                    and not re.fullmatch(r'\d{4}[A-Z0-9-]+', str(entity.get('name') or '').strip(), re.I)
                    for entity in current)
                if not has_named_organization:
                    title_audit['without_named_entity'] += 1
                    recovered_name = nom_entite_entete(announcement)
                    if recovered_name:
                        title_audit['recoverable'] += 1
                        if mode != 'repair':
                            _issue(report, 'recoverable_title', 'Nom d’entité relevé en tête d’annonce près d’un identifiant.', selected_year,
                                   announcement_id=announcement['id'], entity_name=recovered_name)
                        if mode == 'repair':
                            entity_id = f"{announcement['id']}:organisation-titre"
                            if entity_id not in known_entity_ids:
                                recovered = dict(id=entity_id, name=recovered_name, type='organisation',
                                                 announcement_id=announcement['id'])
                                entities.append(recovered)
                                known_entity_ids.add(entity_id)
                                current.append(recovered)
                                by_announcement[announcement['id']] = current
                                changed_announcements.add(announcement['id'])
                                repair_details.setdefault(announcement['id'], []).append(dict(
                                    entity_id=entity_id, changes={'title': {'before': None, 'after': recovered_name}}))
                    else:
                        title_audit['unresolved'] += 1
                        if len(title_audit['examples']) < 20:
                            title_audit['examples'].append(dict(announcement_id=announcement['id'],
                                reference=announcement.get('reference'), pages=announcement.get('pages', [])))
                current_relations = relations_by_announcement.get(announcement['id'], [])
                # Récupère les bénéficiaires d'augmentation de capital depuis le
                # texte déjà indexé ; aucune relecture du PDF n'est nécessaire.
                capital = reparer_souscriptions_capital(announcement, current, current_relations)
                if capital['relations']:
                    names=[entity.get('name') for entity in capital['entities']]
                    if mode != 'repair':
                        _issue(report, 'missing_capital_relations',
                               'Des bénéficiaires explicitement désignés d’une augmentation de capital peuvent être ajoutés au graphe.',
                               selected_year, announcement_id=announcement['id'], names=names)
                    else:
                        entities.extend(capital['entities'])
                        relations.extend(capital['relations'])
                        current.extend(capital['entities'])
                        current_relations.extend(capital['relations'])
                        known_entity_ids.update(str(entity.get('id')) for entity in capital['entities'])
                        changed_announcements.add(announcement['id'])
                        repair_details.setdefault(announcement['id'], []).append(dict(
                            capital_relations_added=len(capital['relations']), subscribers=names,
                            note='Désignation publiée ; réalisation effective de la souscription non déduite.'))
                # La réparation des ventes n'extrait pas à nouveau toutes les annonces.
                if not any(':partie-vente:' in str(e.get('id')) for e in current):
                    continue
                result = reparer_parties_vente(announcement, current, current_relations)
                for warning in result.get('warnings', []):
                    _issue(report, 'sale_uncertain', str(warning), selected_year, announcement_id=announcement['id'])
                if not result.get('changed'):
                    continue
                _issue(report, 'sale_identity', 'Type ou identifiant d’une partie de vente à corriger.', selected_year, announcement_id=announcement['id'])
                if mode != 'repair':
                    continue
                # Les IDs stables permettent de préserver les décisions et liens.
                replacements = {e['id']: e for e in result['entities']}
                details = []
                for entity in current:
                    replacement = replacements.get(entity['id'])
                    if replacement is not None:
                        fields = ('name', 'type', 'identifier_type')
                        changes = {key: {'before': entity.get(key), 'after': replacement.get(key)} for key in fields if entity.get(key) != replacement.get(key)}
                        if entity.get('cin') != replacement.get('cin'):
                            changes['cin_present'] = dict(before=bool(entity.get('cin')), after=bool(replacement.get('cin')))
                        if changes:
                            details.append(dict(entity_id=entity['id'], changes=changes))
                        entity.clear()
                        entity.update(replacement)
                rel_replacements = {r['id']: r for r in result['relations']}
                for rel in current_relations:
                    replacement = rel_replacements.get(rel['id'])
                    if replacement is not None:
                        rel.clear()
                        rel.update(replacement)
                changed_announcements.add(announcement['id'])
                repair_details.setdefault(announcement['id'], []).extend(details)
            report['title_audit'].append(title_audit)
            if stopped():
                report['state'] = 'cancelled'
                break
            if mode == 'repair' and can_sync and (changed_announcements or drift):
                report.setdefault('backups', []).append(_backup(root, [annual_path, db_path], run_id))
                if changed_announcements:
                    _stats(data)
                    data.setdefault('maintenance', {})[RULE_VERSION] = dict(at=_now(), announcement_ids=sorted(changed_announcements))
                    data['revision'] = revision(data)
                affected_sources = {a['source_id'] for a in data['announcements'] if a['id'] in changed_announcements}
                numbers = {s['number'] for s in data['sources'] if s['id'] in affected_sources}
                report['pending_commit'] = dict(year=selected_year, revision=data.get('revision') or revision(data))
                checkpoint()
                _sync_preserving_links(db_path, data, None if drift else numbers)
                if changed_announcements:
                    save_json(annual_path, data)
                report.pop('pending_commit', None)
                for aid in sorted(changed_announcements):
                    report['repairs'].append(dict(rule=RULE_VERSION, year=selected_year, announcement_id=aid, changes=repair_details[aid]))
                    for detail in repair_details[aid]:
                        title_change = detail.get('changes', {}).get('title')
                        if title_change and title_change.get('after'):
                            report['title_recoveries'].append(dict(year=selected_year,
                                announcement_id=aid, name=title_change['after']))
                if drift:
                    report['repairs'].append(dict(rule='sqlite-sync-1', year=selected_year))
            elif changed_announcements and not can_sync:
                _issue(report, 'repair_deferred', 'Réparation différée : SQLite nécessite une récupération préalable.', selected_year, 'error')
            report['completed_years'].append(selected_year)
            report['remaining_years'].remove(selected_year)
            report['year_revisions'][str(selected_year)] = data.get('revision') or revision(data)
            checkpoint()
        if report['state'] == 'running':
            report['can_publish'] = safe_publication
            if global_scope:
                from referentiel_entites import verifier
                try:
                    for message in verifier(root, global_ids['entities']):
                        _issue(report, 'identity_registry', message)
                except (OSError,ValueError,sqlite3.Error) as exc:
                    _issue(report, 'identity_registry', f'Référentiel à vérifier : {exc}', severity='error')
                    safe_publication = False
                for path in [root / 'data/logs/interventions-identites.json', *sorted((root / 'data').glob('[0-9]*/logs/interventions.json'))]:
                    try:
                        for ticket in (_read(path, {}) or {}).values():
                            if ticket.get('announcement_id') and str(ticket['announcement_id']) not in global_ids['announcements']:
                                _issue(report, 'decision_reference', 'Intervention liée à une annonce absente ; historique conservé.', ticket_id=ticket.get('id'))
                            for identifier in ticket.get('entity_ids', []):
                                if str(identifier) not in global_ids['entities']:
                                    _issue(report, 'decision_reference', 'Intervention liée à une occurrence absente ; historique conservé.', ticket_id=ticket.get('id'), entity_id=identifier)
                    except (OSError, ValueError, AttributeError):
                        _issue(report, 'decision_unreadable', 'Fichier de décisions illisible.', severity='error', path=str(path.relative_to(root)))
            report['can_publish'] = safe_publication
            if mode in ('repair', 'connections') and publish and safe_publication:
                published = reconstruire(root)
                report['published_revision'] = published['revision']
            report['state'] = 'done'
        report['finished_at'] = _now()
    except Exception as exc:
        report.update(state='failed', finished_at=_now(), error=f'{type(exc).__name__}: {exc}', error_traceback=traceback.format_exc())
        _issue(report, 'maintenance_error', report['error'], report.get('current_year'), 'error')
    checkpoint()
    save_json(directory / f'rapport-{run_id}.json', report)
    save_json(directory / 'dernier-rapport.json', report)
    return report


def _optimize(root, selected, progress=None, cancelled=None):
    """Optimise puis compacte SQLite, en conservant un rapport et une sauvegarde."""
    directory = root / 'data' / 'maintenance'
    directory.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    state_path = directory / 'etat.json'
    previous = _read(state_path, {})
    report = dict(id=run_id, mode='optimize', years=selected, full=True, started_at=_now(), state='running',
                  issues=[], issue_counts={}, severity_counts={}, repairs=[], completed_years=[], remaining_years=selected[:],
                  previous_run=previous.get('id'), sizes=[], backups=[], can_publish=False)
    stopped = cancelled if callable(cancelled) else (cancelled.is_set if cancelled is not None else lambda: False)

    def checkpoint():
        save_json(state_path, report)

    def fail(code, message, selected_year):
        _issue(report, code, message, selected_year, 'error')
        report['state'] = 'failed'
        report['error'] = message

    checkpoint()
    try:
        for position, selected_year in enumerate(selected, 1):
            if stopped():
                report['state'] = 'cancelled'
                break
            report['current_year'] = selected_year
            if progress:
                progress(dict(year=selected_year, current=position, total=len(selected), message=f'Optimisation de la base {selected_year}'))
            annual_path = root / f'data/{selected_year}/base.json'
            db_path = root / f'data/{selected_year}/jort-{selected_year}-fr.sqlite'
            if not db_path.is_file():
                _issue(report, 'sqlite_missing', 'Base SQLite absente ; aucune modification effectuée.', selected_year)
                report['completed_years'].append(selected_year)
                report['remaining_years'].remove(selected_year)
                checkpoint()
                continue
            try:
                with closing(sqlite3.connect(db_path, timeout=3)) as db:
                    result = db.execute('PRAGMA integrity_check').fetchmany(10)
                    if len(result) != 1 or result[0][0] != 'ok':
                        fail('sqlite_integrity', 'Échec du contrôle integrity_check ; base laissée intacte.', selected_year)
                        break
                size_before = db_path.stat().st_size
                report['backups'].append(_backup(root, [annual_path, db_path], run_id))
                report['sizes'].append(dict(year=selected_year, before_bytes=size_before, after_bytes=None))
                checkpoint()
                if stopped():
                    report['state'] = 'cancelled'
                    break
                with closing(sqlite3.connect(db_path, timeout=3)) as db:
                    db.execute('PRAGMA optimize')
                    db.execute('VACUUM')
                    result = db.execute('PRAGMA integrity_check').fetchmany(10)
                    if len(result) != 1 or result[0][0] != 'ok':
                        fail('sqlite_integrity_after', 'Échec du contrôle integrity_check après optimisation.', selected_year)
                        break
                report['sizes'][-1]['after_bytes'] = db_path.stat().st_size
                report['completed_years'].append(selected_year)
                report['remaining_years'].remove(selected_year)
                checkpoint()
            except (OSError, sqlite3.DatabaseError) as exc:
                fail('sqlite_optimize', f'Échec de l’optimisation SQLite : {exc}', selected_year)
                break
        if report['state'] == 'running':
            report['state'] = 'done'
        report['finished_at'] = _now()
    except Exception as exc:
        report.update(state='failed', finished_at=_now(), error=f'{type(exc).__name__}: {exc}')
        _issue(report, 'maintenance_error', report['error'], report.get('current_year'), 'error')
    checkpoint()
    save_json(directory / f'rapport-{run_id}.json', report)
    save_json(directory / 'dernier-rapport.json', report)
    return report


def _backup_report(root, selected, progress=None, cancelled=None):
    """Crée un point de sauvegarde cohérent des fichiers annuels existants."""
    directory = root / 'data' / 'maintenance'
    directory.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    state_path = directory / 'etat.json'
    previous = _read(state_path, {})
    report = dict(id=run_id, mode='backup', years=selected, full=False, started_at=_now(), state='running',
                  issues=[], issue_counts={}, severity_counts={}, completed_years=[], remaining_years=selected[:],
                  previous_run=previous.get('id'), backups=[], sizes=[], can_publish=False)
    stopped = cancelled if callable(cancelled) else (cancelled.is_set if cancelled is not None else lambda: False)

    def checkpoint():
        save_json(state_path, report)

    checkpoint()
    try:
        identity_paths = [root / 'data/referentiel-entites.sqlite', root / 'data/identity-merges.json']
        if any(path.is_file() for path in identity_paths):
            report['backups'].append(_backup(root, identity_paths, run_id))
        for position, selected_year in enumerate(selected, 1):
            if stopped():
                report['state'] = 'cancelled'
                break
            report['current_year'] = selected_year
            if progress:
                progress(dict(year=selected_year, current=position, total=len(selected), message=f'Sauvegarde de la base {selected_year}'))
            annual_path = root / f'data/{selected_year}/base.json'
            db_path = root / f'data/{selected_year}/jort-{selected_year}-fr.sqlite'
            if not annual_path.is_file() and not db_path.is_file():
                _issue(report, 'backup_missing', 'Aucun fichier annuel à sauvegarder.', selected_year)
            else:
                report['backups'].append(_backup(root, [annual_path, db_path], run_id))
                report['sizes'].append(dict(year=selected_year,
                    json_bytes=annual_path.stat().st_size if annual_path.is_file() else None,
                    sqlite_bytes=db_path.stat().st_size if db_path.is_file() else None))
            report['completed_years'].append(selected_year)
            report['remaining_years'].remove(selected_year)
            checkpoint()
        if report['state'] == 'running':
            report['state'] = 'done'
        report['finished_at'] = _now()
    except Exception as exc:
        report.update(state='failed', finished_at=_now(), error=f'{type(exc).__name__}: {exc}')
        _issue(report, 'maintenance_error', report['error'], report.get('current_year'), 'error')
    checkpoint()
    save_json(directory / f'rapport-{run_id}.json', report)
    save_json(directory / 'dernier-rapport.json', report)
    return report


def startup_check(root, budget_seconds=2):
    """Lecture des seuls petits marqueurs ; aucune reconstruction ni lecture PDF."""
    root = Path(root).resolve()
    started = time.monotonic()
    result = dict(checked_at=_now(), state='ok', warnings=[], deferred_checks=[], mode='light')
    for relative in ('data/collecte-traitement.json', 'data/logs/interventions-identites.json', 'data/maintenance/etat.json', 'dist/publication.json'):
        if time.monotonic() - started > budget_seconds:
            result['warnings'].append('Budget de contrôle atteint ; contrôle complet disponible dans l’administration.')
            break
        try:
            path = root / relative
            if path.exists() and path.stat().st_size > 512 * 1024:
                result['deferred_checks'].append(f'{relative} : fichier volumineux, lecture approfondie différée au contrôle administrateur.')
                continue
            value = _read(path, {})
            if relative.endswith('publication.json') and value.get('state') not in (None, 'ready'):
                result['warnings'].append('Publication interrompue : vérifier les vues de restitution.')
            if relative.endswith('etat.json') and value.get('state') in ('running', 'failed', 'cancelled'):
                result['warnings'].append('Une maintenance précédente est incomplète ; reprise disponible.')
        except (OSError, ValueError):
            result['warnings'].append(f'État illisible : {relative}')
    result['state'] = 'attention' if result['warnings'] else 'ok'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('check', 'repair', 'reextract_entities', 'merge_entities', 'undo_merge', 'connections', 'deduplicate_announcements', 'merge_duplicates', 'optimize', 'backup'), default='check')
    parser.add_argument('--years', nargs='*', type=int, default=None)
    parser.add_argument('--light', action='store_true')
    parser.add_argument('--deep-hashes', action='store_true')
    args = parser.parse_args()
    if args.light:
        print(json.dumps(startup_check(ROOT), ensure_ascii=False))
        return 0
    with socket.socket() as probe:
        probe.settimeout(.3)
        if probe.connect_ex(('127.0.0.1', 8765)) == 0:
            parser.error('Le serveur fonctionne : lancez la maintenance depuis l’administration pour coordonner les écritures.')
    report = maintenance(ROOT, mode=args.mode, years=args.years, deep_hashes=args.deep_hashes, publish=False)
    if args.mode in ('repair', 'reextract_entities', 'connections', 'deduplicate_announcements', 'merge_duplicates') and report['state'] == 'done' and report.get('can_publish'):
        # Le serveur est arrêté ; mêmes règles de tickets/décisions que l'API.
        from serveur_local import select_year, publish_boundary
        for selected_year in report['years']:
            select_year(selected_year)
        publish_boundary()
    print(json.dumps({k: report.get(k) for k in ('id', 'state', 'issue_counts', 'severity_counts', 'published_revision', 'error')}, ensure_ascii=False))
    return 1 if report['state'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
