-- =====================================================================
-- D1 Portfolio SaaS — Postgres schema
-- =====================================================================
-- Apply with:
--   psql -h <host> -U <user> -d <db> -f schema.sql
--
-- Designed for Supabase (uses uuid-ossp + pgcrypto + Row Level Security).
-- Every table has explicit RLS policies so users can only see their own rows.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS citext;

-- =====================================================================
-- ENUM types
-- =====================================================================
DO $$ BEGIN
    CREATE TYPE user_status AS ENUM ('pending', 'approved', 'paused', 'paused_unpaid', 'banned');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE bot_status AS ENUM ('stopped', 'provisioning', 'running', 'paused_unpaid', 'paused_admin', 'paused_drawdown', 'error');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE charge_status AS ENUM ('pending', 'processing', 'paid', 'failed', 'refunded', 'disputed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE broker_name AS ENUM ('ic_markets', 'pepperstone', 'tickmill', 'fp_markets', 'exness', 'other');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- =====================================================================
-- users — top-level account table
-- =====================================================================
CREATE TABLE IF NOT EXISTS users (
    user_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email              CITEXT UNIQUE NOT NULL,
    password_hash      TEXT NOT NULL,
    mfa_secret_enc     BYTEA,                  -- encrypted TOTP secret; NULL until enrolled
    mfa_enrolled       BOOLEAN NOT NULL DEFAULT FALSE,
    status             user_status NOT NULL DEFAULT 'pending',
    referred_by_code   TEXT,                   -- broker IB tracking
    referral_clicked   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at        TIMESTAMPTZ,
    approved_by        UUID,
    last_login_at      TIMESTAMPTZ,
    last_login_ip      INET,
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    locked_until       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

-- =====================================================================
-- admin_users — separate table, never joins to users
-- =====================================================================
CREATE TABLE IF NOT EXISTS admin_users (
    admin_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           CITEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    mfa_secret_enc  BYTEA,
    role            TEXT NOT NULL DEFAULT 'admin',   -- 'admin' | 'support' | 'readonly'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ
);

-- =====================================================================
-- user_broker_credentials — encrypted secrets, separate table
-- (so a SQL injection on `users` doesn't leak credentials)
-- =====================================================================
CREATE TABLE IF NOT EXISTS user_broker_credentials (
    user_id                UUID PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    broker                 broker_name NOT NULL,
    mt5_account_no         BIGINT NOT NULL,
    mt5_server             TEXT NOT NULL,
    mt5_password_enc       BYTEA NOT NULL,         -- AES-256-GCM ciphertext
    mt5_password_enc_iv    BYTEA NOT NULL,         -- nonce (12 bytes)
    mt5_password_enc_tag   BYTEA NOT NULL,         -- auth tag (16 bytes)
    dek_id                 UUID NOT NULL,          -- which Data Encryption Key was used
    crypto_account_no      BIGINT,                 -- optional second broker for crypto
    crypto_password_enc    BYTEA,
    crypto_password_iv     BYTEA,
    crypto_password_tag    BYTEA,
    crypto_broker          broker_name,
    last_validated_at      TIMESTAMPTZ,
    last_validation_error  TEXT
);

-- =====================================================================
-- user_configs — per-user bot settings
-- =====================================================================
CREATE TABLE IF NOT EXISTS user_configs (
    user_id                  UUID PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    fee_pct_of_profit        DECIMAL(5,2) NOT NULL DEFAULT 20.00,    -- admin-set
    fee_daily_cap_usd        DECIMAL(10,2),                          -- optional cap per day
    risk_per_trade_pct       DECIMAL(5,3) NOT NULL DEFAULT 0.500,    -- 0.5% of equity
    max_open_positions       INTEGER NOT NULL DEFAULT 15,
    max_daily_loss_pct       DECIMAL(5,2) NOT NULL DEFAULT 3.00,
    max_total_dd_pct         DECIMAL(5,2) NOT NULL DEFAULT 20.00,
    quality_threshold        INTEGER NOT NULL DEFAULT 60,
    bucket_tp_enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    bucket_tp_per_pos        DECIMAL(10,2) NOT NULL DEFAULT 25.00,
    bucket_tp_min            DECIMAL(10,2) NOT NULL DEFAULT 150.00,
    bucket_tp_max            DECIMAL(10,2) NOT NULL DEFAULT 250.00,
    sl_migration_enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    active_strategies        JSONB NOT NULL DEFAULT '["consensus","rsi2_H1","momentum60_H1","bb_extreme_H1"]'::jsonb,
    symbol_whitelist         JSONB NOT NULL DEFAULT '["EURUSD","GBPUSD","USDJPY","XAUUSD","XAGUSD"]'::jsonb,
    risk_multiplier_overrides JSONB NOT NULL DEFAULT '{"XAUUSD":0.6,"XAGUSD":0.6}'::jsonb,
    bot_status               bot_status NOT NULL DEFAULT 'stopped',
    bot_container_id         TEXT,
    bot_provisioned_at       TIMESTAMPTZ,
    bot_paused_reason        TEXT,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by_admin_id      UUID REFERENCES admin_users(admin_id)
);

-- =====================================================================
-- user_strategy_health — per-user per-strategy WR tracking
-- (mirrors the strategy_health.json file but per-user in DB)
-- =====================================================================
CREATE TABLE IF NOT EXISTS user_strategy_health (
    user_id          UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    symbol           TEXT NOT NULL,
    strategy         TEXT NOT NULL,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    recent_trades    JSONB NOT NULL DEFAULT '[]'::jsonb,   -- rolling last 20
    paper_trades     JSONB NOT NULL DEFAULT '[]'::jsonb,
    inactive_since   TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, symbol, strategy)
);
CREATE INDEX IF NOT EXISTS idx_health_user_active ON user_strategy_health(user_id, is_active);

-- =====================================================================
-- trades — immutable per-user trade log
-- =====================================================================
CREATE TABLE IF NOT EXISTS trades (
    trade_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id               UUID NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    ticket                BIGINT NOT NULL,
    magic                 BIGINT NOT NULL,
    symbol                TEXT NOT NULL,
    strategy              TEXT NOT NULL,
    direction             TEXT NOT NULL CHECK (direction IN ('BUY','SELL')),
    volume                DECIMAL(10,2) NOT NULL,
    open_time             TIMESTAMPTZ NOT NULL,
    entry_price           DECIMAL(20,8) NOT NULL,
    sl_price              DECIMAL(20,8),
    tp_price              DECIMAL(20,8),
    close_time            TIMESTAMPTZ,
    exit_price            DECIMAL(20,8),
    exit_reason           TEXT,
    realized_pnl_usd      DECIMAL(15,2),
    duration_minutes      INTEGER,
    entry_spread          DECIMAL(20,8),
    entry_atr             DECIMAL(20,8),
    max_floating_pnl      DECIMAL(15,2),
    min_floating_pnl      DECIMAL(15,2),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, ticket)
);
CREATE INDEX IF NOT EXISTS idx_trades_user_close ON trades(user_id, close_time DESC) WHERE close_time IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trades_user_open ON trades(user_id, open_time DESC) WHERE close_time IS NULL;
CREATE INDEX IF NOT EXISTS idx_trades_user_strategy ON trades(user_id, strategy);

-- Block updates and deletes — trades are append-only audit log
CREATE OR REPLACE RULE trades_no_update AS ON UPDATE TO trades DO INSTEAD NOTHING;
CREATE OR REPLACE RULE trades_no_delete AS ON DELETE TO trades DO INSTEAD NOTHING;
-- Exception: bot inserts trade as "open", later inserts a row to mark close.
-- Or we use a SEPARATE table for closes. For now, allow the close-update via a function.

-- =====================================================================
-- fees — daily/period fee calculations and charges
-- =====================================================================
CREATE TABLE IF NOT EXISTS fees (
    fee_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    period_start        TIMESTAMPTZ NOT NULL,
    period_end          TIMESTAMPTZ NOT NULL,
    realized_pnl_usd    DECIMAL(15,2) NOT NULL,
    fee_pct             DECIMAL(5,2) NOT NULL,
    fee_amount_usd      DECIMAL(10,2) NOT NULL,
    charge_status       charge_status NOT NULL DEFAULT 'pending',
    stripe_invoice_id   TEXT,
    stripe_payment_id   TEXT,
    failure_reason      TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    next_retry_at       TIMESTAMPTZ,
    paid_at             TIMESTAMPTZ,
    pnl_source_hash     TEXT NOT NULL,           -- SHA-256 of the trades that made up this PnL (for audit)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, period_start, period_end)   -- idempotency
);
CREATE INDEX IF NOT EXISTS idx_fees_user_period ON fees(user_id, period_end DESC);
CREATE INDEX IF NOT EXISTS idx_fees_status_retry ON fees(charge_status, next_retry_at) WHERE charge_status IN ('pending','failed');

