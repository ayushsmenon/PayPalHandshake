# PostgreSQL Database Schema & Migrations

This directory contains the initial standalone PostgreSQL database schema definitions and migration scripts for the **PayPal Handshake** negotiation and verification system.

---

## Strict Constraints & Security Guardrails

This migration strictly enforces the following boundaries:
1. **Isolated Schema:** Does NOT touch or modify any existing core service code or pipelines.
2. **Process Integrity:** Does NOT modify existing Process A, Process B, or Process C logic.
3. **API & Crypto Integrity:** No alterations to existing API schemas, request/response structures, or Ed25519 signing/verification algorithms.
4. **Zero-Secret Safety:**
   - **NO** PayPal client secrets, API tokens, passwords, or credentials are designed or stored.
   - **NO** Ed25519 private keys are stored. Only public key identifiers (`public_key_reference`) and cryptographic signatures are retained.
   - Only non-sensitive reference tokens (e.g. `paypal_order_id`, `transaction_reference`) are recorded for auditability.
5. **Live Flow Isolation:** Standalone DDL and models only; not wired into live production execution.

---

## Schema Overview

The initial schema consists of 6 tables designed for high auditability, deterministic traceability, and strict relational integrity:

```
                  ┌──────────────────────┐
                  │     negotiations     │
                  └──────────┬───────────┘
                             │
     ┌───────────────────────┼───────────────────────┬───────────────────────┬───────────────────────┐
     │ 1:N                   │ 1:N                   │ 1:N                   │ 1:N                   │ 1:N
     ▼                       ▼                       ▼                       ▼                       ▼
┌──────────┐           ┌────────────────┐      ┌──────────┐           ┌────────────────────┐   ┌────────────────┐
│ products │◄──(0..1)──┤ offer_snapshots│      │ mandates │           │verification_events │   │ payment_events │
└──────────┘           └────────────────┘      └──────────┘           └────────────────────┘   └────────────────┘
```

### 1. `negotiations`
Tracks shopper negotiation requests, authorized budget ceilings, currency, and lifecycle states.
- **Primary Key:** `id` (`UUID`)
- **Key Fields:** `negotiation_reference` (unique, e.g. `HS-2048`), `shopper_request` (`JSONB`), `budget_amount_cents` (`BIGINT`), `budget_amount` (`NUMERIC(12, 2)`), `currency` (`VARCHAR(3)`), `status` (`VARCHAR(50)`), `metadata` (`JSONB`)
- **Timestamps:** `created_at`, `updated_at` (auto-updated via trigger)
- **Indexes:** `idx_negotiations_reference`, `idx_negotiations_status`, `idx_negotiations_created_at`

### 2. `products`
Tracks candidate products discovered for a negotiation via catalog integrations (e.g. Channel3).
- **Primary Key:** `id` (`UUID`)
- **Foreign Key:** `negotiation_id -> negotiations(id) ON DELETE CASCADE`
- **Key Fields:** `product_identifier` (`VARCHAR(255)`), `title` (`VARCHAR(500)`), `metadata` (`JSONB`), `discovered_at` (`TIMESTAMPTZ`)
- **Timestamps:** `created_at`, `updated_at`
- **Indexes:** `idx_products_negotiation_id`, `idx_products_identifier`, `idx_products_discovered_at`

### 3. `offer_snapshots`
Records point-in-time merchant offers and pricing selected during negotiation.
- **Primary Key:** `id` (`UUID`)
- **Foreign Keys:**
  - `negotiation_id -> negotiations(id) ON DELETE CASCADE`
  - `product_id -> products(id) ON DELETE SET NULL`
- **Key Fields:** `merchant_id` (`VARCHAR(255)`), `merchant_name` (`VARCHAR(255)`), `price_cents` (`BIGINT`), `price` (`NUMERIC(12, 2)`), `currency` (`VARCHAR(3)`), `offer_url` (`TEXT`), `snapshot_payload` (`JSONB`), `captured_at` (`TIMESTAMPTZ`)
- **Timestamps:** `created_at`
- **Indexes:** `idx_offer_snapshots_negotiation_id`, `idx_offer_snapshots_product_id`, `idx_offer_snapshots_merchant_id`, `idx_offer_snapshots_captured_at`

