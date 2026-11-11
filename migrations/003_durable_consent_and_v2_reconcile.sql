-- ============================================================================
-- Migration: 003_durable_consent_and_v2_reconcile.sql
-- Description: Durable Consent Evidence, Protocol v2 Alignment, and Strict Consumption Safeguards
-- Platform: PayPal Handshake (Autonomous Agent Commerce & Verification)
-- Target Database: PostgreSQL 16
--
-- STRICT INVARIANTS ENFORCED:
-- 1. SIGNED DATA IS AUTHORITATIVE: No synthetic digests or invented hashes.
-- 2. FAIL CLOSED: Missing consent or digest verification prevents execution.
-- 3. MONEY IN INTEGER MINOR UNITS: All monetary columns are BIGINT cents accompanied by currency_code.
-- 4. IDENTITY DISTINCTIONS: Channel3 product IDs, merchant domains, display names, and payee IDs kept distinct.
-- 5. ZERO SECRET LEAKAGE: No client secrets, access tokens, or private Ed25519 signing keys.
-- 6. DURABLE CONSENT: Single-use consent claim per mandate enforced via UNIQUE(mandate_id).
-- ============================================================================

-- Ensure pgcrypto extension is available
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ----------------------------------------------------------------------------
-- 1. Ensure Unique Constraint on mandates(mandate_id) for Foreign Key References
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_mandates_mandate_id'
    ) THEN
        ALTER TABLE mandates ADD CONSTRAINT uq_mandates_mandate_id UNIQUE (mandate_id);
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 2. Ensure currency_code and integer minor unit columns across all core tables
-- ----------------------------------------------------------------------------

-- negotiations: currency_code
ALTER TABLE negotiations
    ADD COLUMN IF NOT EXISTS currency_code VARCHAR(3);

UPDATE negotiations
SET currency_code = currency
WHERE currency_code IS NULL AND currency IS NOT NULL;

-- offer_snapshots: currency_code
ALTER TABLE offer_snapshots
    ADD COLUMN IF NOT EXISTS currency_code VARCHAR(3);

UPDATE offer_snapshots
SET currency_code = currency
WHERE currency_code IS NULL AND currency IS NOT NULL;

-- mandates: currency_code
ALTER TABLE mandates
    ADD COLUMN IF NOT EXISTS currency_code VARCHAR(3);

UPDATE mandates
SET currency_code = currency
WHERE currency_code IS NULL AND currency IS NOT NULL;

-- payment_events: currency_code
ALTER TABLE payment_events
    ADD COLUMN IF NOT EXISTS currency_code VARCHAR(3);

UPDATE payment_events
SET currency_code = currency
WHERE currency_code IS NULL AND currency IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 3. Table: consent_evidence
-- Purpose: Durable, single-use consent evidence bound to a signed mandate.
-- Enforces: Exactly ONE valid consent claim per mandate via UNIQUE(mandate_id).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS consent_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_id VARCHAR(128) NOT NULL,
    mandate_id VARCHAR(128) NOT NULL,
    mandate_digest VARCHAR(64) NOT NULL,
    negotiation_id UUID NOT NULL REFERENCES negotiations(id) ON DELETE CASCADE,
    shopper_id VARCHAR(128) NOT NULL,
    approval_action VARCHAR(64) NOT NULL DEFAULT 'AUTHORIZE_AND_MANDATE',
    evidence_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    approved_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_consent_evidence_evidence_id UNIQUE (evidence_id),
    CONSTRAINT uq_consent_evidence_mandate_id UNIQUE (mandate_id),
    CONSTRAINT fk_consent_evidence_mandate_id FOREIGN KEY (mandate_id) REFERENCES mandates(mandate_id) ON DELETE CASCADE
);

COMMENT ON TABLE consent_evidence IS 'Durable, single-use shopper consent evidence contract';
COMMENT ON COLUMN consent_evidence.evidence_id IS 'Unique identifier from the server-controlled consent event';
COMMENT ON COLUMN consent_evidence.mandate_id IS 'Foreign key reference to mandates(mandate_id); exactly 1 consent per mandate';
COMMENT ON COLUMN consent_evidence.mandate_digest IS 'Authoritative SHA-256 digest of the signed mandate.v2';
COMMENT ON COLUMN consent_evidence.negotiation_id IS 'Parent negotiation reference';
COMMENT ON COLUMN consent_evidence.shopper_id IS 'Shopper or payer account identifier';
COMMENT ON COLUMN consent_evidence.approval_action IS 'Explicit shopper approval action taken (e.g. AUTHORIZE_AND_MANDATE)';
COMMENT ON COLUMN consent_evidence.evidence_payload IS 'Serialized cryptographic consent metadata and constraints';
COMMENT ON COLUMN consent_evidence.approved_at IS 'Timestamp when the shopper authorized the mandate';

