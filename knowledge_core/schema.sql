PRAGMA foreign_keys = ON;
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE import_runs (
    run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, version TEXT NOT NULL,
    inventory_json TEXT NOT NULL CHECK(json_valid(inventory_json)),
    status TEXT NOT NULL CHECK(status IN ('building','complete'))
);
CREATE TABLE lectures (
    lecture_id TEXT PRIMARY KEY, identity_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL
);
CREATE TABLE run_lectures (
    run_id TEXT NOT NULL REFERENCES import_runs, lecture_id TEXT NOT NULL REFERENCES lectures,
    directory TEXT NOT NULL, title TEXT NOT NULL, course_position INTEGER,
    PRIMARY KEY(run_id, lecture_id), UNIQUE(run_id, directory)
);
CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
    lecture_id TEXT REFERENCES lectures, identity_key TEXT NOT NULL UNIQUE
);
CREATE TABLE artifact_revisions (
    revision_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts,
    sha256 TEXT NOT NULL CHECK(length(sha256)=64),
    byte_size INTEGER NOT NULL CHECK(byte_size >= 0), blob_path TEXT NOT NULL,
    UNIQUE(artifact_id, sha256)
);
CREATE TABLE run_artifacts (
    run_id TEXT NOT NULL REFERENCES import_runs, source_path TEXT NOT NULL,
    revision_id TEXT NOT NULL REFERENCES artifact_revisions,
    PRIMARY KEY(run_id,source_path)
);
CREATE TABLE legacy_records (
    record_id TEXT PRIMARY KEY, record_type TEXT NOT NULL, legacy_id TEXT,
    artifact_revision TEXT NOT NULL REFERENCES artifact_revisions,
    locator TEXT NOT NULL, raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
    UNIQUE(artifact_revision,locator)
);
CREATE TABLE run_records (
    run_id TEXT NOT NULL REFERENCES import_runs, record_id TEXT NOT NULL REFERENCES legacy_records,
    PRIMARY KEY(run_id,record_id)
);
CREATE TABLE evidence_units (
    evidence_id TEXT PRIMARY KEY, artifact_revision TEXT NOT NULL REFERENCES artifact_revisions,
    lecture_id TEXT NOT NULL REFERENCES lectures,
    kind TEXT NOT NULL CHECK(kind IN ('transcript_segment','source_unit','frame','ocr')),
    segment_index INTEGER CHECK(segment_index >= 0),
    start_ms INTEGER CHECK(start_ms >= 0), end_ms INTEGER,
    text TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
    CHECK(end_ms IS NULL OR (start_ms IS NOT NULL AND end_ms >= start_ms))
);
CREATE TABLE run_evidence_alias (
    run_id TEXT NOT NULL REFERENCES import_runs, namespace TEXT NOT NULL, legacy_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL REFERENCES evidence_units,
    PRIMARY KEY(run_id,namespace,legacy_id)
);
CREATE TABLE evidence_links (
    from_evidence TEXT NOT NULL REFERENCES evidence_units,
    to_evidence TEXT NOT NULL REFERENCES evidence_units,
    role TEXT NOT NULL, record_id TEXT NOT NULL REFERENCES legacy_records,
    run_id TEXT NOT NULL REFERENCES import_runs,
    PRIMARY KEY(from_evidence,to_evidence,role,record_id,run_id)
);
CREATE TABLE knowledge_items (
    item_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, legacy_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('claim','candidate','rule','concept')),
    UNIQUE(namespace,legacy_id)
);
CREATE TABLE knowledge_revisions (
    revision_id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES knowledge_items,
    record_id TEXT NOT NULL REFERENCES legacy_records,
    statement TEXT NOT NULL, quote TEXT NOT NULL, interpretation TEXT NOT NULL,
    conditions_json TEXT NOT NULL CHECK(json_valid(conditions_json)),
    exceptions_json TEXT NOT NULL CHECK(json_valid(exceptions_json)),
    context_json TEXT NOT NULL CHECK(json_valid(context_json)), legacy_status TEXT,
    review_state TEXT NOT NULL DEFAULT 'imported_unverified',
    UNIQUE(revision_id,item_id)
);
CREATE TABLE run_knowledge (
    run_id TEXT NOT NULL REFERENCES import_runs, item_id TEXT NOT NULL REFERENCES knowledge_items,
    revision_id TEXT NOT NULL,
    PRIMARY KEY(run_id,item_id),
    FOREIGN KEY(revision_id,item_id) REFERENCES knowledge_revisions(revision_id,item_id)
);
CREATE TABLE knowledge_evidence (
    revision_id TEXT NOT NULL REFERENCES knowledge_revisions,
    evidence_id TEXT NOT NULL REFERENCES evidence_units, role TEXT NOT NULL,
    PRIMARY KEY(revision_id,evidence_id,role)
);
CREATE TABLE knowledge_relations (
    from_revision TEXT NOT NULL REFERENCES knowledge_revisions,
    to_revision TEXT NOT NULL REFERENCES knowledge_revisions,
    relation_type TEXT NOT NULL, role TEXT NOT NULL,
    record_id TEXT NOT NULL REFERENCES legacy_records,
    PRIMARY KEY(from_revision,to_revision,relation_type,role)
);
CREATE TABLE review_events (
    review_id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES legacy_records,
    target_revision TEXT REFERENCES knowledge_revisions, legacy_target TEXT,
    legacy_status TEXT, actor TEXT, event_at TEXT, scope TEXT NOT NULL,
    origin TEXT NOT NULL CHECK(origin='imported_legacy'),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json))
);
CREATE TABLE run_reviews (
    run_id TEXT NOT NULL REFERENCES import_runs,
    review_id TEXT NOT NULL REFERENCES review_events,
    PRIMARY KEY(run_id,review_id)
);
CREATE TABLE concept_terms (
    revision_id TEXT NOT NULL REFERENCES knowledge_revisions,
    term TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY(revision_id,term,kind)
);
CREATE TABLE import_issues (
    issue_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES import_runs,
    severity TEXT NOT NULL CHECK(severity IN ('warning','error')),
    code TEXT NOT NULL, detail_json TEXT NOT NULL CHECK(json_valid(detail_json))
);
CREATE INDEX evidence_lecture ON evidence_units(lecture_id,kind);
CREATE INDEX evidence_target ON knowledge_evidence(evidence_id);
CREATE INDEX relation_target ON knowledge_relations(to_revision);
CREATE INDEX artifact_hash ON artifact_revisions(sha256);
CREATE INDEX run_evidence_target ON run_evidence_alias(run_id,evidence_id);
CREATE INDEX run_evidence_from ON evidence_links(run_id,from_evidence);
CREATE INDEX run_evidence_to ON evidence_links(run_id,to_evidence);
CREATE VIRTUAL TABLE search_index USING fts5(
    target_id UNINDEXED, kind UNINDEXED, title, text,
    tokenize='unicode61 remove_diacritics 2'
);
