"""Regrouper les bases annuelles indexées pour la recherche du site privé."""
import hashlib
import json
import os
import tempfile
import time
from collections import Counter
from pathlib import Path
try:
    from .referentiel_entites import synchroniser as synchroniser_identites
except ImportError:
    from referentiel_entites import synchroniser as synchroniser_identites

try:
    from .rapprochement_identites import normaliser_nom_personne
except ImportError:
    from rapprochement_identites import normaliser_nom_personne


def revision(data):
    snapshot = dict(data)
    snapshot.pop('revision', None)
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def identity_group_key(entity):
    """Clé de regroupement prudente ; ne s'appuie jamais sur un nom seul."""
    kind = entity.get('type')
    if kind == 'personne':
        cin = ''.join(char for char in str(entity.get('cin') or '') if char.isdigit())
        if len(cin) != 8:
            return None
        if entity.get('identity_match_status') == 'confirme':
            return f'personne:cin-confirmee:{cin}'
        name = normaliser_nom_personne(entity.get('name'))
        if name:
            return f'personne:cin:{cin}:{name}'
        return None
    if kind in ('societe', 'association', 'organisation'):
        identifier_type = str(entity.get('identifier_type') or '')
        if identifier_type not in ('identifiant_unique_publie', 'identifiant_fiscal_publie', 'registre_commerce_publie'):
            return None
        identifier = ''.join(char for char in str(entity.get('identifier') or '').upper() if char.isalnum())
        if identifier:
            return f'organisation:{identifier_type}:{identifier}'
    return None


