# Mandate Gate

The Mandate Gate is Process B for PAYPAL HANDSHAKE: a deterministic security
boundary that verifies a signed mandate and checks a candidate deal against it.
It is exposed as an independent FastAPI service for backend-to-backend calls.

## Trust boundary

- Uses no LLM, embedding, or keyword-matching packages.
- Has no PayPal credentials and makes no network calls.
- Contains no private signing key.
- Accepts only an Ed25519 public key supplied by the caller/configuration.
- Uses integer cents for spending-cap comparisons.
- Treats product descriptions as informational; descriptions and similarity
  scores are never proof of product identity.
- Returns `REJECT` and `payment_ready: false` until trusted product references
  are explicitly configured.

Synthetic product identifiers such as `SYNTH-MONITOR-27-4K` may be used in
isolated tests only. They are not real GTINs, UPCs, or catalogue references.

## Canonical signed mandate format

Sign the UTF-8 bytes of compact JSON produced by `canonical_mandate_bytes`:

1. Serialize the Pydantic mandate in JSON mode.
2. Use lexicographically sorted keys.
3. Use compact separators `(",", ":")`.
4. Escape non-ASCII characters.
5. Encode the resulting JSON as UTF-8.

The signature and pinned public key are base64-encoded Ed25519 byte strings.
The production private key belongs outside this service and is never loaded by
the gate.

## Configuration and usage

Applications should load a pinned public key from deployment configuration, for
the `MANDATE_GATE_PUBLIC_KEY_B64` environment variable. The value is the
base64-encoded Ed25519 public key. This service does not read PayPal
credentials or call external services.

`evaluate_candidate` returns per-check `PASS`/`REJECT` results and an overall
verdict. A valid signature and matching candidate still cannot be payment-ready
when trusted product references are unconfigured.

The HTTP request contains only the signed mandate, signature, and candidate
deal. It cannot provide a public key or declare trusted product references
configured. A running process is not necessarily ready: `/health` reports
liveness, while `/ready` reports whether a valid pinned public key is loaded.
Even when `/ready` reports ready, `/api/v1/verify` remains fail-closed and
returns `payment_ready: false` until trusted product references exist.

## Install and test on Windows PowerShell

```powershell
cd services\mandate-gate
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest
```

The tests generate temporary Ed25519 key pairs in memory and use only synthetic
identifiers. They do not access the network or PayPal.

## Run the HTTP service

Set the pinned public key in the process environment, then start on loopback:

```powershell
$env:MANDATE_GATE_PUBLIC_KEY_B64 = "<base64-pinned-ed25519-public-key>"
uvicorn mandate_gate.api:app --host 127.0.0.1 --port 8010
```

Without activation:

```powershell
.\.venv\Scripts\python.exe -m uvicorn mandate_gate.api:app --host 127.0.0.1 --port 8010
```

Health and readiness checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8010/health
Invoke-RestMethod http://127.0.0.1:8010/ready
```

Verification request shape:

```powershell
$body = @{
  mandate = @{
    schema_version = "1"
    negotiation_id = "HS-2048"
    paypal_auth_id = "AUTH-SYNTHETIC"
    authorized_cap_cents = 45000
    currency = "USD"
    authorized_merchant_id = "merchant-best-buy-sandbox"
    allowed_product_identifiers = @("SYNTH-MONITOR-27-4K")
    issued_at = "2026-01-01T00:00:00Z"
    expires_at = "2026-01-01T01:00:00Z"
    mandate_id = "mandate-synthetic-api"
  }
  signature = "<base64-ed25519-signature>"
  candidate = @{
    negotiation_id = "HS-2048"
    paypal_auth_id = "AUTH-SYNTHETIC"
    merchant_id = "merchant-best-buy-sandbox"
    total_cents = 41850
    currency = "USD"
    products = @(@{
      product_identifier = "SYNTH-MONITOR-27-4K"
      quantity = 1
      description = "Synthetic monitor"
    })
  }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod http://127.0.0.1:8010/api/v1/verify `
  -Method Post -ContentType "application/json" -Body $body
```
