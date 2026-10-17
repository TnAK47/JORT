CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE TABLE IF NOT EXISTS jort_meta(key text PRIMARY KEY, value jsonb NOT NULL);
CREATE SEQUENCE IF NOT EXISTS jort_public_id_seq;
CREATE TABLE IF NOT EXISTS jort_documents(
 id text PRIMARY KEY, year smallint NOT NULL CHECK(year>=1956),number integer NOT NULL,
 publication_date date,sha256 text,language text NOT NULL DEFAULT 'fr',pages integer,
 payload jsonb NOT NULL,source_revision text,UNIQUE(year,number,language,id));
CREATE INDEX IF NOT EXISTS jort_doc_year ON jort_documents(year,number);
CREATE TABLE IF NOT EXISTS jort_document_locations(
 document_id text REFERENCES jort_documents(id),location text NOT NULL,
 storage text NOT NULL CHECK(storage IN ('local','drive')),verified_at timestamptz,
 PRIMARY KEY(document_id,location));
CREATE TABLE IF NOT EXISTS jort_announcements(
 id text PRIMARY KEY,document_id text NOT NULL REFERENCES jort_documents(id),reference text,
 publication_date date,category text NOT NULL,publication_type text NOT NULL,
 original_text text NOT NULL,title text NOT NULL,normalized_title text NOT NULL,
 search_vector tsvector NOT NULL,search_visible boolean NOT NULL DEFAULT true,
 extraction_quality text NOT NULL,extraction_version integer NOT NULL,
 original_sha256 text NOT NULL,payload jsonb NOT NULL,legacy_payload jsonb,
 UNIQUE(document_id,id));
CREATE INDEX IF NOT EXISTS jort_notice_date ON jort_announcements(publication_date DESC,id DESC);
CREATE INDEX IF NOT EXISTS jort_notice_doc ON jort_announcements(document_id);
CREATE INDEX IF NOT EXISTS jort_notice_reference ON jort_announcements(reference);
CREATE INDEX IF NOT EXISTS jort_notice_search ON jort_announcements USING gin(search_vector);
CREATE INDEX IF NOT EXISTS jort_notice_type_date ON jort_announcements(publication_type,publication_date DESC);
CREATE TABLE IF NOT EXISTS jort_entities(
 id text PRIMARY KEY,public_id text NOT NULL UNIQUE,kind text NOT NULL,
 nature text NOT NULL CHECK(nature IN ('personne_physique','personne_morale','organisation_a_qualifier','actif_commercial')),
 label text NOT NULL,normalized_name text NOT NULL,status text NOT NULL DEFAULT 'active',
 identity_status text NOT NULL DEFAULT 'source_occurrence',label_locked boolean NOT NULL DEFAULT false,
 displayable boolean NOT NULL DEFAULT true,created_at timestamptz NOT NULL DEFAULT now(),
 redirect_to text REFERENCES jort_entities(id));
CREATE INDEX IF NOT EXISTS jort_entity_name ON jort_entities(normalized_name,kind);
CREATE INDEX IF NOT EXISTS jort_entity_search ON jort_entities USING gin(normalized_name gin_trgm_ops);
CREATE TABLE IF NOT EXISTS jort_entity_metrics(entity_id text PRIMARY KEY REFERENCES jort_entities(id),occurrences bigint NOT NULL,relations bigint NOT NULL);
CREATE TABLE IF NOT EXISTS jort_legacy_records(record_type text NOT NULL,id text NOT NULL,payload jsonb NOT NULL,PRIMARY KEY(record_type,id));
CREATE TABLE IF NOT EXISTS jort_persons(entity_id text PRIMARY KEY REFERENCES jort_entities(id));
CREATE TABLE IF NOT EXISTS jort_organizations(entity_id text PRIMARY KEY REFERENCES jort_entities(id),subtype text NOT NULL);
CREATE TABLE IF NOT EXISTS jort_assets(
 entity_id text PRIMARY KEY REFERENCES jort_entities(id),activity text,address text,trade_name text,
 description text NOT NULL,description_quote text,identity_status text NOT NULL DEFAULT 'source_object');
CREATE TABLE IF NOT EXISTS jort_mentions(
 id text PRIMARY KEY,entity_id text NOT NULL REFERENCES jort_entities(id),
 announcement_id text NOT NULL REFERENCES jort_announcements(id),name_as_published text NOT NULL,
 kind text NOT NULL,resolution text NOT NULL,payload jsonb NOT NULL,active boolean NOT NULL DEFAULT true,
 extraction_version integer NOT NULL);
CREATE INDEX IF NOT EXISTS jort_mention_entity ON jort_mentions(entity_id,active,announcement_id);
CREATE INDEX IF NOT EXISTS jort_mention_notice ON jort_mentions(announcement_id,active);
CREATE TABLE IF NOT EXISTS jort_names(
 entity_id text REFERENCES jort_entities(id),normalized_name text NOT NULL,label text NOT NULL,
 language text NOT NULL DEFAULT 'fr',mention_id text REFERENCES jort_mentions(id),
 PRIMARY KEY(entity_id,normalized_name,mention_id));
CREATE INDEX IF NOT EXISTS jort_names_search ON jort_names(normalized_name);
CREATE INDEX IF NOT EXISTS jort_names_search_trgm ON jort_names USING gin(normalized_name gin_trgm_ops);
CREATE TABLE IF NOT EXISTS jort_entity_name_history(
 id text PRIMARY KEY,
 entity_id text NOT NULL REFERENCES jort_entities(id),
 name text NOT NULL,
 normalized_name text NOT NULL,
 name_status text NOT NULL CHECK(name_status IN ('current','former','published')),
 valid_from date,
 valid_to date,
 announcement_id text REFERENCES jort_announcements(id),
 quote text,
 method text NOT NULL DEFAULT 'source_explicit',
 observed_on date,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(entity_id,normalized_name,name_status,announcement_id));