def _write_json(path, value):
    _write_text(path, json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n')


def _write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.stem}-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows may refuse the atomic replacement while the local web server,
        # browser, antivirus, or Drive client still has the previous publication open.
        # Keep the complete temporary file and retry long enough for short-lived readers.
        delays = (.2, .4, .8, 1.6, 2.5, 2.5, 2.5, 2.5, 2.5, 2.5, 2.5)
        for attempt in range(len(delays) + 1):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == len(delays):
                    raise
                time.sleep(delays[attempt])
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def reconstruire(root):
    """Publier dans dist une vue complète de toutes les années déjà indexées."""
    root = Path(root)
    from postgresql_local import enabled
    if enabled(root):
        from migration_postgresql import sync_changed
        return sync_changed(root)
    years = []
    sources, announcements, entities, relations = [], [], [], []
    yearly_coverage = {}
    annual_revisions = {}
    category_counts = Counter()
    pages = residual_fragments = 0

    for directory in sorted((root / 'data').iterdir(), key=lambda item: item.name):
        if not directory.is_dir() or not directory.name.isdigit():
            continue
        path = directory / 'base.json'
        if not path.is_file():
            continue
        year = int(directory.name)
        annual = json.loads(path.read_text(encoding='utf-8'))
        years.append(year)
        sources.extend(annual.get('sources', []))
        announcements.extend(annual.get('announcements', []))
        entities.extend(annual.get('entities', []))
        relations.extend(annual.get('relations', []))
        yearly_coverage[str(year)] = annual.get('coverage', {})
        annual_revisions[str(year)] = annual.get('revision') or revision(annual)
        stats = annual.get('stats', {})
        category_counts.update(stats.get('categories', {}))
        pages += int(stats.get('pages', 0) or 0)
        residual_fragments += int(stats.get('residual_fragments', 0) or 0)

    if not years:
        raise ValueError('Aucune base annuelle indexée à regrouper.')

    source_ids = [str(item['id']) for item in sources]
    announcement_ids = [str(item['id']) for item in announcements]
    entity_ids = [str(item['id']) for item in entities]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError('Identifiants de sources en double entre les bases annuelles.')
    if len(announcement_ids) != len(set(announcement_ids)):
        raise ValueError('Identifiants d’annonces en double entre les bases annuelles.')
    if len(entity_ids) != len(set(entity_ids)):
        raise ValueError('Identifiants d’entités en double entre les bases annuelles.')

    known_sources = set(source_ids)
    known_announcements = set(announcement_ids)
    known_entities = set(entity_ids)
    if any(str(item.get('source_id')) not in known_sources for item in announcements):
        raise ValueError('Une annonce ne renvoie à aucune source conservée.')
    if any(str(item.get('announcement_id')) not in known_announcements for item in entities):
        raise ValueError('Une entité ne renvoie à aucune annonce conservée.')
    if any(str(item.get('announcement_id')) not in known_announcements
           or str(item.get('from')) not in known_entities
           or str(item.get('to')) not in known_entities for item in relations):
        raise ValueError('Une relation ne renvoie pas à ses annonces ou entités sources.')

    manual_groups_path = root / 'data' / 'identity-merges.json'
    manual_groups = []
    if manual_groups_path.is_file():
        manual_data = json.loads(manual_groups_path.read_text(encoding='utf-8-sig'))
        for group in manual_data.get('groups', []):
            group_id = str(group.get('id') or '')
            member_ids = [str(value) for value in group.get('entity_ids', [])]
            label_key = normaliser_nom_personne(group.get('name'))
            if not group_id or len(member_ids) < 2 or not label_key or len(set(member_ids)) != len(member_ids):
                raise ValueError('Règle de fusion manuelle invalide dans data/identity-merges.json.')
            manual_groups.append(group)

    # Clé de restitution seulement : chaque occurrence et sa référence source
    # restent intactes dans les bases annuelles. Un nom seul n'identifie personne.
    for entity in entities:
        entity.pop('presumed_identity_key', None)
        entity.pop('identity_group_key', None)
        entity.pop('identity_resolution', None)
    identity_summary = synchroniser_identites(root, entities, identity_group_key, manual_groups)

    missing, errors = [], []
    expected = downloaded = 0
    inventories_complete = True
    for year in years:
        coverage = yearly_coverage[str(year)]
        expected += int(coverage.get('expected') or 0)
        downloaded += int(coverage.get('downloaded') or 0)
        inventories_complete = inventories_complete and bool(coverage.get('inventory_complete'))
        missing.extend(dict(year=year, number=number) for number in coverage.get('missing', []))
        errors.extend(dict(year=year, **error) for error in coverage.get('errors', []))

    org_count = sum(item.get('type') in ('societe', 'association') for item in entities)
    person_count = sum(item.get('type') == 'personne' for item in entities)
    stats = dict(
        announcements=len(announcements), entities=len(entities), relations=len(relations),
        pages=pages, residual_fragments=residual_fragments,
        organizations=org_count, persons=person_count, categories=dict(category_counts),
        unique_entities=identity_summary['unique_entities'],
        unique_persons=len({row['canonical_id'] for row in entities if row.get('type') == 'personne'}),
        unique_organizations=len({row['canonical_id'] for row in entities if row.get('type') in ('societe','association','organisation')}),
    )
    data = dict(
        sources=sources,
        announcements=announcements,
        entities=entities,
        relations=relations,
        coverage=dict(
            scope='all_indexed_years', available_years=years, years=yearly_coverage,
            expected=expected, downloaded=downloaded, imported=len(sources),
            inventory_complete=inventories_complete, missing=missing, errors=errors,
        ),
        stats=stats,
        identity_summary=identity_summary,
        annual_revisions=annual_revisions,
    )
    data['revision'] = revision(data)
    try:
        from .restitution_index import build as build_consultation
    except ImportError:
        from restitution_index import build as build_consultation
    output = root / 'dist'
    output.mkdir(parents=True, exist_ok=True)
    marker = output / 'publication.json'
    previous = json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {}
    _write_json(marker, dict(state='writing', revision=previous.get('revision'), pending_revision=data['revision']))
    build_consultation(root, data)
    _write_json(output / 'data.json', data)
    # Petit état initial. Les annonces et les voisinages sont paginés par l'API.
    lite = {key:data[key] for key in ('revision','stats','coverage','identity_summary')}
    _write_text(output / 'data-lite.js', 'window.JORT_DATA = ' + json.dumps(lite, ensure_ascii=False, separators=(',', ':')) + ';\n')
    _write_json(marker, dict(state='ready', revision=data['revision'], years=years,
                            counts=dict(sources=len(sources), announcements=len(announcements), entities=len(entities), relations=len(relations))))
    # Ancienne copie JavaScript intégrale, remplacée par les API paginées.
    legacy=output/'data.js'
    if legacy.exists():
        try: legacy.unlink()
        except PermissionError: pass  # Nettoyage différé si un ancien lecteur la garde ouverte.
    return data
