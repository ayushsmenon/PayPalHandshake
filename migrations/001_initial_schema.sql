CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS negotiations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shopper_id VARCHAR(128) NOT NULL,
    request_prompt TEXT NOT NULL,
    budget_amount NUMERIC(12, 2) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'OPEN',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_negotiations_budget_positive CHECK (budget_amount >= 0),
    CONSTRAINT chk_negotiations_currency_len CHECK (length(currency) = 3)
);

COMMENT ON TABLE negotiations IS 'Stores shopper intent, budget constraints, currency code, and negotiation lifecycle status';
COMMENT ON COLUMN negotiations.id IS 'Primary key UUID generated via gen_random_uuid()';
COMMENT ON COLUMN negotiations.shopper_id IS 'Shopper identifier or user account reference';
COMMENT ON COLUMN negotiations.request_prompt IS 'Original natural language or structured shopper requirement prompt';
COMMENT ON COLUMN negotiations.budget_amount IS 'Authorized spending ceiling in standard currency decimal units';
COMMENT ON COLUMN negotiations.currency IS 'ISO 4217 3-letter currency code (e.g. USD, INR, EUR)';
COMMENT ON COLUMN negotiations.status IS 'Lifecycle status (e.g. OPEN, NEGOTIATING, AGREED, REJECTED, EXPIRED)';
COMMENT ON COLUMN negotiations.metadata IS 'Flexible unstructured context, shopping constraints, and agent state';
COMMENT ON COLUMN negotiations.created_at IS 'Timestamp of negotiation creation';
COMMENT ON COLUMN negotiations.updated_at IS 'Timestamp of last update (auto-updated by trigger)';

CREATE INDEX IF NOT EXISTS idx_negotiations_shopper_id ON negotiations (shopper_id);
CREATE INDEX IF NOT EXISTS idx_negotiations_status ON negotiations (status);
CREATE INDEX IF NOT EXISTS idx_negotiations_created_at ON negotiations (created_at DESC);

DROP TRIGGER IF EXISTS trg_negotiations_updated_at ON negotiations;
CREATE TRIGGER trg_negotiations_updated_at
    BEFORE UPDATE ON negotiations
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    negotiation_id UUID NOT NULL REFERENCES negotiations(id) ON DELETE CASCADE,
    external_product_id VARCHAR(255),
    title TEXT NOT NULL,
    description TEXT,
    catalog_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE products IS 'Tracks candidate products discovered or evaluated during negotiation';
COMMENT ON COLUMN products.id IS 'Primary key UUID generated via gen_random_uuid()';
COMMENT ON COLUMN products.negotiation_id IS 'Foreign key reference to parent negotiation';
COMMENT ON COLUMN products.external_product_id IS 'External catalog identifier (e.g. Channel3 reference or catalog SKU)';
COMMENT ON COLUMN products.title IS 'Product name or title';
COMMENT ON COLUMN products.description IS 'Optional product description or detailed specification';
COMMENT ON COLUMN products.catalog_metadata IS 'Raw extracted attributes, specifications, verification flags, and brand metadata';
COMMENT ON COLUMN products.created_at IS 'Timestamp when product was recorded';

CREATE INDEX IF NOT EXISTS idx_products_negotiation_id ON products (negotiation_id);
CREATE INDEX IF NOT EXISTS idx_products_external_id ON products (external_product_id);
CREATE INDEX IF NOT EXISTS idx_products_created_at ON products (created_at DESC);

CREATE TABLE IF NOT EXISTS offer_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    negotiation_id UUID NOT NULL REFERENCES negotiations(id) ON DELETE CASCADE,
    product_id UUID REFERENCES products(id) ON DELETE SET NULL,
    merchant_id VARCHAR(255) NOT NULL,
    merchant_name VARCHAR(255),
    offered_price NUMERIC(12, 2) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    terms_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    offer_hash VARCHAR(64) NOT NULL,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_offer_snapshots_price_positive CHECK (offered_price >= 0),
    CONSTRAINT chk_offer_snapshots_currency_len CHECK (length(currency) = 3)
);

