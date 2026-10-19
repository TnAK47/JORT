"""Compléter les entités et liens depuis les textes conservés, sans relire les PDF.

Utilisation, serveur arrêté : python scripts/corriger_entites_et_liens.py
Dans l'application : Entretien de la base > Opérations avancées > Compléter les titres et les liens, ou Corriger les noms parasités.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import uuid

from collecter_2024 import save_json
from extraire_entites import (enrichir, ENTITY_EXTRACTION_VERSION, famille_role, completer_titre_annonce,
    nettoyer_nom_personne_extrait, nom_personne_extraction_parasitee)
from rapprochement_identites import normaliser_nom_personne
from referentiel_entites import registry_path, fusionner, annuler_fusion


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _source_digest(announcements):
    return _digest([{key: row.get(key) for key in ('id','reference','source_id','text','pages','source_date')}
                    for row in announcements])


def extraction_parasitee(entities):
    """Repère une occurrence de personne qui déborde sur un connecteur de phrase."""
    return any(row.get('type') == 'personne' and nom_personne_extraction_parasitee(row.get('name'))
               for row in entities)


def corriger_extractions_parasites(entities, relations, announcement_id):
    """Corrige un suffixe de phrase certain sans altérer l’annonce ni sa preuve."""
    entities = [dict(row) for row in entities]
    relations = [dict(row) for row in relations]
    fixes = []
    for row in list(entities):
        if row.get('type') != 'personne' or not nom_personne_extraction_parasitee(row.get('name')):
            continue
        before = row.get('name')
        corrected = nettoyer_nom_personne_extrait(before)
        target = next((candidate for candidate in entities
                       if candidate is not row and candidate.get('type') == 'personne'
                       and normaliser_nom_personne(candidate.get('name')) == normaliser_nom_personne(corrected)), None)
        if target is None:
            # Le motif est déterministe (« Nom. En ») : garder l’ID et sa
            # preuve, mais corriger le libellé indexé pour le futur regroupement.
            row['name'] = corrected
            fixes.append(dict(announcement_id=announcement_id, entity_id=row['id'], replacement_id=row['id'],
                              before=before, after=corrected, reason='suffixe_de_phrase_absorbe'))
            continue
        old_id, target_id = row['id'], target['id']
        for relation in relations:
            if relation.get('from') == old_id:
                relation['from'] = target_id
            if relation.get('to') == old_id:
                relation['to'] = target_id
        entities.remove(row)
        fixes.append(dict(announcement_id=announcement_id, entity_id=old_id, replacement_id=target_id,
                          before=before, after=target.get('name'), reason='suffixe_de_phrase_absorbe'))
    # Après remappage, deux relations peuvent devenir identiques. Une seule preuve
    # par signature est gardée ; les annonces et leurs passages restent conservés.
    unique, seen = [], set()
    for relation in relations:
        signature = (relation.get('from'), relation.get('to'), normaliser_nom_personne(relation.get('role')),
                     relation.get('type'), relation.get('event_type'), relation.get('announcement_id'))
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(relation)
    return entities, unique, fixes


def completer_annonce(announcement, current_entities, current_relations):
    """Réconcilie les résultats : IDs existants et décisions humaines conservés."""
    fresh = enrichir([dict(announcement)])
    aid = str(announcement['id'])
    entities = [dict(row) for row in current_entities]
    relations = [dict(row) for row in current_relations]
    id_map = {}
    changed_titles = 0
    for row in fresh['entities']:
        previous = next((old for old in entities if old['id'] == row['id']), None)
        if previous is None:
            candidates = [old for old in entities if old.get('type') == row.get('type')
                          and normaliser_nom_personne(old.get('name')) == normaliser_nom_personne(row.get('name'))
                          and not (old.get('cin') and row.get('cin') and old['cin'] != row['cin'])]
            previous = candidates[0] if candidates else None
        # Numeric IDs from an older extraction can refer to a different name.
        if previous is not None and not row.get('is_primary') and (
                previous.get('type') != row.get('type') or
                normaliser_nom_personne(previous.get('name')) != normaliser_nom_personne(row.get('name'))):
            previous = next((old for old in entities if old.get('type') == row.get('type')
                             and normaliser_nom_personne(old.get('name')) == normaliser_nom_personne(row.get('name'))), None)
        if previous is None:
            new = dict(row)
            if row.get('is_primary'):
                changed_titles += 1
            if any(old['id'] == new['id'] for old in entities):
                new['id'] = aid + ':entite-complete:' + _digest([row['type'], row['name']])[:16]
            entities.append(new)
            previous = new
        else:
            if previous.get('type') == row.get('type') == 'personne' and previous.get('cin') and row.get('cin') and previous['cin'] != row['cin']:
                raise ValueError('CIN contradictoire pour une occurrence ; attribution conservée pour vérification.')
            if row.get('is_primary') and previous.get('name') != row.get('name'):
                changed_titles += 1
            # Keep user validation and identifiers not recovered by this parser.
            for key, value in row.items():
                if key not in ('id', 'status', 'identity_match_status', 'canonical_id', 'identity_group_key'):
                    previous[key] = value
        id_map[row['id']] = previous['id']
    for row in fresh['relations']:
        new = dict(row)
        new['from'], new['to'] = id_map[row['from']], id_map[row['to']]
        signature = (new['from'], new['to'], normaliser_nom_personne(new.get('role')), new.get('type'), new.get('event_type'))
        previous = next((old for old in relations if
                        (old.get('from'), old.get('to'), normaliser_nom_personne(old.get('role')), old.get('type'), old.get('event_type')) == signature), None)
        if previous:
            if previous.get('status') not in ('valide', 'a_corriger', 'human_confirmed'):
                previous.update({key: value for key, value in new.items() if key != 'id'})
        else:
            new['id'] = aid + ':relation-complete:' + _digest(signature)[:16]
            relations.append(new)
    for relation in relations:
        relation.setdefault('role_family', famille_role(relation.get('role', '')))
    normalized_text = ' '.join(str(announcement.get('text') or '').split())
    for relation in relations:
        if relation.get('id') not in {row.get('id') for row in current_relations}:
            if not relation.get('quote') or relation['quote'] not in normalized_text:
                raise ValueError('Relation nouvelle sans passage source vérifiable.')
    completer_titre_annonce(announcement, entities)
    entities, relations, parasite_fixes = corriger_extractions_parasites(entities, relations, aid)
    return entities, relations, changed_titles, parasite_fixes


def executer(root, mode='reextract_entities', years=None, progress=None, cancelled=None,
             publish=True, identity_options=None, force=False):
    # Import locally: maintenance owns the shared lock and calls this module.
    from maintenance_base import _years, _read, _validated, _backup, _sync_preserving_links, _stats, _sqlite_checks, _issue
    from agregation_base import reconstruire, revision
    root = Path(root).resolve()
    selected = _years(root, years if mode in ('reextract_entities', 'clean_parasitic_extractions') else None)
    directory = root / 'data/maintenance'
    directory.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    report = dict(id=run_id, mode=mode, years=selected, started_at=datetime.now(timezone.utc).isoformat(),
                  state='running', issues=[], issue_counts={}, severity_counts={}, repairs=[], backups=[],
                  completed_years=[], remaining_years=selected[:], can_publish=True,
                  extraction=dict(announcements=0, reused=0, added_entities=0, added_relations=0, corrected_titles=0,
                                  parasitic_occurrences_removed=0, targeted_announcements=0, failures=0))
    stopped = cancelled if callable(cancelled) else (cancelled.is_set if cancelled is not None else lambda: False)

    def checkpoint():
        save_json(directory / 'etat.json', report)

    try:
        checkpoint()
        if mode in ('merge_entities', 'undo_merge'):
            if not registry_path(root).exists():
                raise ValueError('Créez d’abord le référentiel avec « Compléter les titres et les liens ».')
            report['backups'].append(_backup(root, [registry_path(root)], run_id))
            options = identity_options or {}
            result = (fusionner(root, options.get('canonical_ids', []), options.get('label', ''), options.get('reason', ''))
                      if mode == 'merge_entities' else annuler_fusion(root, options.get('event_id', '')))
            if result.get('later_occurrences_to_review'):
                _issue(report, 'identity_after_split', f'{result["later_occurrences_to_review"]} occurrences ajoutées depuis la fusion restent à vérifier après sa séparation.')
            report['identity_merge'] = dict(result, manual=True, published=False, groups=1)
            report['repairs'].append(dict(rule=mode, **result))
            report['completed_years'], report['remaining_years'] = selected, []
        else:
            if registry_path(root).exists():
                report['backups'].append(_backup(root, [registry_path(root)], run_id))
            for position, selected_year in enumerate(selected, 1):
                if stopped():
                    report['state'] = 'cancelled'
                    break
                report['current_year'] = selected_year
                if progress:
                    progress(dict(year=selected_year, current=position-1, total=len(selected),
                                  message=(f'Nettoyer les extractions parasitées de {selected_year}' if mode == 'clean_parasitic_extractions'
                                           else f'Compléter les entités et liens de {selected_year}')))
                annual_path = root / f'data/{selected_year}/base.json'
                db_path = root / f'data/{selected_year}/jort-{selected_year}-fr.sqlite'
                try:
                    data = _read(annual_path)
                    valid, maps = _validated(data, report, selected_year)
                    if not valid:
                        raise ValueError('Références sources incohérentes ; données conservées.')
                    can_sync, drift = _sqlite_checks(db_path, data, report, selected_year, False)
                    if not can_sync:
                        raise ValueError('Base SQLite indisponible ; réparation différée.')
                    source_announcements = defaultdict(list)
                    for announcement in data['announcements']:
                        source_announcements[announcement['source_id']].append(announcement)
                    by_announcement = defaultdict(list)
                    by_relations = defaultdict(list)
                    for entity in data['entities']:
                        by_announcement[entity['announcement_id']].append(entity)
                    for relation in data['relations']:
                        by_relations[relation['announcement_id']].append(relation)
                    originals = _source_digest(data['announcements'])
                    pending = data.setdefault('entity_extraction_checkpoints', {})
                    affected = set()
                    local_count = 0
                    for source_id, announcements in source_announcements.items():
                        if stopped():
                            report['state'] = 'cancelled'
                            break
                        targets = announcements
                        if mode == 'clean_parasitic_extractions':
                            targets = [row for row in announcements if extraction_parasitee(by_announcement[row['id']])]
                            if not targets:
                                report['extraction']['reused'] += len(announcements)
                                continue
                        fingerprint = _digest([(row['id'], row.get('text', '')) for row in announcements])
                        expected = dict(version=ENTITY_EXTRACTION_VERSION, fingerprint=fingerprint)
                        if mode != 'clean_parasitic_extractions' and not force and pending.get(source_id) == expected:
                            report['extraction']['reused'] += len(announcements)
                            continue
                        source_ok = True
                        for announcement in targets:
                            if stopped():
                                source_ok = False
                                report['state'] = 'cancelled'
                                break
                            aid = announcement['id']
                            try:
                                old_entities, old_relations = by_announcement[aid], by_relations[aid]
                                completed, connected, titles, parasite_fixes = completer_annonce(announcement, old_entities, old_relations)
                                by_announcement[aid], by_relations[aid] = completed, connected
                                report['extraction']['announcements'] += 1
                                report['extraction']['targeted_announcements'] += int(mode == 'clean_parasitic_extractions')
                                report['extraction']['added_entities'] += len(completed)-len(old_entities)
                                report['extraction']['added_relations'] += len(connected)-len(old_relations)
                                report['extraction']['corrected_titles'] += titles
                                report['extraction']['parasitic_occurrences_removed'] += len(parasite_fixes)
                                for fix in parasite_fixes:
                                    report['repairs'].append(dict(rule='parasitic-person-name-v1', year=selected_year, **fix))
                                affected.add(source_id)
                            except Exception as exc:
                                source_ok = False
                                report['extraction']['failures'] += 1
                                _issue(report, 'extraction_incomplete', f'Annonce conservée : {exc}', selected_year,
                                       announcement_id=aid)
                            local_count += 1
                            if progress and local_count % 100 == 0:
                                progress(dict(year=selected_year, current=position-1 + local_count/max(1,len(data['announcements'])),
                                              total=len(selected), message=f'{selected_year} : {local_count}/{len(data["announcements"])} annonces examinées'))
                        if source_ok:
                            pending[source_id] = expected
                    data['entities'] = [row for announcement in data['announcements'] for row in by_announcement[announcement['id']]]
                    data['relations'] = [row for announcement in data['announcements'] for row in by_relations[announcement['id']]]
                    if _source_digest(data['announcements']) != originals:
                        raise ValueError('Les annonces sources ont changé ; correction refusée.')
                    valid, _ = _validated(data, report, selected_year)
                    if not valid:
                        raise ValueError('Contrôle des nouvelles connexions échoué.')
                    if affected or drift:
                        report['backups'].append(_backup(root, [annual_path, db_path], run_id))
                        _stats(data)
                        data['entity_extraction_version'] = ENTITY_EXTRACTION_VERSION
                        data['revision'] = revision(data)
                        report['pending_commit'] = dict(year=selected_year, revision=data['revision'])
                        checkpoint()
                        numbers = {row['number'] for row in data['sources'] if row['id'] in affected}
                        _sync_preserving_links(db_path, data, None if drift else numbers)
                        save_json(annual_path, data)
                        report.pop('pending_commit', None)
                        report['repairs'].append(dict(year=selected_year, rule=f'entities-and-links-v{ENTITY_EXTRACTION_VERSION}', announcements_preserved=True))
                    if report['state'] == 'cancelled':
                        checkpoint()
                        break
                    report['completed_years'].append(selected_year)
                    report['remaining_years'].remove(selected_year)
                    if progress:
                        progress(dict(year=selected_year, current=position, total=len(selected),
                                      message=f'{selected_year} : correction enregistrée ; annonces sources conservées'))
                    checkpoint()
                except Exception as exc:
                    report['can_publish'] = False
                    _issue(report, 'year_correction_failed', str(exc), selected_year, 'error')
                    checkpoint()
                    # A problem in one annual base never prevents the next one.
                    continue
        if report['state'] == 'running':
            if report['remaining_years']:
                report['state'] = 'failed'
                report['can_publish'] = False
                report['error'] = 'Années non corrigées : ' + ', '.join(map(str, report['remaining_years']))
            if publish and report['can_publish']:
                published = reconstruire(root)
                report['published_revision'] = published['revision']
                report['unique_entities'] = published['stats']['unique_entities']
                if 'identity_merge' in report:
                    report['identity_merge']['published'] = True
            if report['state'] == 'running':
                report['state'] = 'done'
    except Exception as exc:
        report.update(state='failed', can_publish=False, error=f'{type(exc).__name__}: {exc}')
        _issue(report, 'entity_correction_failed', report['error'], severity='error')
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    checkpoint()
    save_json(directory / f'rapport-{run_id}.json', report)
    save_json(directory / 'dernier-rapport.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--annees', nargs='+', type=int, help='Par défaut : toutes les bases annuelles présentes.')
    parser.add_argument('--mode', choices=('reextract_entities', 'clean_parasitic_extractions'), default='reextract_entities',
                        help='Réextraction générale, ou correction ciblée des suffixes de phrase absorbés.')
    parser.add_argument('--forcer', action='store_true', help='Réexaminer aussi les textes déjà corrigés par cette version.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as probe:
        probe.settimeout(.3)
        if probe.connect_ex(('127.0.0.1', 8765)) == 0:
            parser.error('Le serveur fonctionne : utilisez le bouton de maintenance dans l’application.')
    from maintenance_base import _maintenance_lock
    with _maintenance_lock(root):
        report = executer(root, mode=args.mode, years=args.annees, force=args.forcer,
                          progress=lambda row: print(json.dumps(row, ensure_ascii=False), flush=True))
    print(json.dumps({key: report.get(key) for key in ('id','state','extraction','remaining_years','published_revision','error')}, ensure_ascii=False))
    return 1 if report['state'] == 'failed' or report['remaining_years'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
