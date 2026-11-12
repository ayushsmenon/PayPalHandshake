export type NegotiationStatus = "VERIFYING" | "VERIFIED" | "CAPTURED";
export type VerificationState =
  | "PENDING"
  | "RUNNING"
  | "PRELIMINARY"
  | "PASSED"
  | "BLOCKED";

export type Product = {
  name: string;
  price_cents: number;
  illustrative: boolean;
  verified: boolean;
  product_identifier?: string | null;
  canonical_category?: string | null;
  merchant_domain?: string | null;
  offer_url?: string | null;
};

export type NegotiationEvent = {
  label: string;
  participant: string;
  amount_cents: number;
};

export type VerificationCheck = {
  check: string;
  state: VerificationState;
};

export type DemoNegotiation = {
  is_demo: boolean;
  demo_notice: string;
  negotiation_id: string;
  shopping_goal: string;
  merchant: string;
  currency: string;
  market_reference_cents: number;
  authorized_budget_cents: number;
  proposed_total_cents: number;
  captured_amount_cents: number;
  status: NegotiationStatus;
  products: Product[];
  history: NegotiationEvent[];
  verification_checks: VerificationCheck[];
};

export type VerificationCheckDetail = {
  name: string;
  verdict: string;
  detail: string;
};

export type DealEvidenceProduct = {
  name: string;
  product_identifier: string;
  price_cents: number;
  canonical_category: string;
  verified: boolean;
  offer_url: string;
  merchant_domain: string;
};

export type DealVerificationResponse = {
  verdict: "PASS" | "BLOCKED" | string;
  payment_ready: boolean;
  checks: VerificationCheckDetail[];
  mandate_id: string;
  mandate_digest_b64: string;
  authorized_cap_cents: number;
  paypal_order_id: string;
  paypal_auth_id: string;
  products: DealEvidenceProduct[];
};

export type DealApprovalCaptureRequest = {
  negotiation_id: string;
  mandate_id: string;
  shopper_id?: string;
  confirmed_amount_cents: number;
  confirmed_merchant: string;
};

export type DealApprovalCaptureResponse = {
  status: string;
  paypal_order_id: string;
  paypal_authorization_id: string;
  paypal_capture_id: string;
  captured_amount_cents: number;
  currency: string;
  mandate_id: string;
  mandate_digest_b64: string;
  timestamp: string;
};

export type AuditTrailItem = {
  event_id: string;
  event_type: string;
  name_or_gate: string;
  decision_or_status: string;
  amount_cents?: number | null;
  details?: string | null;
  created_at: string;
};

export type AuditTrailResponse = {
  negotiation_id: string;
  events: AuditTrailItem[];
};

const API_BASE_URL =
  process.env.NEXT_PUBLIC_HANDSHAKE_API_URL ?? "http://127.0.0.1:8000";

export function formatCents(cents: number, currency = "USD"): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
  }).format(cents / 100);
}

export async function fetchDemoNegotiation(
  signal?: AbortSignal,
): Promise<DemoNegotiation> {
  const response = await fetch(
    `${API_BASE_URL}/api/v1/negotiations/demo`,
    signal ? { signal } : undefined,
  );

  if (!response.ok) {
    throw new Error(`The negotiation service returned ${response.status}.`);
  }

  return (await response.json()) as DemoNegotiation;
}

export async function verifyDeal(
  negotiationId = "HS-2048",
  signal?: AbortSignal,
): Promise<DealVerificationResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/deals/verify`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ negotiation_id: negotiationId }),
    signal,
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(
      `Verification failed (${response.status}): ${errorText || response.statusText}`,
    );
  }

  return (await response.json()) as DealVerificationResponse;
}

export async function approveAndCaptureDeal(
  payload: DealApprovalCaptureRequest,
  signal?: AbortSignal,
): Promise<DealApprovalCaptureResponse> {
  const response = await fetch(
    `${API_BASE_URL}/api/v1/deals/approve-and-capture`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
      signal,
    },
  );

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(
      `Capture failed (${response.status}): ${errorText || response.statusText}`,
    );
  }

  return (await response.json()) as DealApprovalCaptureResponse;
}

export async function fetchAuditTrail(
  negotiationId = "HS-2048",
  signal?: AbortSignal,
): Promise<AuditTrailResponse> {
  const response = await fetch(
    `${API_BASE_URL}/api/v1/audit/trail/${encodeURIComponent(negotiationId)}`,
    signal ? { signal } : undefined,
  );

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(
      `Audit trail fetch failed (${response.status}): ${errorText || response.statusText}`,
    );
  }

  return (await response.json()) as AuditTrailResponse;
}