COMMENT ON TABLE offer_snapshots IS 'Immutable snapshot of merchant, price, currency, and terms frozen at agreement time';
COMMENT ON COLUMN offer_snapshots.id IS 'Primary key UUID generated via gen_random_uuid()';
COMMENT ON COLUMN offer_snapshots.negotiation_id IS 'Foreign key reference to parent negotiation';
COMMENT ON COLUMN offer_snapshots.product_id IS 'Optional foreign key reference to associated product';
COMMENT ON COLUMN offer_snapshots.merchant_id IS 'Merchant unique identifier';
COMMENT ON COLUMN offer_snapshots.merchant_name IS 'Merchant commercial or display name';
COMMENT ON COLUMN offer_snapshots.offered_price IS 'Locked price amount in currency decimal units';
COMMENT ON COLUMN offer_snapshots.currency IS 'ISO 4217 3-letter currency code';
COMMENT ON COLUMN offer_snapshots.terms_payload IS 'Snapshot of shipping, itemized fees, carrier terms, and seller commitments';
COMMENT ON COLUMN offer_snapshots.offer_hash IS 'SHA-256 digest of offer details for cryptographic immutability checks';
COMMENT ON COLUMN offer_snapshots.snapshot_at IS 'Point-in-time timestamp when offer was evaluated and frozen';

CREATE INDEX IF NOT EXISTS idx_offer_snapshots_negotiation_id ON offer_snapshots (negotiation_id);
CREATE INDEX IF NOT EXISTS idx_offer_snapshots_product_id ON offer_snapshots (product_id);
CREATE INDEX IF NOT EXISTS idx_offer_snapshots_merchant_id ON offer_snapshots (merchant_id);
CREATE INDEX IF NOT EXISTS idx_offer_snapshots_offer_hash ON offer_snapshots (offer_hash);
CREATE INDEX IF NOT EXISTS idx_offer_snapshots_snapshot_at ON offer_snapshots (snapshot_at DESC);

CREATE TABLE IF NOT EXISTS mandates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    negotiation_id UUID NOT NULL REFERENCES negotiations(id) ON DELETE CASCADE,
    public_key VARCHAR(128) NOT NULL,
    signature TEXT NOT NULL,
    mandate_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_mandates_expiry_valid CHECK (expires_at > created_at)
);

COMMENT ON TABLE mandates IS 'Signed shopper spending authorizations and bounded agreements with Ed25519 signatures';
COMMENT ON COLUMN mandates.id IS 'Primary key UUID generated via gen_random_uuid()';
COMMENT ON COLUMN mandates.negotiation_id IS 'Foreign key reference to parent negotiation';
COMMENT ON COLUMN mandates.public_key IS 'Shopper/Agent Ed25519 Public Key in hex/base64 (NEVER PRIVATE KEYS)';
COMMENT ON COLUMN mandates.signature IS 'Ed25519 signature payload over bounded mandate agreement';
COMMENT ON COLUMN mandates.mandate_payload IS 'Payload holding max_cap, merchant constraints, allowed operations, and product bounds';
COMMENT ON COLUMN mandates.expires_at IS 'Expiration timestamp after which the mandate cannot be consumed';
COMMENT ON COLUMN mandates.status IS 'Mandate lifecycle state (e.g. ACTIVE, CONSUMED, REVOKED, EXPIRED)';
COMMENT ON COLUMN mandates.created_at IS 'Issuance timestamp of the signed mandate';

CREATE INDEX IF NOT EXISTS idx_mandates_negotiation_id ON mandates (negotiation_id);
CREATE INDEX IF NOT EXISTS idx_mandates_public_key ON mandates (public_key);
CREATE INDEX IF NOT EXISTS idx_mandates_status ON mandates (status);
CREATE INDEX IF NOT EXISTS idx_mandates_expires_at ON mandates (expires_at);

CREATE TABLE IF NOT EXISTS verification_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    negotiation_id UUID REFERENCES negotiations(id) ON DELETE CASCADE,
    gate_name VARCHAR(64) NOT NULL,
    decision VARCHAR(16) NOT NULL,
    passed_checks JSONB NOT NULL DEFAULT '[]'::jsonb,
    blocked_checks JSONB NOT NULL DEFAULT '[]'::jsonb,
    reason_details TEXT,
    manifest_digest VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_verification_events_decision CHECK (decision IN ('PASSED', 'BLOCKED'))
);

COMMENT ON TABLE verification_events IS 'Immutable audit log of all deterministic gate evaluations';
COMMENT ON COLUMN verification_events.id IS 'Primary key UUID generated via gen_random_uuid()';
COMMENT ON COLUMN verification_events.negotiation_id IS 'Optional foreign key reference to associated negotiation';
COMMENT ON COLUMN verification_events.gate_name IS 'Name of gate evaluated (e.g. PATH_CHECK, AST_INSPECTION, PRICE_CAP_GATE, CARRIER_RATE_GATE)';
COMMENT ON COLUMN verification_events.decision IS 'Deterministic gate verdict: PASSED or BLOCKED';
COMMENT ON COLUMN verification_events.passed_checks IS 'JSON list of check rules or assertions that passed';
COMMENT ON COLUMN verification_events.blocked_checks IS 'JSON list of check rules or assertions that failed/blocked';
COMMENT ON COLUMN verification_events.reason_details IS 'Detailed human/machine-readable diagnostic failure or pass explanation';
COMMENT ON COLUMN verification_events.manifest_digest IS 'SHA-256 digest of checked manifest or payload for cryptographic provenance';
COMMENT ON COLUMN verification_events.created_at IS 'Timestamp of evaluation';

