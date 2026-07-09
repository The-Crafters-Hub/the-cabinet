-- ════════════════════════════════════════════════════════════════════════════════
-- CONCIERGE SYSTEM SCHEMA
-- ════════════════════════════════════════════════════════════════════════════════
-- Purpose: Store all customer conversations, AI decisions, and metrics for monitoring
-- Version: 1.0
-- Created: April 1, 2026
-- ════════════════════════════════════════════════════════════════════════════════

-- ──────────────────────────────────────────────────────────────────────────────
-- TABLE: concierge_conversations
-- Purpose: Log every customer message and AI response for audit + analytics
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS concierge_conversations (
    id SERIAL PRIMARY KEY,
    session_id UUID DEFAULT gen_random_uuid() NOT NULL,
    channel VARCHAR(50) NOT NULL CHECK (channel IN ('whatsapp', 'messenger', 'instagram', 'web')),
    sender_id VARCHAR(255) NOT NULL,
    sender_name VARCHAR(255),
    original_message TEXT NOT NULL,
    message_language VARCHAR(10),
    rewritten_query TEXT,
    cabinet_results JSONB,
    ai_model VARCHAR(100),
    ai_response TEXT,
    response_time_ms INT,
    model_used VARCHAR(100),
    intent_detected VARCHAR(100),
    confidence_score DECIMAL(3,2),
    escalated BOOLEAN DEFAULT FALSE,
    escalation_reason VARCHAR(255),
    escalated_to VARCHAR(100),
    customer_feedback INT CHECK (customer_feedback >= 1 AND customer_feedback <= 5),
    feedback_text TEXT,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_concierge_channel ON concierge_conversations(channel);
CREATE INDEX IF NOT EXISTS idx_concierge_sender ON concierge_conversations(sender_id);
CREATE INDEX IF NOT EXISTS idx_concierge_session ON concierge_conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_concierge_escalated ON concierge_conversations(escalated);
CREATE INDEX IF NOT EXISTS idx_concierge_created ON concierge_conversations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_concierge_model ON concierge_conversations(ai_model);

-- ──────────────────────────────────────────────────────────────────────────────
-- TABLE: customer_interactions
-- Purpose: Build customer history + memory for personalization
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS customer_interactions (
    id SERIAL PRIMARY KEY,
    customer_id INT REFERENCES students(id) ON DELETE SET NULL,
    wix_contact_id VARCHAR(255),
    conversation_id INT REFERENCES concierge_conversations(id) ON DELETE CASCADE,
    message_text TEXT,
    ai_response TEXT,
    embedding VECTOR(384),
    interaction_type VARCHAR(100) CHECK (interaction_type IN ('booking', 'question', 'complaint', 'feedback', 'other')),
    sentiment VARCHAR(50) CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    satisfaction_score INT CHECK (satisfaction_score >= 1 AND satisfaction_score <= 5),
    outcome VARCHAR(100) CHECK (outcome IN ('resolved', 'escalated', 'booked', 'follow_up', 'other')),
    tags JSONB,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for retrieval
CREATE INDEX IF NOT EXISTS idx_interactions_customer ON customer_interactions(customer_id);
CREATE INDEX IF NOT EXISTS idx_interactions_embedding ON customer_interactions USING ivfflat (embedding vector_cosine_ops) WITH (lists=100);
CREATE INDEX IF NOT EXISTS idx_interactions_type ON customer_interactions(interaction_type);
CREATE INDEX IF NOT EXISTS idx_interactions_sentiment ON customer_interactions(sentiment);

-- ──────────────────────────────────────────────────────────────────────────────
-- TABLE: wix_services_cache
-- Purpose: Cache Wix services to prevent rate limiting during peak hours
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wix_services_cache (
    id SERIAL PRIMARY KEY,
    wix_service_id VARCHAR(255) UNIQUE NOT NULL,
    service_type VARCHAR(100),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    price_egp DECIMAL(10, 2),
    currency VARCHAR(10) DEFAULT 'EGP',
    duration_minutes INT,
    category VARCHAR(100),
    instructor_id INT REFERENCES instructors(id) ON DELETE SET NULL,
    age_min INT,
    age_max INT,
    max_participants INT,
    status VARCHAR(50) DEFAULT 'ACTIVE',
    synced_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_wix_services_synced ON wix_services_cache(synced_at DESC);
CREATE INDEX IF NOT EXISTS idx_wix_services_id ON wix_services_cache(wix_service_id);

-- ──────────────────────────────────────────────────────────────────────────────
-- TABLE: wix_availability_cache
-- Purpose: Cache Wix availability slots to prevent rate limiting
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wix_availability_cache (
    id SERIAL PRIMARY KEY,
    wix_service_id VARCHAR(255) NOT NULL REFERENCES wix_services_cache(wix_service_id) ON DELETE CASCADE,
    available_date DATE NOT NULL,
    available_time TIME NOT NULL,
    slots_available INT NOT NULL,
    status VARCHAR(50) DEFAULT 'AVAILABLE',
    synced_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_availability_service ON wix_availability_cache(wix_service_id);
CREATE INDEX IF NOT EXISTS idx_availability_date ON wix_availability_cache(available_date);
CREATE INDEX IF NOT EXISTS idx_availability_synced ON wix_availability_cache(synced_at DESC);

-- ──────────────────────────────────────────────────────────────────────────────
-- TABLE: concierge_metrics
-- Purpose: Aggregate hourly metrics for dashboard + alerting
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS concierge_metrics (
    id SERIAL PRIMARY KEY,
    metric_hour TIMESTAMP WITH TIME ZONE NOT NULL,
    total_conversations INT,
    escalated_count INT,
    avg_response_time_ms INT,
    resolution_rate DECIMAL(5, 2),
    csat_avg DECIMAL(3, 2),
    top_intent VARCHAR(100),
    top_channel VARCHAR(50),
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_metrics_hour ON concierge_metrics(metric_hour DESC);

-- ──────────────────────────────────────────────────────────────────────────────
-- TRIGGER: Update concierge_conversations.updated_at
-- ──────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_concierge_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_concierge_updated_at ON concierge_conversations;
CREATE TRIGGER trg_concierge_updated_at
BEFORE UPDATE ON concierge_conversations
FOR EACH ROW
EXECUTE FUNCTION update_concierge_updated_at();

-- ──────────────────────────────────────────────────────────────────────────────
-- VIEWS FOR ANALYTICS
-- ──────────────────────────────────────────────────────────────────────────────

-- View: Channel Performance Summary
CREATE OR REPLACE VIEW v_concierge_channel_performance AS
SELECT
    channel,
    COUNT(*) as total_conversations,
    COUNT(*) FILTER (WHERE escalated = TRUE) as escalated,
    ROUND(100.0 * COUNT(*) FILTER (WHERE escalated = TRUE) / COUNT(*), 2) as escalation_rate,
    ROUND(AVG(response_time_ms), 2) as avg_response_ms,
    ROUND(AVG(customer_feedback), 2) as avg_csat,
    MAX(created_at) as last_conversation
FROM concierge_conversations
GROUP BY channel;

-- View: Daily Trends
CREATE OR REPLACE VIEW v_concierge_daily_trends AS
SELECT
    DATE(created_at) as conversation_date,
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE escalated = TRUE) as escalated,
    ROUND(100.0 * COUNT(*) FILTER (WHERE escalated = TRUE) / COUNT(*), 2) as escalation_pct,
    ROUND(AVG(response_time_ms), 2) as avg_response_ms,
    COUNT(DISTINCT sender_id) as unique_customers,
    COUNT(DISTINCT session_id) as unique_sessions
FROM concierge_conversations
GROUP BY DATE(created_at)
ORDER BY conversation_date DESC;

-- View: Top Intents
CREATE OR REPLACE VIEW v_concierge_top_intents AS
SELECT
    intent_detected,
    COUNT(*) as count,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM concierge_conversations WHERE intent_detected IS NOT NULL), 2) as percentage,
    ROUND(AVG(response_time_ms), 2) as avg_response_ms,
    ROUND(100.0 * COUNT(*) FILTER (WHERE escalated = TRUE) / COUNT(*), 2) as escalation_rate
FROM concierge_conversations
WHERE intent_detected IS NOT NULL
GROUP BY intent_detected
ORDER BY count DESC
LIMIT 20;

-- ════════════════════════════════════════════════════════════════════════════════
-- INITIAL DATA (OPTIONAL)
-- ════════════════════════════════════════════════════════════════════════════════
-- Uncomment to add sample data for testing
-- INSERT INTO concierge_conversations
-- (channel, sender_id, original_message, rewritten_query, ai_response, response_time_ms, intent_detected, escalated)
-- VALUES
-- ('whatsapp', '+201001234567', 'هل فيه كورسات الخميس؟', 'woodworking class schedule thursday', 'Yes, we have classes on Thursday at 3 PM and 6 PM.', 1250, 'scheduling', FALSE),
-- ('messenger', 'user-12345', 'whats the price for the beginner woodworking?', 'beginner woodworking course pricing', 'The beginner woodworking course is EGP 600 for 4 sessions.', 890, 'pricing', FALSE);
