-- ============================================================================
-- Migration Rollback: 003_durable_consent_and_v2_reconcile.down.sql
-- Description: Reverts consent_evidence, product_references v2 columns, and consumption safeguards
-- ============================================================================

-- Drop consent_evidence table
DROP TABLE IF EXISTS consent_evidence CASCADE;

-- Revert mandate_consumptions
ALTER TABLE mandate_consumptions
    DROP CONSTRAINT IF EXISTS fk_mandate_consumptions_mandate_id,
    DROP COLUMN IF EXISTS transaction_id;

-- Revert product_references v2 columns
ALTER TABLE product_references
    DROP CONSTRAINT IF EXISTS chk_product_references_v2_category,
    DROP COLUMN IF EXISTS protocol_version,
    DROP COLUMN IF EXISTS canonical_category,
    DROP COLUMN IF EXISTS registry_version,
    DROP COLUMN IF EXISTS canonical_payload,
    DROP COLUMN IF EXISTS digest,
    DROP COLUMN IF EXISTS signature,
    DROP COLUMN IF EXISTS public_key,
    DROP COLUMN IF EXISTS price_cents,
    DROP COLUMN IF EXISTS currency_code;

-- Revert currency_code additions
ALTER TABLE payment_events DROP COLUMN IF EXISTS currency_code;
ALTER TABLE mandates DROP COLUMN IF EXISTS currency_code;
ALTER TABLE offer_snapshots DROP COLUMN IF EXISTS currency_code;
ALTER TABLE negotiations DROP COLUMN IF EXISTS currency_code;

-- Drop unique constraint on mandates(mandate_id)
ALTER TABLE mandates DROP CONSTRAINT IF EXISTS uq_mandates_mandate_id;
