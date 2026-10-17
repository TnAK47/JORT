"""Migrations SQLite additives pour préparer l'évolution de JORT.

Cette fonction est séparée de la synchronisation actuelle. L'appelant lui
passe une connexion SQLite déjà ouverte; aucune base n'est modifiée à l'import.
"""

SCHEMA_VERSION = 1

_DDL = (
    """CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )""",
    """CREATE TABLE IF NOT EXISTS identity_records (
        id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL CHECK(entity_type IN ('personne','organisation')),
        label TEXT,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','retired')),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )""",
    """CREATE TABLE IF NOT EXISTS identity_occurrences (
        id TEXT PRIMARY KEY,
        identity_id TEXT REFERENCES identity_records(id) ON DELETE SET NULL,
        entity_id TEXT UNIQUE REFERENCES entities(id) ON DELETE SET NULL,
        announcement_id TEXT REFERENCES announcements(id) ON DELETE SET NULL,
        source_id TEXT REFERENCES sources(id) ON DELETE SET NULL,
        name_as_published TEXT,
        cin_value TEXT,
        source_reference TEXT,
        source_number INTEGER,
        source_date TEXT,
        source_pages TEXT,
        source_quote TEXT,
        source_document TEXT,
        extraction_version INTEGER,
        review_state TEXT NOT NULL DEFAULT 'unreviewed'
            CHECK(review_state IN ('unreviewed','reviewed','needs_review')),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )""",
    """CREATE TABLE IF NOT EXISTS identity_candidates (
        id TEXT PRIMARY KEY,
        occurrence_a TEXT NOT NULL REFERENCES identity_occurrences(id) ON DELETE RESTRICT,
        occurrence_b TEXT NOT NULL REFERENCES identity_occurrences(id) ON DELETE RESTRICT,
        method TEXT NOT NULL CHECK(method IN ('exact_cin','phonetic','manual')),
        strategy_version TEXT NOT NULL,
        confidence REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
        evidence_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','confirmed','rejected','needs_review')),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        CHECK(occurrence_a <> occurrence_b),
        UNIQUE(occurrence_a, occurrence_b, method, strategy_version)
    )""",
    """CREATE TABLE IF NOT EXISTS identity_decisions (
        id TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL REFERENCES identity_candidates(id) ON DELETE RESTRICT,
        decision TEXT NOT NULL CHECK(decision IN ('confirm','reject','reopen')),
        actor_user_id TEXT,
        decided_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        rationale TEXT NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '{}'
    )""",
    """CREATE TABLE IF NOT EXISTS app_roles (
        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS app_permissions (
        id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, description TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS role_permissions (
        role_id TEXT NOT NULL REFERENCES app_roles(id) ON DELETE CASCADE,
        permission_id TEXT NOT NULL REFERENCES app_permissions(id) ON DELETE CASCADE,
        PRIMARY KEY(role_id, permission_id)
    )""",
    """CREATE TABLE IF NOT EXISTS app_users (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT,
        status TEXT NOT NULL DEFAULT 'disabled' CHECK(status IN ('disabled','active','locked')),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )""",
    """CREATE TABLE IF NOT EXISTS user_roles (
        user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
        role_id TEXT NOT NULL REFERENCES app_roles(id) ON DELETE RESTRICT,
        granted_by TEXT,
        granted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        PRIMARY KEY(user_id, role_id)
    )""",
    """CREATE TABLE IF NOT EXISTS app_config (
        key TEXT PRIMARY KEY, value_json TEXT NOT NULL, changed_by TEXT,
        changed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )""",
    """CREATE TABLE IF NOT EXISTS auth_sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL, last_seen_at TEXT,
        revoked_at TEXT, revoke_reason TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS audit_events (
        id TEXT PRIMARY KEY, actor_user_id TEXT, action TEXT NOT NULL,
        object_type TEXT, object_id TEXT,
        occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        details_json TEXT NOT NULL DEFAULT '{}', request_id TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS publication_state (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        visibility TEXT NOT NULL DEFAULT 'private' CHECK(visibility IN ('private','preview','public')),
        changed_by TEXT,
        changed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        reason TEXT NOT NULL DEFAULT 'default private state'
    )""",
    """CREATE TABLE IF NOT EXISTS public_records (
        id TEXT PRIMARY KEY, record_type TEXT NOT NULL,
        source_record_id TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        source_revision TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','approved','withdrawn')),
        reviewed_by TEXT, reviewed_at TEXT,
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        CHECK(status <> 'approved' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
        UNIQUE(record_type, source_record_id)
    )""",
    """CREATE TABLE IF NOT EXISTS field_access_policies (
        resource_type TEXT NOT NULL, field_name TEXT NOT NULL,
        public_allowed INTEGER NOT NULL DEFAULT 0 CHECK(public_allowed IN (0,1)),
        required_permission TEXT,
        PRIMARY KEY(resource_type, field_name)
    )""",
    """CREATE TABLE IF NOT EXISTS processing_jobs (
        id TEXT PRIMARY KEY, job_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','paused','completed','failed','cancelled')),
        parameters_json TEXT NOT NULL DEFAULT '{}', cursor_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        started_at TEXT, completed_at TEXT, error TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS journal_checkpoints (
        id TEXT PRIMARY KEY,
        job_id TEXT REFERENCES processing_jobs(id) ON DELETE SET NULL,
        source_id TEXT REFERENCES sources(id) ON DELETE SET NULL,
        journal_number INTEGER, journal_date TEXT, document_sha256 TEXT,
        extractor_version INTEGER,
        state TEXT NOT NULL CHECK(state IN ('started','completed','failed','incomplete')),
        cursor_json TEXT NOT NULL DEFAULT '{}',
        saved_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        UNIQUE(journal_number, document_sha256, extractor_version)
    )""",
    "CREATE INDEX IF NOT EXISTS identity_occurrence_identity ON identity_occurrences(identity_id, id)",
    "CREATE INDEX IF NOT EXISTS identity_occurrence_cin ON identity_occurrences(cin_value)",
    "CREATE INDEX IF NOT EXISTS identity_occurrence_name ON identity_occurrences(name_as_published COLLATE NOCASE, id)",
    "CREATE INDEX IF NOT EXISTS identity_occurrence_source ON identity_occurrences(source_id, announcement_id)",
    "CREATE INDEX IF NOT EXISTS identity_candidate_status ON identity_candidates(status, created_at, id)",
    "CREATE INDEX IF NOT EXISTS identity_decision_candidate ON identity_decisions(candidate_id, decided_at)",
    "CREATE INDEX IF NOT EXISTS auth_session_user_expiry ON auth_sessions(user_id, expires_at, revoked_at)",
    "CREATE INDEX IF NOT EXISTS audit_event_time ON audit_events(occurred_at, id)",
    "CREATE INDEX IF NOT EXISTS processing_job_status_time ON processing_jobs(status, created_at, id)",
    "CREATE INDEX IF NOT EXISTS journal_checkpoint_lookup ON journal_checkpoints(journal_number, document_sha256, extractor_version)",
    "CREATE INDEX IF NOT EXISTS public_record_status_type ON public_records(status,record_type,id)",
)


