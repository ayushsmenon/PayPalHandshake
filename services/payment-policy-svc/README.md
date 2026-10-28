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

## Trusted Channel3 product references

Process C can issue a short-lived reference only after it independently
re-fetches the documented Channel3 product-detail record:
`GET https://api.trychannel3.com/v1/products/{product_id}` with the
`x-api-key` header. Configure `CHANNEL3_API_KEY` in Process C's local `.env`
only; use [.env.example](./.env.example) as a placeholder template.

The issuer requires an explicitly selected merchant domain, USD price, and
currency. It accepts exactly one available (`InStock`) matching offer and
binds the product ID, offer URL, merchant domain, integer minor-unit price,
currency, availability, condition, issue time, and expiry into a
domain-separated Ed25519 signature:

```powershell
cd services\payment-policy-svc
python scripts/issue_channel3_product_reference.py PpawoVb --merchant example.com --price 329.00 --currency USD
```

The command is local and read-only with respect to Channel3. It does not call
PayPal. A reference is not payment authorization by itself: the signed
reference must be included in the Process C-signed mandate and matched to the
candidate product line by Process B. The workflow fails closed when Channel3
does not provide an exact, unique offer with a stable URL and authoritative
price/merchant fields. Channel3's current record does not guarantee GTIN/UPC,
so no such identifier is invented.

The issuer output remains the signed-reference JSON printed to stdout. To save
that same JSON for a local Process B smoke test, add `--output-file`:

```powershell
python scripts\issue_channel3_product_reference.py PpawoVb --merchant newegg.com --price 143.99 --currency USD --output-file .\PpawoVb.product-reference.json
```

The generated file contains only the signed product reference and is ignored
by Git.

To send a synthetic Process C-signed mandate containing a product reference to
the running local Process B endpoint:

```powershell
cd services\payment-policy-svc
python scripts\smoke_test_product_reference_gate.py
```

This smoke test uses only the configured local signing key and synthetic
identifiers. It reports the product-reference check separately from the
overall gate result and never calls PayPal.

To verify a freshly issued real Channel3 reference instead of the synthetic
reference used by the original smoke test:

```powershell
python scripts\smoke_test_issued_product_reference.py .\PpawoVb.product-reference.json
```

The script embeds the exact reference payload and signature from the file,
signs the containing mandate with the existing Process C key, and submits it
to Process B. A `PASS` product-reference check proves Process B verified that
Process C-issued reference; it does not by itself authorize or capture a
PayPal payment. The candidate must also satisfy every other gate check.

Restart Process B after updating its source so the running Uvicorn process
loads the current mandate and product-reference models. A `422` response
whose details report `extra_forbidden` for `mandate.product_references`,
`candidate.products[*].unit_price_cents`, or
`candidate.products[*].product_reference` means the running Process B process
is stale; the current source contract accepts these fields.

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
