-- ============================================================================
-- Migration Rollback: 001_initial_schema.down.sql
-- Description: Reverts the initial PostgreSQL database schema
-- ============================================================================

-- Drop tables in reverse dependency order
DROP TABLE IF EXISTS payment_events CASCADE;
DROP TABLE IF EXISTS verification_events CASCADE;
DROP TABLE IF EXISTS mandates CASCADE;
DROP TABLE IF EXISTS offer_snapshots CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS negotiations CASCADE;

-- Drop trigger function
DROP FUNCTION IF EXISTS update_updated_at_column() CASCADE;
