-- ============================================================================
-- Migration: 002_protocol_reconcile.sql
-- Description: Additive Protocol Reconciliation for PayPal Agent Platform
-- Milestone: mandate.v2, product-reference.v1, integer minor units & replay prevention
--
-- STRICT INVARIANTS ENFORCED:
-- 1. Non-destructive additive migration: preserves existing data & schema.
-- 2. Minor units: Integer cents (BIGINT) added across all monetary fields.
-- 3. mandate.v2: Explicit schema_version, cap_cents, category/merchant constraints, nonce, digest.
-- 4. product-reference.v1: Canonical product reference storage with SHA-256 digests.
-- 5. Replay prevention: Durable mandate consumption tracking with unique constraints.
-- 6. Zero secrets: NO private keys, passwords, client secrets, or auth tokens.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Integer Minor Units (Monetary precision in cents / minor currency units)
-- ----------------------------------------------------------------------------

-- negotiations: add budget_cents & budget_amount_cents
ALTER TABLE negotiations
    ADD COLUMN IF NOT EXISTS budget_cents BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS budget_amount_cents BIGINT DEFAULT 0;

-- Backfill integer cents from existing decimal budget_amount if present
UPDATE negotiations
SET budget_cents = ROUND(budget_amount * 100)::BIGINT,
    budget_amount_cents = ROUND(budget_amount * 100)::BIGINT
WHERE budget_cents = 0 AND budget_amount IS NOT NULL;

-- Add check constraint for non-negative minor units
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_negotiations_budget_cents_positive'
    ) THEN
        ALTER TABLE negotiations
            ADD CONSTRAINT chk_negotiations_budget_cents_positive CHECK (budget_cents >= 0);
    END IF;
END $$;

-- offer_snapshots: add price_cents & offered_price_cents
ALTER TABLE offer_snapshots
    ADD COLUMN IF NOT EXISTS price_cents BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS offered_price_cents BIGINT DEFAULT 0;

UPDATE offer_snapshots
SET price_cents = ROUND(offered_price * 100)::BIGINT,
    offered_price_cents = ROUND(offered_price * 100)::BIGINT
WHERE price_cents = 0 AND offered_price IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_offer_snapshots_price_cents_positive'
    ) THEN
        ALTER TABLE offer_snapshots
            ADD CONSTRAINT chk_offer_snapshots_price_cents_positive CHECK (price_cents >= 0);
    END IF;
END $$;

-- payment_events: add amount_cents
ALTER TABLE payment_events
    ADD COLUMN IF NOT EXISTS amount_cents BIGINT DEFAULT 0;

UPDATE payment_events
SET amount_cents = ROUND(amount * 100)::BIGINT
WHERE amount_cents = 0 AND amount IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_payment_events_amount_cents_positive'
    ) THEN
        ALTER TABLE payment_events
            ADD CONSTRAINT chk_payment_events_amount_cents_positive CHECK (amount_cents >= 0);
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 2. mandate.v2 Protocol Alignment & Replay Prevention
-- ----------------------------------------------------------------------------

ALTER TABLE mandates
    ADD COLUMN IF NOT EXISTS mandate_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS schema_version VARCHAR(32) DEFAULT 'mandate.v2',
    ADD COLUMN IF NOT EXISTS paypal_order_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS authorized_cap_cents BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS allowed_product_categories JSONB DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS merchant_restrictions JSONB DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS expected_paypal_payee_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS issued_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN IF NOT EXISTS nonce VARCHAR(128),
    ADD COLUMN IF NOT EXISTS mandate_digest VARCHAR(64),
    ADD COLUMN IF NOT EXISTS consumed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS consumption_tx_id VARCHAR(128);

-- Backfill mandate_id from id if empty
UPDATE mandates
SET mandate_id = 'mnd_' || id::text
WHERE mandate_id IS NULL;

-- Backfill issued_at from created_at if empty
UPDATE mandates
SET issued_at = created_at
WHERE issued_at IS NULL;

