# Database Service Contract & Persistence Architecture (PayPal Handshake)

## 1. System Vision & Foundational Invariants

> **Core Platform Promise:** *"Nothing is charged unless the final deal matches exactly what the shopper approved."*

The PayPal Handshake database persistence layer provides an immutable, audit-verifiable, and replay-resistant foundation for agentic commerce. The architecture strictly enforces bounded separation of responsibilities:

1. **Signed Data is Authoritative:** Database records never confer protocol validity to unsigned, unverified, or tampered artifacts.
2. **Zero Tolerance for Synthetic Fallbacks:** All synthetic fallback digests (e.g. `digest_<mandate_id>`) are purged. Missing, invalid, or empty digests fail closed immediately.
3. **Fail Closed:** Missing consent, identity, mandate, or verification evidence blocks execution and propagates errors. Database exceptions are never swallowed or converted into `False`/permission to proceed.
4. **Money in Integer Minor Units:** All monetary columns (`budget_cents`, `price_cents`, `authorized_cap_cents`, `amount_cents`) use `BIGINT` integer cents accompanied by an ISO 4217 `currency_code` (e.g. `USD`). Floats and decimals are prohibited in cryptographic comparisons.
5. **Distinct Identity Columns:** Channel3 product IDs, merchant domains, display names, product offer URLs, and PayPal payee IDs are preserved in distinct, un-aliased columns.
6. **Zero Secret Leakage:** No PayPal client secrets, access tokens, or private Ed25519 signing keys are designed, stored, or accepted. Only public keys, cryptographic signatures, digests, and sanitized audit payloads are persisted.
7. **Transaction Safety:** Repository methods never commit or roll back caller-owned sessions. Multi-step mutations use nested savepoints (`session.begin_nested()`) to guarantee clean rollback on failure.

---

## 2. Service-to-Database Contract Mappings

```
┌─────────────────────────────────┐
│     PROCESS A: Shopper Intent   │──► negotiations (id, shopper_id, request_prompt, budget_cents, currency_code)
└─────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│     PROCESS B: Negotiation      │──► products (external_product_id, title, metadata)
│        & Deal Freezing          │──► offer_snapshots (price_cents, currency_code, offer_hash, terms_payload)
│                                 │──► product_references (channel3_product_id, digest, signature, public_key)
└─────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│     SECURITY GATES (Mandate)    │──► mandates (mandate_id, mandate_digest, authorized_cap_cents, public_key, signature)
│                                 │──► consent_evidence (evidence_id, mandate_id, mandate_digest, UNIQUE(mandate_id))
│                                 │──► verification_events (gate_name, decision, manifest_digest, passed_checks)
└─────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│     PROCESS C: Payment Policy   │──► mandate_consumptions (mandate_id, mandate_digest, idempotency_key, tx_id)
│        (Money Movement)         │──► payment_events (paypal_order_id, auth_id, capture_id, amount_cents, idempotency_key)
└─────────────────────────────────┘
```

### Process A: Shopper Intent (`negotiations`)
- **Repository:** `NegotiationRepository`
- **Tables:** `negotiations`
- **Fields:** `id` (UUID), `shopper_id`, `request_prompt`, `budget_cents` (BIGINT $\ge 0$), `currency_code` (ISO 4217), `status` (`OPEN`, `NEGOTIATING`, `AGREED`, `REJECTED`, `EXPIRED`), `metadata` (JSONB).

### Process B: Product Discovery & Deal Freezing (`products`, `offer_snapshots`, `product_references`)
- **Repository:** `NegotiationRepository`
- **Tables:**
  - `products`: Discovered catalog items with Channel3 product references and extracted specifications.
  - `offer_snapshots`: Point-in-time frozen offers locked with SHA-256 `offer_hash`, `price_cents`, and `currency_code`.
  - `product_references`: Canonical `product-reference.v1` and `v2` records signed with Ed25519, binding `channel3_product_id`, `merchant_domain`, `canonical_category`, `price_cents`, `currency_code`, and `digest`.

### Process C: Payment Policy & Money Movement (`mandates`, `consent_evidence`, `mandate_consumptions`, `payment_events`)
- **Repositories:** `MandateRepository`, `ConsentRepository`, `AuditRepository`
- **Tables:**
  - `mandates`: Authoritative signed spending agreements conforming to `mandate.v2`.
  - `consent_evidence`: Durable, single-use shopper consent claim bound to a specific mandate digest.
  - `mandate_consumptions`: Replay prevention ledger enforcing atomic, single-use mandate consumption.
  - `payment_events`: PayPal Orders v2, Authorizations, and Captures transaction log with unique `idempotency_key` replay protection.

---

## 3. Explicit `ConsentStore` Transaction & Concurrency Behavior

The `ConsentRepository` implements the `ConsentStore` protocol (`record_and_claim(evidence) -> bool`):

```python
class ConsentStore(Protocol):
    def record_and_claim(self, evidence: MandateConsentEvidence) -> bool:
        """Atomically record evidence and reject an already-consumed event."""
        ...
```

