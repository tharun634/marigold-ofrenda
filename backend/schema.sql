-- Marigold stores an ofrenda in Tiger Cloud.
-- Memories carry Gemma embeddings (pgvector) and a BM25 index (pg_textsearch).
-- Candle lights are a Timescale hypertable: append-only, ordered in time.
-- No retention policy: an ofrenda should not forget.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_textsearch;

CREATE TABLE IF NOT EXISTS beloved (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    display_name TEXT NOT NULL,
    relationship TEXT NOT NULL DEFAULT '',
    years_lived TEXT NOT NULL DEFAULT '',
    favorite_food TEXT NOT NULL DEFAULT '',
    favorite_song TEXT NOT NULL DEFAULT '',
    offering TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT beloved_name_len CHECK (char_length(display_name) BETWEEN 1 AND 80)
);

CREATE TABLE IF NOT EXISTS memories (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    beloved_id BIGINT NOT NULL REFERENCES beloved (id) ON DELETE CASCADE,
    told_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    body TEXT NOT NULL,
    embedding halfvec(768),
    CONSTRAINT memories_body_len CHECK (char_length(body) BETWEEN 1 AND 4000)
);

CREATE INDEX IF NOT EXISTS memories_embedding_hnsw
    ON memories USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS memories_body_bm25
    ON memories USING bm25 (body) WITH (text_config = 'english');

CREATE INDEX IF NOT EXISTS memories_beloved_told
    ON memories (beloved_id, told_at DESC);

CREATE TABLE IF NOT EXISTS candle_lights (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    lit_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    beloved_id BIGINT NOT NULL,
    whisper TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (id, lit_at),
    CONSTRAINT candle_whisper_len CHECK (char_length(whisper) <= 280)
) WITH (
    tsdb.hypertable,
    tsdb.partition_column = 'lit_at',
    tsdb.enable_columnstore = true,
    tsdb.segmentby = 'beloved_id',
    tsdb.orderby = 'lit_at DESC'
);

CREATE INDEX IF NOT EXISTS candle_lights_beloved_lit
    ON candle_lights (beloved_id, lit_at DESC);

CREATE MATERIALIZED VIEW IF NOT EXISTS candle_lights_daily
WITH (timescaledb.continuous) AS
SELECT
    time_bucket(INTERVAL '1 day', lit_at) AS bucket,
    beloved_id,
    COUNT(*) AS lights
FROM candle_lights
GROUP BY bucket, beloved_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'candle_lights_daily',
    start_offset => NULL,
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour'
);

ALTER MATERIALIZED VIEW candle_lights_daily
    SET (timescaledb.materialized_only = false);
