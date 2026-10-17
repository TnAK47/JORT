"""Identifiants permanents et fusions réversibles, communs à toutes les années.

Une occurrence reste dans sa base annuelle. Ce référentiel conserve uniquement
son rattachement, les noms publiés et l'historique des décisions.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

try:
    from .rapprochement_identites import normaliser_nom_personne
except ImportError:
    from rapprochement_identites import normaliser_nom_personne


def _now():
    return datetime.now(timezone.utc).isoformat()


def registry_path(root):
    return Path(root) / 'data/referentiel-entites.sqlite'


@contextmanager
def _database(root, readonly=False):
    path = registry_path(root)
    if readonly:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        if not readonly:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS canonical_entities (
                    id TEXT PRIMARY KEY, type TEXT NOT NULL, label TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL,
                    label_locked INTEGER NOT NULL DEFAULT 0, public_id TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS entity_occurrences (
                    id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL REFERENCES canonical_entities(id),
                    announcement_id TEXT NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL,
                    cin TEXT, identifier TEXT, identifier_type TEXT, resolution TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1);
                CREATE INDEX IF NOT EXISTS occurrence_canonical ON entity_occurrences(canonical_id,active);
                CREATE INDEX IF NOT EXISTS occurrence_announcement ON entity_occurrences(announcement_id);
                CREATE TABLE IF NOT EXISTS identity_keys (
                    key TEXT PRIMARY KEY, canonical_id TEXT NOT NULL REFERENCES canonical_entities(id));
                CREATE TABLE IF NOT EXISTS entity_redirects (
                    old_id TEXT PRIMARY KEY REFERENCES canonical_entities(id),
                    new_id TEXT NOT NULL REFERENCES canonical_entities(id), event_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS merge_events (
                    id TEXT PRIMARY KEY, target_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    reason TEXT NOT NULL, method TEXT NOT NULL, snapshot_json TEXT NOT NULL,
                    undone_at TEXT);
                CREATE TABLE IF NOT EXISTS legacy_imports (id TEXT PRIMARY KEY, event_id TEXT);
                CREATE TABLE IF NOT EXISTS identity_exclusions (key TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS identity_split_events (
                    id TEXT PRIMARY KEY, old_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    reason TEXT NOT NULL, snapshot_json TEXT NOT NULL);
            ''')
            if 'label_locked' not in {row[1] for row in db.execute('PRAGMA table_info(canonical_entities)')}:
                db.execute('ALTER TABLE canonical_entities ADD COLUMN label_locked INTEGER NOT NULL DEFAULT 0')
            if 'public_id' not in {row[1] for row in db.execute('PRAGMA table_info(canonical_entities)')}:
                db.execute('ALTER TABLE canonical_entities ADD COLUMN public_id TEXT')
            db.execute('''CREATE TABLE IF NOT EXISTS entity_sequence (
                name TEXT PRIMARY KEY, next_value INTEGER NOT NULL CHECK(next_value > 0))''')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS canonical_public_id ON canonical_entities(public_id)')
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT OR IGNORE INTO entity_sequence(name,next_value) VALUES('public_id',1)")
            # Preserve UUIDs as technical keys and add a readable permanent ID.
            missing_ids = db.execute('SELECT id FROM canonical_entities WHERE public_id IS NULL ORDER BY created_at,id').fetchall()
            for row in missing_ids:
                value = db.execute("SELECT next_value FROM entity_sequence WHERE name='public_id'").fetchone()[0]
                db.execute('UPDATE canonical_entities SET public_id=? WHERE id=?', (f'JORT-E-{value:08d}', row['id']))
                db.execute("UPDATE entity_sequence SET next_value=? WHERE name='public_id'", (value + 1,))
            _split_generic_funds(db)
        yield db
        if not readonly:
            db.commit()
    except Exception:
        if not readonly:
            db.rollback()
        raise
    finally:
        db.close()


def _resolve(db, identifier):
    visited = set()
    while True:
        if identifier in visited:
            raise ValueError('Cycle dans les redirections d’entités.')
        visited.add(identifier)
        row = db.execute('SELECT new_id FROM entity_redirects WHERE old_id=?', (identifier,)).fetchone()
        if row is None:
            return identifier
        identifier = row['new_id']


def _split_generic_funds(db):
    """Annule les anciens regroupements automatiques de fonds génériques."""
    rows = db.execute('''SELECT c.id AS canonical_id,o.id,o.announcement_id,o.name,o.resolution
        FROM canonical_entities c JOIN entity_occurrences o ON o.canonical_id=c.id
        WHERE c.type='fonds_commerce' AND c.status='active' AND o.active=1
        ORDER BY c.created_at,c.id,o.announcement_id,o.id''').fetchall()
    groups = {}
    for row in rows:
        groups.setdefault(row['canonical_id'], []).append(dict(row))
    for canonical_id, occurrences in groups.items():
        if len(occurrences) < 2 or any(row['resolution'] != 'same_name_presumed' for row in occurrences):
            continue
        event_id = 'separation-' + uuid.uuid4().hex
        moved = []
        for occurrence in occurrences[1:]:
            value = db.execute("SELECT next_value FROM entity_sequence WHERE name='public_id'").fetchone()[0]
            public_id = f'JORT-E-{value:08d}'
            db.execute("UPDATE entity_sequence SET next_value=? WHERE name='public_id'", (value + 1,))
            new_id = 'ent-' + uuid.uuid4().hex
            db.execute('''INSERT INTO canonical_entities(id,type,label,status,created_at,label_locked,public_id)
                          VALUES(?, 'fonds_commerce', ?, 'active', ?, 0, ?)''',
                       (new_id, occurrence['name'], _now(), public_id))
            db.execute("UPDATE entity_occurrences SET canonical_id=?,resolution='source_occurrence' WHERE id=?",
                       (new_id, occurrence['id']))
            prefix = f"occurrence:{occurrence['announcement_id']}:fonds_commerce:"
            db.execute('UPDATE identity_keys SET canonical_id=? WHERE canonical_id=? AND substr(key,1,?)=?',
                       (new_id, canonical_id, len(prefix), prefix))
            moved.append(dict(occurrence_id=occurrence['id'], canonical_id=new_id,
                              announcement_id=occurrence['announcement_id'], name=occurrence['name']))
        if moved:
            db.execute('INSERT INTO identity_split_events VALUES(?,?,?,?,?)',
                       (event_id, canonical_id, _now(), 'Séparation des fonds regroupés par un libellé générique',
                        json.dumps(dict(moved=moved), ensure_ascii=False)))


def _family(kind):
    return 'organisation' if kind in ('societe', 'association', 'organisation') else kind


def _official(value):
    return ''.join(c for c in str(value or '').upper() if c.isalnum())


def _merge(db, identifiers, label, reason, method='manual'):
    identifiers = list(dict.fromkeys(_resolve(db, value) for value in identifiers))
    if len(identifiers) < 2:
        return None
    rows = [db.execute('SELECT * FROM canonical_entities WHERE id=?', (value,)).fetchone() for value in identifiers]
    if any(row is None for row in rows) or len({_family(row['type']) for row in rows}) != 1:
        raise ValueError('Fusion impossible : entité absente ou types incompatibles.')
    placeholders = ','.join('?' for _ in identifiers)
    occurrences = [dict(row) for row in db.execute(f'SELECT * FROM entity_occurrences WHERE canonical_id IN ({placeholders})', identifiers)]
    cins = {row['cin'] for row in occurrences if row['active'] and row['type'] == 'personne' and row['cin'] and len(row['cin']) == 8}
    if len(cins) > 1:
        raise ValueError('CIN contradictoires : corrigez leur attribution avant la fusion.')
    official = {}
    for row in occurrences:
        if row['active'] and row['identifier'] and row['identifier_type']:
            official.setdefault(row['identifier_type'], set()).add(_official(row['identifier']))
    if any(len(values) > 1 for values in official.values()):
        raise ValueError('Identifiants officiels contradictoires : fusion à vérifier.')
    target = identifiers[0]
    snapshot = dict(entities=[dict(row) for row in rows], occurrences=occurrences,
                    keys=[dict(row) for row in db.execute(f'SELECT * FROM identity_keys WHERE canonical_id IN ({placeholders})', identifiers)],
                    source_ids=identifiers)
    event_id = 'fusion-' + uuid.uuid4().hex
    db.execute('INSERT INTO merge_events VALUES(?,?,?,?,?,?,NULL)',
               (event_id, target, _now(), reason, method, json.dumps(snapshot, ensure_ascii=False)))
    for old_id in identifiers[1:]:
        db.execute('UPDATE entity_occurrences SET canonical_id=?,resolution=? WHERE canonical_id=?', (target, 'human_confirmed' if method == 'manual' else method, old_id))
        db.execute('UPDATE identity_keys SET canonical_id=? WHERE canonical_id=?', (target, old_id))
        db.execute('INSERT INTO entity_redirects VALUES(?,?,?)', (old_id, target, event_id))
        db.execute("UPDATE canonical_entities SET status='retired' WHERE id=?", (old_id,))
    db.execute('UPDATE canonical_entities SET label=?,label_locked=? WHERE id=?', (label or rows[0]['label'], int(method == 'manual' or rows[0]['label_locked']), target))
    if method == 'manual':
        db.execute("UPDATE entity_occurrences SET resolution='human_confirmed' WHERE canonical_id=?", (target,))
    return event_id


def fusionner(root, identifiers, label, reason):
    if len(set(identifiers)) < 2 or not reason.strip():
        raise ValueError('Choisissez au moins deux fiches et indiquez le motif vérifié.')
    with _database(root) as db:
        event_id = _merge(db, identifiers, label.strip(), reason.strip())
        target = _resolve(db, identifiers[0])
        occurrences = db.execute('SELECT count(*) FROM entity_occurrences WHERE canonical_id=? AND active=1', (target,)).fetchone()[0]
    return dict(event_id=event_id, canonical_id=target, occurrences_grouped=occurrences, evidence_preserved=True)


def annuler_fusion(root, event_id):
    with _database(root) as db:
        event = db.execute('SELECT * FROM merge_events WHERE id=? AND undone_at IS NULL', (event_id,)).fetchone()
        if event is None:
            raise ValueError('Fusion absente ou déjà annulée.')
        snapshot = json.loads(event['snapshot_json'])
        members = set(snapshot['source_ids'])
        prior_occurrences = {row['id'] for row in snapshot['occurrences']}
        later_occurrences = [row['id'] for row in db.execute('SELECT id FROM entity_occurrences WHERE canonical_id=? AND active=1', (event['target_id'],)) if row['id'] not in prior_occurrences]
        for later in db.execute('SELECT snapshot_json FROM merge_events WHERE created_at>? AND undone_at IS NULL', (event['created_at'],)):
            if members.intersection(json.loads(later['snapshot_json'])['source_ids']):
                raise ValueError('Annulez d’abord la fusion ultérieure portant sur ces fiches.')
        for row in snapshot['entities']:
            db.execute('UPDATE canonical_entities SET label=?,status=?,label_locked=? WHERE id=?', (row['label'], row['status'], row.get('label_locked',0), row['id']))
        db.execute('DELETE FROM entity_redirects WHERE event_id=?', (event_id,))
        for row in snapshot['occurrences']:
            db.execute('UPDATE entity_occurrences SET canonical_id=?,resolution=? WHERE id=?', (row['canonical_id'], row['resolution'], row['id']))
        for row in snapshot['keys']:
            db.execute('UPDATE identity_keys SET canonical_id=? WHERE key=?', (row['canonical_id'], row['key']))
            # Une reconstruction ne doit pas annuler la séparation décidée.
            db.execute('INSERT OR IGNORE INTO identity_exclusions VALUES(?)', (row['key'],))
        for identifier in later_occurrences:
            db.execute("UPDATE entity_occurrences SET resolution='review_after_split' WHERE id=?", (identifier,))
        db.execute('UPDATE merge_events SET undone_at=? WHERE id=?', (_now(), event_id))
    return dict(event_id=event_id, evidence_preserved=True, later_occurrences_to_review=len(later_occurrences))


def synchroniser(root, entities, identity_key, manual_groups=()):
    """Attribue des UUID durables ; n'effectue aucun rapprochement phonétique."""
    by_id = {str(row['id']): row for row in entities}
    name_groups = {}
    for row in entities:
        # Sold assets are announcement-specific; a generic label must never
        # merge unrelated businesses based on the words « Fonds de commerce ».
        if row.get('type') == 'fonds_commerce':
            continue
        family = _family(row.get('type'))
        name_key = normaliser_nom_personne(row.get('name'))
        if name_key:
            name_groups.setdefault((family, name_key), []).append(row)
    name_conflicts = set()
    for group_key, peers in name_groups.items():
        family = group_key[0]
        if len(peers) < 2:
            continue
        if family == 'personne':
            identifiers = {str(peer.get('cin')) for peer in peers
                           if peer.get('cin') and len(str(peer.get('cin'))) == 8}
        else:
            identifiers_by_type = {}
            for peer in peers:
                if peer.get('identifier') and peer.get('identifier_type'):
                    identifiers_by_type.setdefault(peer['identifier_type'], set()).add(_official(peer['identifier']))
            identifiers = {kind: values for kind, values in identifiers_by_type.items() if len(values) > 1}
        if (len(identifiers) > 1 if family == 'personne' else bool(identifiers)):
            name_conflicts.add(group_key)
    with _database(root) as db:
        current = {row['id']: dict(row) for row in db.execute('SELECT * FROM entity_occurrences')}
        keys = {row['key']: _resolve(db, row['canonical_id']) for row in db.execute('SELECT * FROM identity_keys')}
        exclusions = {row['key'] for row in db.execute('SELECT key FROM identity_exclusions')}
        # Une annulation humaine reste prioritaire. Les refus automatiques
        # peuvent être retentés après une correction de format d'identifiant.
        protected_keys=set()
        for event in db.execute('SELECT snapshot_json FROM merge_events WHERE undone_at IS NOT NULL'):
            protected_keys.update(k['key'] for k in json.loads(event[0]).get('keys',[]))
        for key in exclusions-protected_keys:
            db.execute('DELETE FROM identity_exclusions WHERE key=?',(key,))
        exclusions &= protected_keys
        warnings = []
        db.execute('CREATE TEMP TABLE sync_occurrence_ids(id TEXT PRIMARY KEY)')
        db.executemany('INSERT INTO sync_occurrence_ids VALUES(?)',[(str(row['id']),) for row in entities])
        db.execute('UPDATE entity_occurrences SET active=0 WHERE id NOT IN (SELECT id FROM sync_occurrence_ids)')
        for row in entities:
            occurrence_id = str(row['id'])
            kind = row.get('type', 'organisation')
            name = str(row.get('name') or 'Entité sans nom')
            name_key = normaliser_nom_personne(name)
            key = identity_key(row)
            basis = ('cin_and_exact_name' if key and key.startswith('personne:') else 'official_identifier') if key else 'source_occurrence'
            family = _family(kind)
            peers = name_groups.get((family, name_key), [])
            # Même nom complet normalisé : les personnes sont regroupées comme
            # présumées identiques, sauf CIN contradictoires. La preuve et les
            # variantes restent attachées à chaque annonce pour vérification.
            # Les identifiants officiels contradictoires bloquent aussi les
            # rapprochements d'organisations et des autres types d'entités.
            if len(peers) > 1 and (family, name_key) in name_conflicts:
                basis = 'identity_conflict'
                if len(warnings) < 100:
                    warnings.append(f'Nom répété avec identifiants contradictoires, rapprochement laissé séparé : {name}')
            elif len(peers) > 1:
                if family == 'personne':
                    key = 'personne:nom-exact-presume:' + name_key
                    basis = 'same_name_presumed'
                elif family == 'organisation':
                    identified = next((identity_key(peer) for peer in peers if identity_key(peer)), None)
                    key = identified or 'organisation:nom-presume:' + name_key
                    basis = 'organization_name_presumed'
                else:
                    key = f'{family}:nom-exact-presume:{name_key}'
                    basis = 'same_name_presumed'
            # Within one notice, repeated extraction of the same named subject
            # is reunited; published occurrences and their proofs remain.
            local_key = f"occurrence:{row.get('announcement_id')}:{kind}:{name_key}:{row.get('cin') or ''}"
            proposed_key = key or local_key
            old = current.get(occurrence_id)
            if old:
                canonical_id = _resolve(db, old['canonical_id'])
            else:
                canonical_id = _resolve(db, keys[proposed_key]) if proposed_key in keys and proposed_key not in exclusions else None
                if canonical_id is None:
                    canonical_id = 'ent-' + uuid.uuid4().hex
                    sequence = db.execute("SELECT next_value FROM entity_sequence WHERE name='public_id'").fetchone()[0]
                    public_id = f'JORT-E-{sequence:08d}'
                    db.execute("UPDATE entity_sequence SET next_value=? WHERE name='public_id'", (sequence + 1,))
                    db.execute('INSERT INTO canonical_entities(id,type,label,status,created_at,public_id) VALUES(?,?,?,\'active\',?,?)', (canonical_id, kind, name, _now(), public_id))
            target = _resolve(db, keys[proposed_key]) if proposed_key in keys else None
            if target and proposed_key not in exclusions and target != canonical_id:
                # Keys require corroboration for persons; names alone are
                # candidates across notices, never confirmed automatically.
                try:
                    event = _merge(db, [target, canonical_id], None, 'Rattachement par clé documentée', basis)
                    if event:
                        canonical_id = _resolve(db, canonical_id)
                except ValueError:
                    exclusions.add(proposed_key)
                    db.execute('INSERT OR IGNORE INTO identity_exclusions VALUES(?)', (proposed_key,))
                    basis = 'identity_conflict'
                    if len(warnings) < 100:
                        warnings.append('Rapprochement refusé pour identifiants contradictoires : ' + occurrence_id)
            if old and old['resolution'] in ('human_confirmed', 'review_after_split'):
                basis = old['resolution']
            if proposed_key not in exclusions:
                keys[proposed_key] = canonical_id
                db.execute('INSERT INTO identity_keys VALUES(?,?) ON CONFLICT(key) DO UPDATE SET canonical_id=excluded.canonical_id', (proposed_key, canonical_id))
            db.execute('''INSERT INTO entity_occurrences VALUES(?,?,?,?,?,?,?,?,?,1)
                ON CONFLICT(id) DO UPDATE SET canonical_id=excluded.canonical_id,announcement_id=excluded.announcement_id,
                name=excluded.name,type=excluded.type,cin=excluded.cin,identifier=excluded.identifier,
                identifier_type=excluded.identifier_type,resolution=excluded.resolution,active=1''',
                (occurrence_id, canonical_id, row['announcement_id'], name, kind, row.get('cin'),
                 row.get('identifier'), row.get('identifier_type'), basis))
            # A corrected primary title replaces the previous derived label.
            if row.get('is_primary') or (old and old['name'] != name):
                db.execute('UPDATE canonical_entities SET label=? WHERE id=? AND label_locked=0', (name, canonical_id))
        for group in manual_groups:
            if db.execute('SELECT 1 FROM legacy_imports WHERE id=?', (group['id'],)).fetchone():
                continue
            members = [value for value in group.get('entity_ids', []) if str(value) in by_id]
            if len(members) != len(group.get('entity_ids', [])) or len(members) < 2:
                warnings.append('Fusion historique à revoir : ' + str(group['id']))
                continue
            canonical_ids = [_resolve(db, db.execute('SELECT canonical_id FROM entity_occurrences WHERE id=?', (str(value),)).fetchone()[0]) for value in members]
            try:
                event_id = _merge(db, canonical_ids, group.get('name'), group.get('reason') or 'Confirmation historique de l’administrateur')
                db.execute('INSERT INTO legacy_imports VALUES(?,?)', (group['id'], event_id))
            except ValueError:
                warnings.append('Fusion historique refusée pour identifiants contradictoires : ' + str(group['id']))
        labels = {row['id']: row['label'] for row in db.execute("SELECT id,label FROM canonical_entities WHERE status='active'")}
        public_ids = {row['id']: row['public_id'] for row in db.execute("SELECT id,public_id FROM canonical_entities WHERE status='active'")}
        mappings = {row['id']: dict(row) for row in db.execute('SELECT id,canonical_id,resolution FROM entity_occurrences WHERE active=1')}
        for row in entities:
            mapping = mappings[str(row['id'])]
            row.update(canonical_id=mapping['canonical_id'], identity_group_key=mapping['canonical_id'],
                       canonical_name=labels[mapping['canonical_id']], public_entity_id=public_ids[mapping['canonical_id']],
                       identity_resolution=mapping['resolution'])
        return dict(unique_entities=len({row['canonical_id'] for row in mappings.values()}), warnings=warnings)


