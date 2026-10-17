"""Independent bounded, append-only public progress schema."""
from alembic import op

revision = "m24_0001_public_progress"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE frontend_experience.progress_runs(
        run_id uuid PRIMARY KEY, trace_id uuid NOT NULL, record_count integer NOT NULL DEFAULT 0 CHECK(record_count BETWEEN 0 AND 512),
        limited boolean NOT NULL DEFAULT false, settled boolean NOT NULL DEFAULT false,
        UNIQUE(run_id,trace_id))""")
    op.execute("""CREATE TABLE frontend_experience.public_records(
        run_id uuid NOT NULL, trace_id uuid NOT NULL, sequence integer NOT NULL CHECK(sequence BETWEEN 1 AND 512),
        event_key varchar(256) NOT NULL CHECK(event_key ~ '^[a-z0-9:_-]+$'),
        occurred_at timestamptz NOT NULL CHECK(extract(timezone FROM occurred_at)=0),
        content_bytes bytea NOT NULL CHECK(octet_length(content_bytes) BETWEEN 1 AND 16384),
        content_hash varchar(64) NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
        PRIMARY KEY(run_id,sequence), UNIQUE(run_id,event_key),
        FOREIGN KEY(run_id,trace_id) REFERENCES frontend_experience.progress_runs(run_id,trace_id))""")
    op.execute("""CREATE TABLE frontend_experience.capacity(
        singleton boolean PRIMARY KEY CHECK(singleton=true),
        record_count integer NOT NULL CHECK(record_count BETWEEN 0 AND 16384))""")
    op.execute("INSERT INTO frontend_experience.capacity VALUES(true,0)")


def downgrade():
    raise RuntimeError("forward-only migration")