def appliquer_schema(db):
    """Ajoute les structures futures à une connexion SQLite existante.

    À appeler avant la transaction de synchronisation principale. Cette petite
    migration est validée séparément afin que l'appelant puisse ensuite ouvrir
    sa transaction avec BEGIN IMMEDIATE. Rien ne s'exécute à l'import.
    """
    existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {'sources', 'announcements', 'entities', 'relations'}
    missing = required - existing
    if missing:
        raise ValueError('Schéma JORT incomplet; tables absentes: ' + ', '.join(sorted(missing)))
    db.execute('SAVEPOINT schema_evolution')
    try:
        for statement in _DDL:
            db.execute(statement)
        db.execute(
            'INSERT OR IGNORE INTO publication_state(id, visibility, reason) VALUES(1, ?, ?)',
            ('private', 'default private state'))
        db.execute('INSERT OR IGNORE INTO schema_migrations(version) VALUES(?)', (SCHEMA_VERSION,))
        db.executemany('''INSERT OR IGNORE INTO field_access_policies
            (resource_type,field_name,public_allowed,required_permission) VALUES(?,?,?,?)''', (
            ('personne','cin',0,'identities.sensitive.read'),
            ('identity_occurrences','cin_value',0,'identities.sensitive.read'),
            ('app_users','password_hash',0,'auth.internal'),
            ('auth_sessions','token_hash',0,'auth.internal'),
        ))
        # Catalogue générique des droits: aucune attribution à un compte n'est créée.
        roles = (
            ('role-admin', 'admin', 'Administration, comptes et configuration'),
            ('role-collecte', 'collecte', 'Exécution et suivi de la collecte'),
            ('role-consultation', 'consultation', 'Consultation des données et des sources'),
        )
        permissions = (
            ('perm-users-manage', 'users.manage', 'Gérer les comptes et leurs rôles'),
            ('perm-config-manage', 'config.manage', 'Modifier la configuration'),
            ('perm-collect-manage', 'collection.manage', 'Démarrer et suivre la collecte'),
            ('perm-data-read', 'data.read', 'Consulter les données extraites'),
            ('perm-source-read', 'sources.read', 'Consulter les documents et annonces source'),
            ('perm-identity-review', 'identities.review', 'Examiner les candidats d’identité'),
            ('perm-identity-sensitive', 'identities.sensitive.read', 'Accès réservé aux identifiants personnels'),
            ('perm-publication-manage', 'publication.manage', 'Modifier explicitement la visibilité'),
        )
        db.executemany('INSERT OR IGNORE INTO app_roles(id,name,description) VALUES(?,?,?)', roles)
        db.executemany('INSERT OR IGNORE INTO app_permissions(id,code,description) VALUES(?,?,?)', permissions)
        grants = {
            'admin': ('users.manage', 'config.manage', 'collection.manage', 'data.read',
                      'sources.read', 'identities.review', 'identities.sensitive.read', 'publication.manage'),
            'collecte': ('collection.manage', 'data.read', 'sources.read'),
            'consultation': ('data.read', 'sources.read'),
        }
        for role_name, permission_codes in grants.items():
            for code in permission_codes:
                db.execute('''INSERT OR IGNORE INTO role_permissions(role_id,permission_id)
                    SELECT r.id,p.id FROM app_roles r, app_permissions p
                    WHERE r.name=? AND p.code=?''', (role_name, code))
        db.execute('RELEASE SAVEPOINT schema_evolution')
        return SCHEMA_VERSION
    except Exception:
        db.execute('ROLLBACK TO SAVEPOINT schema_evolution')
        db.execute('RELEASE SAVEPOINT schema_evolution')
        raise


# Alias conservé pour les intégrateurs qui ont déjà adopté le nom initial.
appliquer_migrations = appliquer_schema