def consulter(root, query='', limit=80):
    """Vue admin bornée : fiches, variantes, occurrences et fusions annulables."""
    if not registry_path(root).exists():
        return dict(entities=[], merges=[], ready=False)
    with _database(root, readonly=True) as db:
        query = normaliser_nom_personne(query)
        parameters=[]
        condition="c.status='active'"
        if query:
            try:
                from restitution_index import Store
                matches=Store(root).nodes(query,limit=limit)['items']
                identifiers=list(dict.fromkeys(_resolve(db,item['id']) for item in matches))
                if not identifiers: return dict(entities=[],merges=[],ready=True)
                condition+=' AND c.id IN ('+','.join('?' for _ in identifiers)+')'
                parameters=identifiers
            except (OSError,sqlite3.Error):
                db.create_function('normalized_name',1,normaliser_nom_personne)
                condition+=' AND instr(normalized_name(c.label),?)>0'
                parameters=[query]
        rows = db.execute('''SELECT c.id,c.public_id,c.type,c.label,count(o.id) AS occurrences
            FROM canonical_entities c JOIN entity_occurrences o ON o.canonical_id=c.id AND o.active=1
            WHERE '''+condition+' GROUP BY c.id ORDER BY c.label LIMIT ?',parameters+[limit])
        results = []
        for row in rows:
            item = dict(row)
            occurrences = [dict(value) for value in db.execute('SELECT id,name,announcement_id,cin,identifier,identifier_type,resolution FROM entity_occurrences WHERE canonical_id=? AND active=1 LIMIT 30', (row['id'],))]
            item.update(evidence=occurrences, names=sorted({value['name'] for value in occurrences}))
            results.append(item)
            if len(results) >= limit:
                break
        merges = [dict(row) for row in db.execute("SELECT id,target_id,created_at,reason,method FROM merge_events WHERE undone_at IS NULL AND method='manual' ORDER BY created_at DESC LIMIT 30")]
    return dict(entities=results, merges=merges, ready=True)