### 4. `mandates`
Maintains cryptographically signed mandates authorized by the shopper.
- **Primary Key:** `id` (`UUID`)
- **Foreign Key:** `negotiation_id -> negotiations(id) ON DELETE CASCADE`
- **Key Fields:** `mandate_reference` (`VARCHAR(100)` unique), `schema_version` (`VARCHAR(20)`), `paypal_auth_id` (`VARCHAR(100)`), `authorized_cap_cents` (`BIGINT`), `currency` (`VARCHAR(3)`), `authorized_merchant_id` (`VARCHAR(255)`), `allowed_product_identifiers` (`JSONB`), `public_key_reference` (`VARCHAR(255)` - **NO private keys**), `signature` (`TEXT`), `status` (`VARCHAR(50)`), `issued_at` (`TIMESTAMPTZ`), `expires_at` (`TIMESTAMPTZ`)
- **Constraints:** `expires_at > issued_at`, `authorized_cap_cents >= 0`
- **Timestamps:** `created_at`, `updated_at`
- **Indexes:** `idx_mandates_negotiation_id`, `idx_mandates_reference`, `idx_mandates_status`, `idx_mandates_expires_at`, `idx_mandates_public_key_ref`

### 5. `verification_events`
Audit trail documenting each security gate check, verdicts, and failure reasons.
- **Primary Key:** `id` (`UUID`)
- **Foreign Key:** `negotiation_id -> negotiations(id) ON DELETE CASCADE`
- **Key Fields:** `check_name` (`VARCHAR(100)`), `check_type` (`VARCHAR(100)`), `gate_decision` (`VARCHAR(20)` CHECK `IN ('PASS', 'BLOCK')`), `reason_details` (`JSONB`), `timestamp` (`TIMESTAMPTZ`)
- **Timestamps:** `created_at`
- **Indexes:** `idx_verification_events_negotiation_id`, `idx_verification_events_gate_decision`, `idx_verification_events_check_name`, `idx_verification_events_timestamp`

### 6. `payment_events`
Audit trail recording PayPal order lifecycle events and transaction statuses.
- **Primary Key:** `id` (`UUID`)
- **Foreign Key:** `negotiation_id -> negotiations(id) ON DELETE CASCADE`
- **Key Fields:** `paypal_order_id` (`VARCHAR(100)`), `auth_status` (`VARCHAR(50)`), `amount_cents` (`BIGINT`), `amount` (`NUMERIC(12, 2)`), `currency` (`VARCHAR(3)`), `transaction_reference` (`VARCHAR(255)` - **NO credentials**), `event_payload` (`JSONB`), `timestamp` (`TIMESTAMPTZ`)
- **Timestamps:** `created_at`
- **Indexes:** `idx_payment_events_negotiation_id`, `idx_payment_events_paypal_order_id`, `idx_payment_events_auth_status`, `idx_payment_events_timestamp`

---

## File Manifest

| File | Purpose |
|---|---|
| `migrations/001_initial_schema.sql` | Up migration: creates extension, triggers, tables, constraints, and indexes |
| `migrations/001_initial_schema.down.sql` | Down migration: drops tables in reverse dependency order and trigger function |
| `migrations/models.py` | Standalone SQLAlchemy 2.0 ORM models and Pydantic v2 schemas |
| `migrations/test_models.py` | Verification test validating model compilation and entity schema definitions |
| `migrations/README.md` | Schema documentation and execution guide |

---

## Migration Execution

### Up Migration
Using standard `psql`:
```bash
psql -U <db_user> -d <db_name> -f migrations/001_initial_schema.sql
```

### Rollback (Down Migration)
```bash
psql -U <db_user> -d <db_name> -f migrations/001_initial_schema.down.sql
```

### Python Verification
To verify the standalone Python models and schemas:
```powershell
$env:PYTHONPATH="."
py -m migrations.test_models
```