### Concurrency & Uniqueness Semantics
1. **Target Mandate Verification:** Before attempting to record, the repository fetches the target mandate and verifies that `mandate.mandate_digest == evidence.mandate_digest`. If digests mismatch, it immediately raises `PersistenceError` (fails closed).
2. **Zero Synthetic Digests:** Passing an empty, missing, or synthetic fallback digest (e.g. `digest_<mandate_id>`) raises `ValueError`.
3. **Atomic Claim Pattern:**
   ```sql
   INSERT INTO consent_evidence (
       evidence_id, mandate_id, mandate_digest, negotiation_id,
       shopper_id, approval_action, evidence_payload, approved_at
   )
   VALUES (
       :evidence_id, :mandate_id, :mandate_digest, :negotiation_id,
       :shopper_id, :approval_action, :evidence_payload, :approved_at
   )
   ON CONFLICT (mandate_id) DO NOTHING
   RETURNING id;
   ```
4. **Return Verdicts:**
   - **`True`**: The consent evidence was newly inserted and the mandate successfully claimed.
   - **`False`**: A conflict occurred on `UNIQUE(mandate_id)` (already claimed) or `UNIQUE(evidence_id)` (replayed evidence identifier).
5. **Exception Boundary:** Database connection drops, network timeouts, or schema errors propagate as exceptions; they are **never converted into `False`** or swallowed.

---

## 4. Mandate Consumption State Machine

Mandate lifecycle transitions are deterministic and strictly unidirectional:

```
               ┌────────────────┐
               │     ACTIVE     │
               └───────┬────────┘
                       │
       ┌───────────────┴───────────────┐
       │                               │
       ▼                               ▼
┌──────────────┐               ┌───────────────┐
│   CONSUMED   │               │    EXPIRED    │
└──────────────┘               └───────────────┘
```

### Atomic Consumption Implementation (`MandateRepository.consume_mandate`)
```python
def consume_mandate(
    mandate_id: str,
    expected_digest: str,
    idempotency_key: str,
    tx_id: str,
    ...
) -> bool:
```

### State Transition Invariants
1. **Pre-flight Digest Validation:** Validates `mandate.mandate_digest == expected_digest`. If the digest is synthetic or mismatches, execution fails closed (`PersistenceError`).
2. **Conditional Atomic Update:**
   ```sql
   UPDATE mandates
   SET consumed_at = :now, status = 'CONSUMED', consumption_tx_id = :tx_id
   WHERE mandate_id = :mandate_id
     AND mandate_digest = :expected_digest
     AND status = 'ACTIVE'
     AND consumed_at IS NULL
     AND expires_at > :now
   RETURNING mandate_id;
   ```
3. **Durable Ledger Insert:** Upon a successful row update, a matching record is inserted into `mandate_consumptions`.
4. **Concurrency Safety:** Parallel workers racing to consume the same mandate cannot both succeed. Exactly one worker receives `True`; all subsequent attempts receive `False`.

---

## 5. Migration Execution & Validation Commands

### Forward Migrations
Execute in order against PostgreSQL 16:
```bash
# 001: Initial core schema (UUID extension, core tables, triggers)
psql -U <user> -d <dbname> -f migrations/001_initial_schema.sql

# 002: Protocol reconciliation (Minor units, mandate.v2, product-reference.v1)
psql -U <user> -d <dbname> -f migrations/002_protocol_reconcile.sql

# 003: Durable consent evidence, protocol v2 alignment, and consumption safeguards
psql -U <user> -d <dbname> -f migrations/003_durable_consent_and_v2_reconcile.sql
```

### Rollback (Down Migrations)
```bash
psql -U <user> -d <dbname> -f migrations/003_durable_consent_and_v2_reconcile.down.sql
psql -U <user> -d <dbname> -f migrations/002_protocol_reconcile.down.sql
psql -U <user> -d <dbname> -f migrations/001_initial_schema.down.sql
```

### Schema Audit & Static Invariant Check
```powershell
$env:PYTHONPATH="."
py scripts/verify_db_schema.py
```

### Test Suite Execution
```powershell
$env:PYTHONPATH="."
py -m pytest tests
```

---

## 6. Record of Tests Executed vs. Tests Skipped

| Test Category | Suite / Test File | Tests Run | Passed | Failed | Skipped |
|---|---|---|---|---|---|
| **Baseline Models & Dialect DDL** | `tests/test_db_baseline.py` | 4 | 4 | 0 | 0 |
| **Failure-Mode & Minor Units** | `tests/test_persistence_layer.py` | 5 | 5 | 0 | 0 |
| **Consent & Concurrency v2** | `tests/test_persistence_v2.py` | 6 | 6 | 0 | 0 |
| **Mandate Gate Security Tests** | `services/mandate-gate/tests` | 44 | 44 | 0 | 0 |
| **Agent Orchestrator Tests** | `services/agent-orchestrator/tests` | 27 | 27 | 0 | 0 |
| **Payment Policy Service Tests** | `services/payment-policy-svc/tests` | 57 | 57 | 0 | 0 |
| **TOTAL** | | **143** | **143** | **0** | **0** |
