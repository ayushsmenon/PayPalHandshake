CREATE EXTENSION IF NOT EXISTS "pgcrypto";

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_mandates_mandate_id'
    ) THEN
        ALTER TABLE mandates ADD CONSTRAINT uq_mandates_mandate_id UNIQUE (mandate_id);
    END IF;
END $$;

ALTER TABLE negotiations
    ADD COLUMN IF NOT EXISTS currency_code VARCHAR(3);

UPDATE negotiations
SET currency_code = currency
WHERE currency_code IS NULL AND currency IS NOT NULL;

ALTER TABLE offer_snapshots
    ADD COLUMN IF NOT EXISTS currency_code VARCHAR(3);

UPDATE offer_snapshots
SET currency_code = currency
WHERE currency_code IS NULL AND currency IS NOT NULL;

ALTER TABLE mandates
    ADD COLUMN IF NOT EXISTS currency VARCHAR(3) DEFAULT 'USD',
    ADD COLUMN IF NOT EXISTS currency_code VARCHAR(3) DEFAULT 'USD';

UPDATE mandates
SET currency_code = COALESCE(currency, 'USD')
WHERE currency_code IS NULL;

ALTER TABLE payment_events
    ADD COLUMN IF NOT EXISTS currency_code VARCHAR(3);

UPDATE payment_events
SET currency_code = currency
WHERE currency_code IS NULL AND currency IS NOT NULL;

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

ALTER TABLE mandate_consumptions
    DROP CONSTRAINT IF EXISTS mandate_consumptions_mandate_id_fkey;

ALTER TABLE mandate_consumptions
    ALTER COLUMN mandate_id TYPE VARCHAR(128) USING mandate_id::text;

ALTER TABLE mandate_consumptions
    ADD COLUMN IF NOT EXISTS transaction_id VARCHAR(128);

UPDATE mandate_consumptions
SET transaction_id = idempotency_key
WHERE transaction_id IS NULL;

ALTER TABLE mandate_consumptions
    ALTER COLUMN transaction_id SET NOT NULL;

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