-- Unique index on mandate_id for fast lookup
CREATE UNIQUE INDEX IF NOT EXISTS idx_mandates_mandate_id ON mandates (mandate_id);
-- Unique index on mandate_digest to prevent duplicate/replayed mandate registration
CREATE UNIQUE INDEX IF NOT EXISTS idx_mandates_mandate_digest ON mandates (mandate_digest) WHERE mandate_digest IS NOT NULL;
-- Index on consumption timestamp to track active vs consumed
CREATE INDEX IF NOT EXISTS idx_mandates_consumed_at ON mandates (consumed_at);

-- ----------------------------------------------------------------------------
-- 3. product-reference.v1 Table
-- Purpose: Canonical, signed product references (Channel3 verification & price lock)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS product_references (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    negotiation_id UUID NOT NULL REFERENCES negotiations(id) ON DELETE CASCADE,
    product_id UUID REFERENCES products(id) ON DELETE SET NULL,
    schema_version VARCHAR(32) NOT NULL DEFAULT 'product-reference.v1',
    channel3_product_id VARCHAR(255) NOT NULL,
    offer_url TEXT NOT NULL,
    merchant_domain VARCHAR(255) NOT NULL,
    price_minor BIGINT NOT NULL CHECK (price_minor >= 0),
    currency VARCHAR(3) NOT NULL,
    availability VARCHAR(64) NOT NULL,
    condition VARCHAR(64),
    canonical_digest VARCHAR(64) NOT NULL,
    signature_b64 TEXT NOT NULL,
    public_key_b64 TEXT,
    issued_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_product_references_expiry CHECK (expires_at > issued_at),
    CONSTRAINT chk_product_references_currency CHECK (length(currency) = 3)
);

COMMENT ON TABLE product_references IS 'Canonical, signed product references conforming to product-reference.v1';
COMMENT ON COLUMN product_references.id IS 'Primary key UUID';
COMMENT ON COLUMN product_references.negotiation_id IS 'Parent negotiation reference';
COMMENT ON COLUMN product_references.channel3_product_id IS 'Channel3 external product ID';
COMMENT ON COLUMN product_references.price_minor IS 'Price in minor currency units (integer cents)';
COMMENT ON COLUMN product_references.canonical_digest IS 'SHA-256 digest of canonical product reference bytes';
COMMENT ON COLUMN product_references.signature_b64 IS 'Base64 Ed25519 signature payload';
COMMENT ON COLUMN product_references.public_key_b64 IS 'Base64 Ed25519 public key (NO private keys)';

CREATE INDEX IF NOT EXISTS idx_product_references_negotiation_id ON product_references (negotiation_id);
CREATE INDEX IF NOT EXISTS idx_product_references_channel3_id ON product_references (channel3_product_id);
CREATE INDEX IF NOT EXISTS idx_product_references_digest ON product_references (canonical_digest);
CREATE INDEX IF NOT EXISTS idx_product_references_expires_at ON product_references (expires_at);

-- ----------------------------------------------------------------------------
-- 4. Durable Mandate Consumption Tracking (Strict Replay Prevention)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mandate_consumptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mandate_id UUID NOT NULL REFERENCES mandates(id) ON DELETE CASCADE,
    mandate_digest VARCHAR(64) NOT NULL UNIQUE,
    consumed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    paypal_order_id VARCHAR(128),
    payment_event_id UUID REFERENCES payment_events(id) ON DELETE SET NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

COMMENT ON TABLE mandate_consumptions IS 'Append-only audit log enforcing strict single-use mandate consumption replay prevention';
COMMENT ON COLUMN mandate_consumptions.mandate_digest IS 'Unique mandate digest ensuring a mandate cannot be consumed more than once';
COMMENT ON COLUMN mandate_consumptions.idempotency_key IS 'Unique idempotency key tying consumption to payment request';

CREATE UNIQUE INDEX IF NOT EXISTS idx_mandate_consumptions_digest ON mandate_consumptions (mandate_digest);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mandate_consumptions_idempotency ON mandate_consumptions (idempotency_key);
CREATE INDEX IF NOT EXISTS idx_mandate_consumptions_mandate_id ON mandate_consumptions (mandate_id);