CREATE INDEX IF NOT EXISTS idx_verification_events_negotiation_id ON verification_events (negotiation_id);
CREATE INDEX IF NOT EXISTS idx_verification_events_gate_name ON verification_events (gate_name);
CREATE INDEX IF NOT EXISTS idx_verification_events_decision ON verification_events (decision);
CREATE INDEX IF NOT EXISTS idx_verification_events_manifest_digest ON verification_events (manifest_digest);
CREATE INDEX IF NOT EXISTS idx_verification_events_created_at ON verification_events (created_at DESC);

CREATE TABLE IF NOT EXISTS payment_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    negotiation_id UUID REFERENCES negotiations(id) ON DELETE CASCADE,
    idempotency_key VARCHAR(128) NOT NULL,
    paypal_order_id VARCHAR(128),
    paypal_authorization_id VARCHAR(128),
    paypal_capture_id VARCHAR(128),
    paypal_payout_batch_id VARCHAR(128),
    event_type VARCHAR(64) NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    status VARCHAR(32) NOT NULL DEFAULT 'CREATED',
    raw_response_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_payment_events_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT chk_payment_events_amount_positive CHECK (amount >= 0),
    CONSTRAINT chk_payment_events_currency_len CHECK (length(currency) = 3)
);

COMMENT ON TABLE payment_events IS 'Immutable transaction log for PayPal Orders v2, Authorizations, Captures, and Payouts';
COMMENT ON COLUMN payment_events.id IS 'Primary key UUID generated via gen_random_uuid()';
COMMENT ON COLUMN payment_events.negotiation_id IS 'Optional foreign key reference to associated negotiation';
COMMENT ON COLUMN payment_events.idempotency_key IS 'Unique PayPal-Request-Id preventing duplicate runs and replay attacks';
COMMENT ON COLUMN payment_events.paypal_order_id IS 'PayPal Order ID reference (v2/checkout/orders)';
COMMENT ON COLUMN payment_events.paypal_authorization_id IS 'PayPal Authorization ID reference';
COMMENT ON COLUMN payment_events.paypal_capture_id IS 'PayPal Capture ID reference';
COMMENT ON COLUMN payment_events.paypal_payout_batch_id IS 'PayPal Payout batch identifier';
COMMENT ON COLUMN payment_events.event_type IS 'Transaction phase (e.g. ORDER_CREATED, AUTHORIZED, PARTIAL_CAPTURE, PAYOUT_SUBMITTED)';
COMMENT ON COLUMN payment_events.amount IS 'Transaction amount in standard decimal currency units';
COMMENT ON COLUMN payment_events.currency IS 'ISO 4217 3-letter currency code';
COMMENT ON COLUMN payment_events.status IS 'Transaction status (e.g. CREATED, CAPTURING, COMPLETED, FAILED, RECONCILIATION_REQUIRED)';
COMMENT ON COLUMN payment_events.raw_response_summary IS 'Sanitized PayPal API response summary (NO credentials or auth headers)';
COMMENT ON COLUMN payment_events.created_at IS 'Timestamp of the payment event';

CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_events_idempotency_key ON payment_events (idempotency_key);
CREATE INDEX IF NOT EXISTS idx_payment_events_negotiation_id ON payment_events (negotiation_id);
CREATE INDEX IF NOT EXISTS idx_payment_events_paypal_order_id ON payment_events (paypal_order_id);
CREATE INDEX IF NOT EXISTS idx_payment_events_authorization_id ON payment_events (paypal_authorization_id);
CREATE INDEX IF NOT EXISTS idx_payment_events_capture_id ON payment_events (paypal_capture_id);
CREATE INDEX IF NOT EXISTS idx_payment_events_event_type ON payment_events (event_type);
CREATE INDEX IF NOT EXISTS idx_payment_events_status ON payment_events (status);
CREATE INDEX IF NOT EXISTS idx_payment_events_created_at ON payment_events (created_at DESC);