def previsualiser(data, identifiers):
    """Réunit les connexions pour l'aperçu sans écrire dans la base."""
    selected = set(identifiers)
    entities = {str(row['id']): row for row in data.get('entities', [])}
    neighbors = {}
    proofs, announcements = set(), set()
    for relation in data.get('relations', []):
        if relation.get('status') == 'a_corriger':
            continue
        a, b = entities.get(str(relation.get('from'))), entities.get(str(relation.get('to')))
        if not a or not b:
            continue
        from_id, to_id = a.get('canonical_id'), b.get('canonical_id')
        if from_id not in selected and to_id not in selected:
            continue
        proofs.add(relation['id'])
        announcements.add(relation['announcement_id'])
        other = b if from_id in selected else a
        other_id = other.get('canonical_id')
        if other_id in selected:
            continue
        bucket = neighbors.setdefault(other_id, dict(id=other_id, name=other.get('canonical_name') or other['name'], roles=set()))
        bucket['roles'].add(relation.get('role') or 'Lien publié')
    return dict(relations=len(proofs), announcements=len(announcements), neighbors_count=len(neighbors),
                neighbors=[dict(row, roles=sorted(row['roles'])) for row in list(neighbors.values())[:50]])


def verifier(root, expected_occurrences):
    if not registry_path(root).exists():
        return ['Référentiel absent : lancer la correction des titres et des liens.']
    issues = []
    with _database(root, readonly=True) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            issues.append('Intégrité du référentiel à réparer.')
        active = {row[0] for row in db.execute('SELECT id FROM entity_occurrences WHERE active=1')}
        missing = set(expected_occurrences) - active
        if missing:
            issues.append(f'{len(missing)} occurrences sans rattachement global : reconstruire la restitution.')
        for row in db.execute('SELECT old_id FROM entity_redirects'):
            _resolve(db, row['old_id'])
        conflicts = db.execute("SELECT count(*) FROM (SELECT canonical_id FROM entity_occurrences WHERE active=1 AND type='personne' AND length(cin)=8 GROUP BY canonical_id HAVING count(DISTINCT cin)>1)").fetchone()[0]
        if conflicts:
            issues.append(f'{conflicts} fiches contiennent des CIN contradictoires ; rapprochements à revoir.')
    return issues
