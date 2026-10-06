"""Six independent application metadata tables, append-only except handoffs."""
from alembic import op

revision="m14_0001_web_application"
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
    statements = [
        """CREATE TABLE web_application.visitors (
            visitor_hash CHAR(64) PRIMARY KEY,
            issued_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ck_visitors_hash CHECK (visitor_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_visitors_expiry CHECK (expires_at = issued_at + interval '2592000 seconds'))""",
        """CREATE TABLE web_application.run_owners (
            run_id UUID PRIMARY KEY, visitor_hash CHAR(64) NOT NULL, bound_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_owner_pair UNIQUE(run_id,visitor_hash),
            CONSTRAINT fk_owner_visitor FOREIGN KEY(visitor_hash) REFERENCES web_application.visitors(visitor_hash) ON UPDATE NO ACTION ON DELETE NO ACTION)""",
        """CREATE INDEX ix_owner_history ON web_application.run_owners(visitor_hash,bound_at DESC,run_id DESC)""",
        """CREATE TABLE web_application.requests (
            visitor_hash CHAR(64) NOT NULL, request_id UUID NOT NULL, run_id UUID NOT NULL,
            kind TEXT NOT NULL, body_fingerprint CHAR(64) NOT NULL, pause_generation BIGINT,
            status TEXT NOT NULL, reason TEXT, created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY(visitor_hash,request_id),
            CONSTRAINT fk_request_owner FOREIGN KEY(run_id,visitor_hash) REFERENCES web_application.run_owners(run_id,visitor_hash) ON UPDATE NO ACTION ON DELETE NO ACTION,
            CONSTRAINT ck_request_kind CHECK(kind IN ('start','clarify','confirm','hotel','cancel','recover')),
            CONSTRAINT ck_request_fingerprint CHECK(body_fingerprint ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_request_status CHECK(status IN ('claimed','dispatched','rejected','uncertain')),
            CONSTRAINT ck_request_generation CHECK((kind IN ('clarify','confirm','hotel') AND pause_generation BETWEEN 0 AND 9007199254740991) OR (kind IN ('start','cancel','recover') AND pause_generation IS NULL)),
            CONSTRAINT ck_request_generation_required CHECK(kind NOT IN ('clarify','confirm','hotel') OR pause_generation IS NOT NULL),
            CONSTRAINT ck_request_reason CHECK((status IN ('claimed','dispatched') AND reason IS NULL) OR (status IN ('rejected','uncertain') AND reason IS NOT NULL)),
            CONSTRAINT ck_request_reason_code CHECK(reason IS NULL OR reason IN ('invalid_request','body_too_large','origin_rejected','csrf_rejected','not_found','busy','quota_exceeded','capacity_unavailable','dependency_unavailable','request_conflict','stale_pause','pause_unavailable','admission_unconfirmed','recovery_unavailable','result_not_ready','summary_not_saved','process_history_unavailable','service_closing')))""",
        """CREATE UNIQUE INDEX uq_pause_claim ON web_application.requests(run_id,pause_generation) WHERE kind IN ('clarify','confirm','hotel') AND status IN ('claimed','dispatched','uncertain')""",
        """CREATE TABLE web_application.admissions (
            run_id UUID PRIMARY KEY, visitor_hash CHAR(64) NOT NULL, ip_hash CHAR(64) NOT NULL,
            reserved_at TIMESTAMPTZ NOT NULL, day_key DATE NOT NULL, status TEXT NOT NULL,
            CONSTRAINT fk_admission_owner FOREIGN KEY(run_id,visitor_hash) REFERENCES web_application.run_owners(run_id,visitor_hash) ON UPDATE NO ACTION ON DELETE NO ACTION,
            CONSTRAINT ck_admission_ip CHECK(ip_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_admission_status CHECK(status IN ('reserved','counted','released')),
            CONSTRAINT ck_admission_day CHECK(day_key = (reserved_at AT TIME ZONE 'Asia/Shanghai')::date))""",
        """CREATE INDEX ix_admission_global ON web_application.admissions(day_key,status)""",
        """CREATE INDEX ix_admission_visitor ON web_application.admissions(visitor_hash,day_key,status)""",
        """CREATE INDEX ix_admission_ip ON web_application.admissions(ip_hash,reserved_at,status)""",
        """CREATE TABLE web_application.pause_credentials (
            run_id UUID NOT NULL, pause_generation BIGINT NOT NULL, contract_digest TEXT NOT NULL,
            cipher_version TEXT NOT NULL, nonce BYTEA NOT NULL, ciphertext BYTEA NOT NULL, saved_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY(run_id,pause_generation),
            CONSTRAINT fk_pause_owner FOREIGN KEY(run_id) REFERENCES web_application.run_owners(run_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
            CONSTRAINT ck_pause_generation CHECK(pause_generation BETWEEN 0 AND 9007199254740991),
            CONSTRAINT ck_pause_digest CHECK(contract_digest ~ '^sha256:[0-9a-f]{64}$'),
            CONSTRAINT ck_pause_version CHECK(cipher_version='aesgcm-v1'),
            CONSTRAINT ck_pause_nonce CHECK(octet_length(nonce)=12),
            CONSTRAINT ck_pause_ciphertext CHECK(octet_length(ciphertext)>=16))""",
        """CREATE TABLE web_application.run_summaries (
            run_id UUID PRIMARY KEY, trace_id UUID NOT NULL, terminal_code TEXT NOT NULL,
            availability TEXT NOT NULL, content_hash TEXT, content_bytes BYTEA, size_bytes BIGINT, saved_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT fk_summary_owner FOREIGN KEY(run_id) REFERENCES web_application.run_owners(run_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
            CONSTRAINT ck_summary_terminal CHECK(terminal_code ~ '^[a-z][a-z0-9_]{0,63}$'),
            CONSTRAINT ck_summary_availability CHECK(availability IN ('available','process_history_unavailable')),
            CONSTRAINT ck_summary_content CHECK((availability='available' AND content_hash IS NOT NULL AND content_bytes IS NOT NULL AND size_bytes IS NOT NULL AND content_hash ~ '^sha256:[0-9a-f]{64}$' AND size_bytes BETWEEN 1 AND 1048576 AND size_bytes=octet_length(content_bytes)) OR (availability='process_history_unavailable' AND content_hash IS NULL AND content_bytes IS NULL AND size_bytes IS NULL)))"""
    ]
    for statement in statements:
        op.execute(statement)

def downgrade():
    raise RuntimeError("M14 downgrade is not supported")
