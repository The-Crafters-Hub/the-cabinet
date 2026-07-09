-- 05_marketing_schema.sql
-- Table to store aggregated daily metrics from Meta and Google Analytics
-- Used by Metabase and Gordon AI

CREATE TABLE IF NOT EXISTS marketing_metrics (
    id SERIAL PRIMARY KEY,
    log_date DATE NOT NULL,
    platform VARCHAR(50) NOT NULL, -- e.g., 'facebook', 'instagram', 'ga4'
    metric_name VARCHAR(100) NOT NULL, -- e.g., 'reach', 'sessions', 'conversions'
    metric_value NUMERIC NOT NULL,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Ensure we don't insert the same metric for the same platform on the same day twice
    CONSTRAINT uq_marketing_metric UNIQUE (log_date, platform, metric_name)
);

-- Indexes for fast querying by Gordon AI and Metabase
CREATE INDEX IF NOT EXISTS idx_marketing_date ON marketing_metrics(log_date);
CREATE INDEX IF NOT EXISTS idx_marketing_platform ON marketing_metrics(platform);
