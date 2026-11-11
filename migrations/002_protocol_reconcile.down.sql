DROP TABLE IF EXISTS mandate_consumptions CASCADE;
DROP TABLE IF EXISTS product_references CASCADE;

ALTER TABLE mandates
    DROP COLUMN IF EXISTS mandate_id CASCADE,
    DROP COLUMN IF EXISTS schema_version CASCADE,
    DROP COLUMN IF EXISTS paypal_order_id CASCADE,
    DROP COLUMN IF EXISTS authorized_cap_cents CASCADE,
    DROP COLUMN IF EXISTS allowed_product_categories CASCADE,
    DROP COLUMN IF EXISTS merchant_restrictions CASCADE,
    DROP COLUMN IF EXISTS expected_paypal_payee_id CASCADE,
    DROP COLUMN IF EXISTS issued_at CASCADE,
    DROP COLUMN IF EXISTS nonce CASCADE,
    DROP COLUMN IF EXISTS mandate_digest CASCADE,
    DROP COLUMN IF EXISTS consumed_at CASCADE,
    DROP COLUMN IF EXISTS consumption_tx_id CASCADE;

ALTER TABLE payment_events
    DROP COLUMN IF EXISTS amount_cents CASCADE;

ALTER TABLE offer_snapshots
    DROP COLUMN IF EXISTS price_cents CASCADE,
    DROP COLUMN IF EXISTS offered_price_cents CASCADE;

ALTER TABLE negotiations
    DROP COLUMN IF EXISTS budget_cents CASCADE,
    DROP COLUMN IF EXISTS budget_amount_cents CASCADE;
