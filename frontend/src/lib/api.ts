export type NegotiationStatus = "VERIFYING";
export type VerificationState = "PENDING" | "RUNNING" | "PRELIMINARY";

export type Product = {
  name: string;
  price_cents: number;
  illustrative: boolean;
  verified: boolean;
  product_identifier: null;
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

export type DealAssemblyRequest = {
  negotiation_id: string;
  requirements: string[];
  budget_cents: number;
  currency: string;
};

export type DealItem = {
  requirement: string;
  product_identifier: string;
  name: string;
  merchant_domain: string;
  offer_url: string;
  price_cents: number;
  currency: string;
  availability: string;
  source: string;
  illustrative: boolean;
};

export type DealAssemblyResponse = {
  status: string;
  is_real: boolean;
  source: string;
  negotiation_id: string;
  budget_cents: number;
  currency: string;
  total_cents: number;
  remaining_budget_cents: number;
  items: DealItem[];
  limitations: string[];
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

export async function assembleDeal(
  request: DealAssemblyRequest,
  signal?: AbortSignal,
): Promise<DealAssemblyResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/deals/assemble`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    ...(signal ? { signal } : {}),
  });

  if (!response.ok) {
    throw new Error(`The deal assembly service returned ${response.status}.`);
  }

  return (await response.json()) as DealAssemblyResponse;
}
