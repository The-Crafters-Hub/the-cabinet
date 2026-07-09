-- ============================================================
-- Heritage Archive: artisan_techniques table
-- Phase A · July 2026
-- ============================================================

CREATE TABLE IF NOT EXISTS artisan_techniques (
    id                  SERIAL PRIMARY KEY,
    technique_name      VARCHAR(255) NOT NULL,
    artisan_name        VARCHAR(255),
    region              VARCHAR(255),
    category            VARCHAR(100),
    materials           TEXT[],
    tools               TEXT[],
    description         TEXT,
    raw_submission_text TEXT,
    source_url          TEXT,
    whatsapp_media_id   TEXT,
    submission_channel  VARCHAR(50) DEFAULT 'whatsapp',
    risk_level          VARCHAR(20) DEFAULT 'unknown',
    verification_status VARCHAR(20) DEFAULT 'pending',
    embedding_vector    vector(768),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_at_category  ON artisan_techniques(category);
CREATE INDEX IF NOT EXISTS idx_at_risk      ON artisan_techniques(risk_level);
CREATE INDEX IF NOT EXISTS idx_at_status    ON artisan_techniques(verification_status);
CREATE INDEX IF NOT EXISTS idx_at_embedding
    ON artisan_techniques
    USING ivfflat (embedding_vector vector_cosine_ops)
    WITH (lists = 50);

CREATE OR REPLACE FUNCTION update_artisan_techniques_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_at_updated_at ON artisan_techniques;
CREATE TRIGGER trg_at_updated_at
    BEFORE UPDATE ON artisan_techniques
    FOR EACH ROW EXECUTE FUNCTION update_artisan_techniques_timestamp();

SELECT 'TABLE OK' AS status, COUNT(*) AS rows FROM artisan_techniques;