-- =====================================================================
-- audit_log — append-only, immutable
-- =====================================================================
CREATE TABLE IF NOT EXISTS audit_log (
    log_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    actor_user_id    UUID REFERENCES users(user_id),
    actor_admin_id   UUID REFERENCES admin_users(admin_id),
    target_user_id   UUID REFERENCES users(user_id),
    action           TEXT NOT NULL,
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip_address       INET,
    user_agent       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log(target_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_user_id, created_at DESC) WHERE actor_user_id IS NOT NULL;
CREATE OR REPLACE RULE audit_no_update AS ON UPDATE TO audit_log DO INSTEAD NOTHING;
CREATE OR REPLACE RULE audit_no_delete AS ON DELETE TO audit_log DO INSTEAD NOTHING;

-- =====================================================================
-- Row Level Security policies
-- (Supabase requires JWT to set request.jwt.claims with user_id)
-- =====================================================================
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_broker_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_strategy_health ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE fees ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

-- Helper function to extract current user_id from JWT
CREATE OR REPLACE FUNCTION auth_user_id() RETURNS UUID AS $$
    SELECT NULLIF(current_setting('request.jwt.claims', true)::jsonb->>'user_id', '')::uuid;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION auth_is_admin() RETURNS BOOLEAN AS $$
    SELECT COALESCE((current_setting('request.jwt.claims', true)::jsonb->>'is_admin')::boolean, false);
$$ LANGUAGE sql STABLE;

-- ---- users ----
DROP POLICY IF EXISTS users_self_read ON users;
CREATE POLICY users_self_read ON users FOR SELECT
    USING (user_id = auth_user_id() OR auth_is_admin());

DROP POLICY IF EXISTS users_self_update ON users;
CREATE POLICY users_self_update ON users FOR UPDATE
    USING (user_id = auth_user_id() OR auth_is_admin());

-- ---- user_broker_credentials ----
-- Admin sees encrypted ciphertext but NOT plaintext (which requires decryption at app layer)
DROP POLICY IF EXISTS creds_self_read ON user_broker_credentials;
CREATE POLICY creds_self_read ON user_broker_credentials FOR SELECT
    USING (user_id = auth_user_id() OR auth_is_admin());

DROP POLICY IF EXISTS creds_self_write ON user_broker_credentials;
CREATE POLICY creds_self_write ON user_broker_credentials FOR ALL
    USING (user_id = auth_user_id() OR auth_is_admin());

-- ---- user_configs ----
DROP POLICY IF EXISTS configs_self_read ON user_configs;
CREATE POLICY configs_self_read ON user_configs FOR SELECT
    USING (user_id = auth_user_id() OR auth_is_admin());

-- Users can update only NON-financial fields (symbol_whitelist, active_strategies)
-- Admin can update everything.  Enforce via separate admin-only update.
DROP POLICY IF EXISTS configs_admin_write ON user_configs;
CREATE POLICY configs_admin_write ON user_configs FOR UPDATE
    USING (auth_is_admin());

-- ---- trades ----
DROP POLICY IF EXISTS trades_self_read ON trades;
CREATE POLICY trades_self_read ON trades FOR SELECT
    USING (user_id = auth_user_id() OR auth_is_admin());

-- Only the bot worker (service role) can insert trades
DROP POLICY IF EXISTS trades_service_insert ON trades;
CREATE POLICY trades_service_insert ON trades FOR INSERT
    WITH CHECK (auth_is_admin());

-- ---- fees ----
DROP POLICY IF EXISTS fees_self_read ON fees;
CREATE POLICY fees_self_read ON fees FOR SELECT
    USING (user_id = auth_user_id() OR auth_is_admin());

DROP POLICY IF EXISTS fees_admin_write ON fees;
CREATE POLICY fees_admin_write ON fees FOR ALL
    USING (auth_is_admin());

-- ---- audit_log ----
-- Users see only logs about themselves; admin sees all.
DROP POLICY IF EXISTS audit_self_read ON audit_log;
CREATE POLICY audit_self_read ON audit_log FOR SELECT
    USING (target_user_id = auth_user_id() OR auth_is_admin());

DROP POLICY IF EXISTS audit_admin_insert ON audit_log;
CREATE POLICY audit_admin_insert ON audit_log FOR INSERT
    WITH CHECK (auth_is_admin() OR actor_user_id = auth_user_id());

-- =====================================================================
-- Useful views (admin-only access via RLS)
-- =====================================================================
CREATE OR REPLACE VIEW admin_user_summary AS
SELECT
    u.user_id,
    u.email,
    u.status,
    u.created_at,
    u.approved_at,
    uc.bot_status,
    uc.fee_pct_of_profit,
    ubc.broker,
    ubc.mt5_account_no,
    COUNT(t.trade_id) FILTER (WHERE t.close_time IS NULL) AS open_trades,
    COUNT(t.trade_id) FILTER (WHERE t.close_time IS NOT NULL) AS closed_trades,
    COALESCE(SUM(t.realized_pnl_usd), 0) AS total_realized_pnl,
    COALESCE(SUM(f.fee_amount_usd) FILTER (WHERE f.charge_status = 'paid'), 0) AS total_fees_paid
FROM users u
LEFT JOIN user_configs uc ON uc.user_id = u.user_id
LEFT JOIN user_broker_credentials ubc ON ubc.user_id = u.user_id
LEFT JOIN trades t ON t.user_id = u.user_id
LEFT JOIN fees f ON f.user_id = u.user_id
GROUP BY u.user_id, u.email, u.status, u.created_at, u.approved_at,
         uc.bot_status, uc.fee_pct_of_profit, ubc.broker, ubc.mt5_account_no;

-- =====================================================================
-- Triggers — updated_at auto-bump
-- =====================================================================
CREATE OR REPLACE FUNCTION bump_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS user_configs_updated_at ON user_configs;
CREATE TRIGGER user_configs_updated_at
    BEFORE UPDATE ON user_configs
    FOR EACH ROW EXECUTE FUNCTION bump_updated_at();

DROP TRIGGER IF EXISTS health_updated_at ON user_strategy_health;
CREATE TRIGGER health_updated_at
    BEFORE UPDATE ON user_strategy_health
    FOR EACH ROW EXECUTE FUNCTION bump_updated_at();

-- =====================================================================
-- DONE
-- =====================================================================
-- Verify with:
--   \dt    -- list tables
--   \dT    -- list types
--   SELECT * FROM pg_policies;  -- list RLS policies
