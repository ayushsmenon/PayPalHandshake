# Payment Policy Service — Process C

This package is the initial trusted mandate-issuance component for PAYPAL
HANDSHAKE. It constructs validated `SignedMandate` values from trusted
server-side inputs and signs the exact canonical bytes consumed by Process B,
the Mandate Gate.

It is a development key-management foundation, not a production-ready payment
service. It has no PayPal credentials, no PayPal API calls, and no endpoints
for authorization, capture, void, refund, payout, or arbitrary public signing.

## Trust boundary

The private Ed25519 signing key is loaded only from
`MANDATE_SIGNING_PRIVATE_KEY_B64` in Process C's local `.env`. It is never
returned or logged. Process B receives only the corresponding public key in
`services/mandate-gate/.env` as `MANDATE_GATE_PUBLIC_KEY_B64`.

Production issuance must be based on validated shopper permissions and verified
PayPal authorization information. The untrusted buyer/AI agent must not be able
to submit arbitrary mandate fields to a signing endpoint.

The signer uses the same protocol as Process B:

- Pydantic mandate schema with extra fields forbidden.
- JSON-mode serialization.
- Lexicographically sorted keys.
- Compact separators `(",", ":")`.
- Escaped non-ASCII characters.
- UTF-8 encoding.
- Ed25519 signatures encoded as base64.

## Initialize a local development pair

From the repository root, run the one-time utility:

```powershell
cd services\payment-policy-svc
python scripts\initialize_dev_keys.py
```

The utility verifies that both `.env` paths are Git-ignored, preserves existing
environment variables, refuses to overwrite an existing pair, and never prints
either key. It writes the private key only to
`services/payment-policy-svc/.env` and the public key only to
`services/mandate-gate/.env`. Do not run this during automated validation if
you want to keep the local key pair uninitialized.

Start Process B from its directory after initialization:

```powershell
cd ..\mandate-gate
python -m uvicorn mandate_gate.api:app --host 127.0.0.1 --port 8010
```

Process B loads only its own `.env` and never accesses Process C's private-key
environment file.

## Install and test

```powershell
cd services\payment-policy-svc
python -m pip install -r requirements.txt
python -m pytest
```

Tests generate temporary in-memory keys and use synthetic identifiers. They do
not access PayPal or create local development key files.

## Local Process B smoke test

After initializing the development key pair and starting Process B, run the
local end-to-end signing and verification smoke test from this service:

```powershell
cd services\payment-policy-svc
python scripts\smoke_test_mandate_gate.py
```

The script loads only Process C's `.env`, signs a short-lived synthetic
mandate, and posts the exact `mandate`, `signature`, and `candidate` request
shape expected by Process B at `http://127.0.0.1:8010/api/v1/verify`. It never
calls PayPal and does not use the previously authorized Sandbox order.

Expected behavior is a successful signature check and an in-cap candidate, but
the overall gate must remain rejected and `payment_ready` must remain false
because trusted product references are not configured. A valid signature proves
only that the mandate was signed by the configured Process C key; it does not
prove product identity or authorize payment readiness.