CREATE INDEX IF NOT EXISTS idx_consent_evidence_mandate_lookup ON consent_evidence (mandate_id, mandate_digest);
CREATE INDEX IF NOT EXISTS idx_consent_evidence_negotiation ON consent_evidence (negotiation_id);
CREATE INDEX IF NOT EXISTS idx_consent_evidence_shopper ON consent_evidence (shopper_id);

-- ----------------------------------------------------------------------------
-- 4. Table: product_references (Protocol v2 Alignment)
-- Purpose: Canonical, signed product references supporting v1 and v2 protocols.
-- ----------------------------------------------------------------------------
ALTER TABLE product_references
    ADD COLUMN IF NOT EXISTS protocol_version VARCHAR(16) NOT NULL DEFAULT 'v1',
    ADD COLUMN IF NOT EXISTS canonical_category VARCHAR(128),
    ADD COLUMN IF NOT EXISTS registry_version VARCHAR(64),
    ADD COLUMN IF NOT EXISTS canonical_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS digest VARCHAR(64),
    ADD COLUMN IF NOT EXISTS signature TEXT,
    ADD COLUMN IF NOT EXISTS public_key VARCHAR(128),
    ADD COLUMN IF NOT EXISTS price_cents BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS currency_code VARCHAR(3);

-- Backfill v1 fields into standard v2-aligned columns
UPDATE product_references
SET digest = canonical_digest
WHERE digest IS NULL AND canonical_digest IS NOT NULL;

UPDATE product_references
SET signature = signature_b64
WHERE signature IS NULL AND signature_b64 IS NOT NULL;

UPDATE product_references
SET public_key = public_key_b64
WHERE public_key IS NULL AND public_key_b64 IS NOT NULL;

UPDATE product_references
SET price_cents = price_minor
WHERE price_cents = 0 AND price_minor IS NOT NULL;

UPDATE product_references
SET currency_code = currency
WHERE currency_code IS NULL AND currency IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_product_references_v2_category'
    ) THEN
        ALTER TABLE product_references
            ADD CONSTRAINT chk_product_references_v2_category
            CHECK (protocol_version != 'v2' OR canonical_category IS NOT NULL);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_product_references_version ON product_references (protocol_version);
CREATE INDEX IF NOT EXISTS idx_product_references_category ON product_references (canonical_category);

-- ----------------------------------------------------------------------------
-- 5. Table: mandate_consumptions (Consumption Safeguards)
-- Ensure mandate_id VARCHAR(128) references mandates(mandate_id) and transaction_id exists.
-- ----------------------------------------------------------------------------

-- Drop old UUID foreign key if present
ALTER TABLE mandate_consumptions
    DROP CONSTRAINT IF EXISTS mandate_consumptions_mandate_id_fkey;

-- Change mandate_id type to VARCHAR(128)
ALTER TABLE mandate_consumptions
    ALTER COLUMN mandate_id TYPE VARCHAR(128) USING mandate_id::text;

-- Add transaction_id column
ALTER TABLE mandate_consumptions
    ADD COLUMN IF NOT EXISTS transaction_id VARCHAR(128);

UPDATE mandate_consumptions
SET transaction_id = idempotency_key
WHERE transaction_id IS NULL;

ALTER TABLE mandate_consumptions
    ALTER COLUMN transaction_id SET NOT NULL;

-- Bind foreign key to mandates(mandate_id)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_mandate_consumptions_mandate_id'
    ) THEN
        ALTER TABLE mandate_consumptions
            ADD CONSTRAINT fk_mandate_consumptions_mandate_id
            FOREIGN KEY (mandate_id) REFERENCES mandates(mandate_id) ON DELETE CASCADE;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_mandate_consumptions_tx_id ON mandate_consumptions (transaction_id);