CREATE INDEX IF NOT EXISTS jort_entity_name_history_search ON jort_entity_name_history(normalized_name,entity_id);
CREATE TABLE IF NOT EXISTS jort_identifier_observations(
 id text PRIMARY KEY,entity_id text NOT NULL REFERENCES jort_entities(id),mention_id text NOT NULL REFERENCES jort_mentions(id),
 kind text NOT NULL,raw_value text NOT NULL,normalized_value text NOT NULL,jurisdiction text NOT NULL DEFAULT 'TN',
 attribution text NOT NULL,quote text,active boolean NOT NULL DEFAULT true);
CREATE INDEX IF NOT EXISTS jort_ident_lookup ON jort_identifier_observations(kind,normalized_value,jurisdiction) WHERE active;
CREATE INDEX IF NOT EXISTS jort_ident_entity ON jort_identifier_observations(entity_id) WHERE active;
CREATE TABLE IF NOT EXISTS jort_events(
 id text PRIMARY KEY,announcement_id text NOT NULL REFERENCES jort_announcements(id),
 kind text NOT NULL,event_date date,date_precision text NOT NULL DEFAULT 'unknown',
 assertion_status text NOT NULL,quote text NOT NULL,payload jsonb NOT NULL,active boolean NOT NULL DEFAULT true);
CREATE INDEX IF NOT EXISTS jort_event_notice ON jort_events(announcement_id,active);
CREATE TABLE IF NOT EXISTS jort_participants(
 event_id text REFERENCES jort_events(id),entity_id text REFERENCES jort_entities(id),
 mention_id text REFERENCES jort_mentions(id),role text NOT NULL,
 PRIMARY KEY(event_id,mention_id,role));
CREATE INDEX IF NOT EXISTS jort_participant_entity ON jort_participants(entity_id,event_id);
CREATE TABLE IF NOT EXISTS jort_event_objects(event_id text REFERENCES jort_events(id),entity_id text REFERENCES jort_assets(entity_id),PRIMARY KEY(event_id,entity_id));
CREATE TABLE IF NOT EXISTS jort_edges(
 id text PRIMARY KEY,event_id text NOT NULL REFERENCES jort_events(id),a text NOT NULL REFERENCES jort_entities(id),
 b text NOT NULL REFERENCES jort_entities(id),kind text NOT NULL,announcement_id text NOT NULL REFERENCES jort_announcements(id),
 payload jsonb NOT NULL,active boolean NOT NULL DEFAULT true);
CREATE INDEX IF NOT EXISTS jort_edge_a ON jort_edges(a,kind) WHERE active;
CREATE INDEX IF NOT EXISTS jort_edge_b ON jort_edges(b,kind) WHERE active;
CREATE INDEX IF NOT EXISTS jort_edge_notice ON jort_edges(announcement_id) WHERE active;
CREATE TABLE IF NOT EXISTS jort_proofs(
 id text PRIMARY KEY,announcement_id text NOT NULL REFERENCES jort_announcements(id),mention_id text REFERENCES jort_mentions(id),
 event_id text REFERENCES jort_events(id),page_numbers jsonb NOT NULL,start_offset integer,end_offset integer,quote text NOT NULL,
 CHECK(start_offset IS NULL OR (start_offset>=0 AND end_offset>=start_offset)));
CREATE TABLE IF NOT EXISTS jort_identity_decisions(
 id text PRIMARY KEY,target_id text,decision text NOT NULL,method text NOT NULL,reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),undone_at timestamptz,snapshot jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS jort_identity_exclusions(key text PRIMARY KEY);
CREATE TABLE IF NOT EXISTS jort_reconciliation_checkpoints(
 run_id text NOT NULL,group_id text NOT NULL,result jsonb NOT NULL,
 committed_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(run_id,group_id));
CREATE TABLE IF NOT EXISTS jort_tasks(
 id text PRIMARY KEY,document_id text REFERENCES jort_documents(id),stage text NOT NULL,state text NOT NULL,
 version integer NOT NULL,sha256 text,attempts integer NOT NULL DEFAULT 0,message text,
 started_at timestamptz,finished_at timestamptz,updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS jort_migration_checkpoints(
 document_id text PRIMARY KEY REFERENCES jort_documents(id),source_signature text NOT NULL,
 extraction_version integer NOT NULL,state text NOT NULL,updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS jort_run_events(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,run_id text NOT NULL,document_id text,level text NOT NULL,message text NOT NULL,occurred_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS jort_roles(code text PRIMARY KEY,description text NOT NULL);
CREATE TABLE IF NOT EXISTS jort_permissions(role_code text REFERENCES jort_roles(code),permission text NOT NULL,PRIMARY KEY(role_code,permission));
INSERT INTO jort_roles VALUES('admin','Configuration et maintenance'),('operateur','Collecte et revue'),('lecteur','Consultation') ON CONFLICT DO NOTHING;
INSERT INTO jort_permissions VALUES('admin','configure'),('admin','maintain'),('admin','collect'),('admin','read'),('operateur','collect'),('operateur','review'),('operateur','read'),('lecteur','read') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS jort_users(id text PRIMARY KEY,username text UNIQUE NOT NULL,password_hash text,status text NOT NULL DEFAULT 'disabled');
CREATE TABLE IF NOT EXISTS jort_user_roles(user_id text REFERENCES jort_users(id),role_code text REFERENCES jort_roles(code),PRIMARY KEY(user_id,role_code));
