# Agent Orchestrator

Minimal FastAPI service for the PAYPAL HANDSHAKE demonstration negotiation.

This service returns explicitly labelled mock data for the Deal Review screen.
The FastAPI application does not connect to PayPal, perform verification,
authorize payments, capture funds, or enforce the final security boundary.
Separate local-only PayPal Sandbox smoke-test scripts are documented below.

## Requirements

- Python 3.15 (the current development environment)

## Install on Windows PowerShell

From the repository root:

```powershell
cd services\agent-orchestrator
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell execution policy prevents activation, run commands through the
virtual environment directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Start the service

With the virtual environment activated:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Or without activation:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The interactive API documentation is available at
`http://127.0.0.1:8000/docs`.

## Run tests

```powershell
python -m pytest
```

Without activation:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## API

### `GET /health`

Returns a small service health response.

### `GET /api/v1/negotiations/demo`

Returns the illustrative HS-2048 negotiation in integer cents. Product
identifiers, product verification, mandate evidence, and transaction details
remain unknown or pending. `captured_amount_cents` is always `0` for this
milestone.

The API allows CORS only from `http://localhost:3000`, the local Next.js
frontend origin.

## Channel3 product-search smoke test

The local-only Channel3 smoke test uses the documented API interface:

- `POST https://api.trychannel3.com/v1/search`
- Authentication via the `x-api-key` header
- JSON body `{"query": "27-inch 4K monitor", "limit": 5}`
- Results read from the documented `products`, `id`, `title`, `brands`, and
  `offers[].price` fields

Set `CHANNEL3_API_KEY` only in the ignored
`services/agent-orchestrator/.env`. The key is server-side and must not be
placed in a `NEXT_PUBLIC_` variable or printed to the terminal.

Run the read-only search from the service directory:

```powershell
cd services\agent-orchestrator
python scripts\check_channel3_search.py
```

The query is an optional positional argument. If omitted, the default is
`27-inch 4K monitor`; for example:

```powershell
python scripts\check_channel3_search.py "mechanical keyboard"
```

The output is limited to five concise product summaries. Missing GTIN, UPC, or
other product identifiers are reported as unavailable; they are never
invented. Channel3 results are catalogue search results only—not shopper-
authorized products and not trusted mandate references. This script does not
modify the mandate gate, Process C, or any PayPal resource.

## Channel3 product-detail smoke test

The read-only product-detail smoke test follows Channel3's documented
interface:

- `GET https://api.trychannel3.com/v1/products/{product_id}`
- Authentication via the `x-api-key` header
- Product details returned as a documented Product object

It reports the returned product ID, title, brands, description, image count,
structured attributes, selected variants, explicit identifier attributes,
merchant offers, prices, currencies, availability, and condition. Missing
optional fields or identifiers are reported as unavailable. It does not infer a
GTIN, UPC, EAN, SKU, or MPN from a title or model name.

Run it from the service directory:

```powershell
cd services\agent-orchestrator
python scripts\inspect_channel3_product.py <channel3_product_id>
```

For example:

```powershell
python scripts\inspect_channel3_product.py product-123
```

This establishes only what Channel3 returned for that catalogue record. It
does not establish shopper authorization, product identity for a mandate, or
trusted product references for the Mandate Gate. It does not call PayPal or
change any payment state.

## PayPal Sandbox OAuth smoke test

This repository includes a local-only OAuth connectivity check. It requests a
PayPal Sandbox client-credentials token, but it does not create orders,
authorize payments, capture funds, or send payouts. The script never prints
the client secret or access token.

Create a local environment file from the tracked template:

```powershell
cd services\agent-orchestrator
Copy-Item .env.example .env
notepad .env
```

Set the two local credential values in `.env`:

```text
PAYPAL_ENV=sandbox
PAYPAL_CLIENT_ID=your_local_sandbox_client_id
PAYPAL_CLIENT_SECRET=your_local_sandbox_client_secret
```

Do not commit `.env` or paste its contents into chat. The repository root
`.gitignore` ignores real environment files while allowing `.env.example`.

Run the smoke test from the service directory:

```powershell
python scripts\check_paypal_oauth.py
```

On success it prints the HTTP status and token expiry duration only. On
failure it prints a concise status or network diagnostic without credentials,
tokens, or response headers.

## PayPal Sandbox order smoke test

After OAuth succeeds, the order smoke test creates one Sandbox order with
intent `AUTHORIZE` for the illustrative `$450.00 USD` Handshake demo. It only
creates the order and displays the buyer approval URL. It does not authorize,
capture, void, or refund a payment, and an order-creation success is not proof
that money was authorized.

Run it from the service directory:

```powershell
python scripts\create_sandbox_order.py
```

The request uses the reference ID `handshake-demo`, a unique
`PayPal-Request-Id`, and local return/cancel URLs:

- `http://localhost:3000/paypal/sandbox/return`
- `http://localhost:3000/paypal/sandbox/cancel`

If PayPal returns an approval URL, open it in a Sandbox browser session and
approve the order with a Sandbox buyer account before implementing any later
authorization step. This milestone intentionally stops before authorization.

## Inspect a PayPal Sandbox order

After returning from buyer approval, inspect an existing Sandbox order with a
read-only GET request. The script reports the PayPal status, whether that
status is exactly `APPROVED`, and each purchase-unit currency and amount. It
does not authorize, capture, void, refund, or otherwise change the order.

Run it from the service directory:

```powershell
python scripts\inspect_sandbox_order.py <order_id>
```

## Authorize an approved PayPal Sandbox order

After independently confirming the order is approved and matches the expected
intent, currency, and amount, authorize the specific order with:

```powershell
python scripts\authorize_sandbox_order.py 9EL47348YP786645U
```

The script performs a fresh read-only preflight, skips authorization if one
already exists, and uses a deterministic `PayPal-Request-Id` for this order.
If the authorization request times out, reconcile the order and authorization
state before retrying. This script never captures, voids, refunds, or pays out
funds; captured amount remains zero.
