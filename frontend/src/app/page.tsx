"use client";

import { useCallback, useEffect, useState } from "react";

import {
  DemoNegotiation,
  formatCents,
  fetchDemoNegotiation,
  VerificationState,
} from "../lib/api";

function displayState(state: VerificationState): string {
  return state.charAt(0) + state.slice(1).toLowerCase();
}

function StatusMark({ state }: { state: VerificationState }) {
  const label = displayState(state);

  return (
    <span className={`status status-${state.toLowerCase()}`}>
      <span className="status-icon" aria-hidden="true">
        {state === "RUNNING" ? "◌" : "○"}
      </span>
      {label}
    </span>
  );
}

function ProductRow({
  product,
  currency,
}: {
  product: DemoNegotiation["products"][number];
  currency: string;
}) {
  return (
    <div className="product-row">
      <div className="product-thumb" aria-hidden="true" />
      <p className="product-name">
        {product.name} <span>· SKU unknown</span>
      </p>
      <p className="product-price">{formatCents(product.price_cents, currency)}</p>
    </div>
  );
}

function NegotiationLine({
  negotiation,
}: {
  negotiation: DemoNegotiation;
}) {
  const positions = ["counter", "final", "cap", "opening", "market"] as const;
  const values = [
    negotiation.history.find((event) => event.label === "Buyer counter-offer"),
    negotiation.history.find((event) => event.label === "Merchant final offer"),
    {
      amount_cents: negotiation.authorized_budget_cents,
      label: "Authorized cap",
      participant: "Shopper",
    },
    negotiation.history.find((event) => event.label === "Merchant opening offer"),
    {
      amount_cents: negotiation.market_reference_cents,
      label: "Market reference",
      participant: "Market data",
    },
  ];

  return (
    <div className="negotiation-line" aria-label="Illustrative negotiation sequence">
      <div className="line-track" />
      <div className="line-segment" />
      {values.map((offer, index) => {
        if (!offer) return null;
        const label = index === 0
          ? "counter"
          : index === 1
            ? "final"
            : index === 2
              ? "cap"
              : index === 3
                ? "opening"
                : "market";

        return (
          <div className={`offer-marker offer-${positions[index]}`} key={label}>
            <span className="offer-dot" />
            <span className="offer-label">
              {formatCents(offer.amount_cents, negotiation.currency)} {label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function TermsTable({ negotiation }: { negotiation: DemoNegotiation }) {
  return (
    <section className="terms-section" aria-labelledby="terms-heading">
      <div className="section-heading">
        <h2 id="terms-heading">Terms vs. deal</h2>
      </div>
      <div className="terms-table">
        <div className="table-row table-header" role="row">
          <span>Check</span>
          <span>You authorized</span>
          <span>Deal says</span>
          <span>Result</span>
        </div>
        {negotiation.verification_checks.map((check) => {
          const row = {
            "Signed mandate": ["Mandate required", negotiation.negotiation_id],
            "Product identity": ["2 listed items", "2 candidate items"],
            "Merchant/payee identity": [negotiation.merchant, negotiation.merchant],
            "Total versus spending cap": [
              `${formatCents(negotiation.authorized_budget_cents, negotiation.currency)} cap`,
              `${formatCents(negotiation.proposed_total_cents, negotiation.currency)} proposed`,
            ],
            "Purchase description": ["Monitor + keyboard", "Negotiated description"],
          }[check.check] ?? ["Unknown", "Unknown"];

          return (
            <div className="table-row" role="row" key={check.check}>
              <span>{check.check}</span>
              <span>{row[0]}</span>
              <span>{row[1]}</span>
              <span>
                <StatusMark state={check.state} />
              </span>
            </div>
          );
        })}
        <p className="verifier-statement">
          The independent verifier checks the deal. AI agents cannot approve their own results.
        </p>
      </div>
    </section>
  );
}

function PaymentRail({ negotiation }: { negotiation: DemoNegotiation }) {
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const statusText =
    negotiation.status === "VERIFYING"
      ? "Awaiting security verification"
      : negotiation.status;

  return (
    <aside className="payment-rail" aria-labelledby="payment-heading">
      <h2 id="payment-heading">Payment</h2>
      <div className="payment-values">
        <div>
          <span>Authorized up to</span>
          <strong>{formatCents(negotiation.authorized_budget_cents, negotiation.currency)}</strong>
        </div>
        <div>
          <span>Proposed capture</span>
          <strong>{formatCents(negotiation.proposed_total_cents, negotiation.currency)}</strong>
        </div>
        <div>
          <span>Actually captured</span>
          <strong>{formatCents(negotiation.captured_amount_cents, negotiation.currency)}</strong>
        </div>
      </div>
      <div className="payment-status">
        <p className="payment-status-title">{statusText}</p>
        <p>No capture until every required check completes.</p>
        <button
          className="text-link"
          type="button"
          aria-expanded={evidenceOpen}
          onClick={() => setEvidenceOpen((open) => !open)}
        >
          View evidence
        </button>
      </div>
      <div className="capture-block">
        <button className="capture-button" type="button" disabled>
          Capture {formatCents(negotiation.proposed_total_cents, negotiation.currency)}
        </button>
        <p>Waiting on 5 of 5 checks · Powered by PayPal [official asset placeholder]</p>
      </div>
      {evidenceOpen && (
        <div className="evidence-list" aria-label="Evidence details">
          <div><span>Mandate validity</span><strong>Pending</strong></div>
          <div><span>Merchant reference</span><strong>Pending</strong></div>
          <div><span>Verifier timestamp</span><strong>Not available</strong></div>
          <div><span>PayPal transaction ID</span><strong className="mono">None</strong></div>
          <div><span>Negotiation ID</span><strong className="mono">{negotiation.negotiation_id}</strong></div>
        </div>
      )}
      <p className="rail-footnote">Capture is backend-confirmed, never inferred from a click.</p>
    </aside>
  );
}

export default function Home() {
  const [negotiation, setNegotiation] = useState<DemoNegotiation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [transcriptOpen, setTranscriptOpen] = useState(false);

  const loadNegotiation = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    fetchDemoNegotiation(signal)
      .then(setNegotiation)
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError("We couldn’t load the demo negotiation. Check that the backend is running and try again.");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchDemoNegotiation(controller.signal)
      .then(setNegotiation)
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError("We couldn’t load the demo negotiation. Check that the backend is running and try again.");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  if (loading) {
    return <main className="page-state" aria-live="polite">Loading Deal Review…</main>;
  }

  if (error || !negotiation) {
    return (
      <main className="page-state page-state-error" role="alert">
        <h1>Deal Review unavailable</h1>
        <p>{error ?? "The backend returned no negotiation data."}</p>
        <button className="secondary-button" type="button" onClick={() => loadNegotiation()}>
          Retry
        </button>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="header-left">
          <a className="wordmark" href="#deal">HANDSHAKE</a>
          <nav aria-label="Primary navigation">
            <a className="active" href="#deal">Deals</a>
            <a href="#activity">Audit log</a>
          </nav>
        </div>
        <div className="header-right">
          <span className="sandbox-pill">Sandbox · no real money moves</span>
          <span className="profile-name">Avery Singh</span>
          <button className="secondary-button" type="button">New negotiation</button>
        </div>
      </header>
      <div className="content-layout">
        <section className="main-column" id="deal">
          <section className="title-block" aria-labelledby="deal-title">
            <p className="eyebrow">
              Active deal · {negotiation.is_demo ? "demo data" : "live data"} · {negotiation.merchant} ·{" "}
              <span className="mono">{negotiation.negotiation_id}</span>
            </p>
            <h1 id="deal-title">{negotiation.shopping_goal}</h1>
            <p>Proposed by an AI agent. Only the independent verifier can approve it.</p>
          </section>
          <section className="figures-row" aria-label="Deal figures">
            <div className="hero-figure">
              <span>Proposed deal</span>
              <strong>{formatCents(negotiation.proposed_total_cents, negotiation.currency)}</strong>
              <small>Claimed · awaiting independent verification</small>
            </div>
            <div className="supporting-figure">
              <span>Authorized cap</span>
              <strong>{formatCents(negotiation.authorized_budget_cents, negotiation.currency)}</strong>
              <small>
                {formatCents(
                  negotiation.authorized_budget_cents - negotiation.proposed_total_cents,
                  negotiation.currency,
                )}{" "}
                below cap
              </small>
            </div>
            <div className="supporting-figure">
              <span>Market reference</span>
              <strong>{formatCents(negotiation.market_reference_cents, negotiation.currency)}</strong>
              <small>
                {formatCents(
                  negotiation.market_reference_cents - negotiation.proposed_total_cents,
                  negotiation.currency,
                )}{" "}
                below reference
              </small>
            </div>
          </section>
          <section className="products-section" aria-labelledby="products-heading">
            <h2 id="products-heading">Candidate products</h2>
            {negotiation.products.map((product) => (
              <ProductRow product={product} currency={negotiation.currency} key={product.name} />
            ))}
            <p className="product-caption">
              Illustrative catalogue data · product identity not yet verified
            </p>
          </section>
          <section className="negotiation-section" aria-labelledby="negotiation-heading">
            <h2 id="negotiation-heading">Negotiation</h2>
            <NegotiationLine negotiation={negotiation} />
            <p className="negotiation-caption">
              Illustrative demo sequence ·{" "}
              <button type="button" className="inline-link" onClick={() => setTranscriptOpen((open) => !open)}>
                View full transcript
              </button>
            </p>
            {transcriptOpen && (
              <div className="transcript" role="region" aria-label="Illustrative transcript">
                {negotiation.history.map((event) => (
                  <p key={event.label}>
                    <span>{event.participant}</span> {formatCents(event.amount_cents, negotiation.currency)}{" "}
                    {event.label.toLowerCase()}
                  </p>
                ))}
              </div>
            )}
          </section>
          <TermsTable negotiation={negotiation} />
        </section>
        <PaymentRail negotiation={negotiation} />
      </div>
    </main>
  );
}
