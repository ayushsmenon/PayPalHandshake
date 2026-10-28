"use client";

import { useState } from "react";

type VerificationState = "Waiting" | "Running" | "Preliminary";

type LedgerRow = {
  check: string;
  authorized: string;
  deal: string;
  result: VerificationState;
};

const ledgerRows: LedgerRow[] = [
  {
    check: "Authorization and signed mandate",
    authorized: "Mandate required",
    deal: "HS-2048",
    result: "Waiting",
  },
  {
    check: "Product identity",
    authorized: "2 listed items",
    deal: "2 candidate items",
    result: "Running",
  },
  {
    check: "Merchant / payee identity",
    authorized: "Best Buy Marketplace",
    deal: "Best Buy Marketplace",
    result: "Waiting",
  },
  {
    check: "Total vs. spending cap",
    authorized: "$450.00 cap",
    deal: "$418.50 proposed",
    result: "Preliminary",
  },
  {
    check: "Purchase description",
    authorized: "Monitor + keyboard",
    deal: "Negotiated description",
    result: "Waiting",
  },
];

const offers = [
  { price: "$410.00", label: "counter", position: "counter" },
  { price: "$418.50", label: "final", position: "final" },
  { price: "$450.00", label: "cap", position: "cap" },
  { price: "$465.00", label: "opening", position: "opening" },
  { price: "$478.00", label: "market", position: "market" },
];

function StatusMark({ state }: { state: VerificationState }) {
  return (
    <span className={`status status-${state.toLowerCase()}`}>
      <span className="status-icon" aria-hidden="true">
        {state === "Running" ? "◌" : "○"}
      </span>
      {state}
    </span>
  );
}

function ProductRow({
  name,
  price,
  kind,
}: {
  name: string;
  price: string;
  kind: "monitor" | "keyboard";
}) {
  return (
    <div className="product-row">
      <div className={`product-thumb product-thumb-${kind}`} aria-hidden="true" />
      <p className="product-name">
        {name} <span>· SKU unknown</span>
      </p>
      <p className="product-price">{price}</p>
    </div>
  );
}

function NegotiationLine() {
  return (
    <div className="negotiation-line" aria-label="Illustrative negotiation sequence">
      <div className="line-track" />
      <div className="line-segment" />
      {offers.map((offer) => (
        <div className={`offer-marker offer-${offer.position}`} key={offer.label}>
          <span className="offer-dot" />
          <span className="offer-label">
            {offer.price} {offer.label}
          </span>
        </div>
      ))}
    </div>
  );
}

function TermsTable() {
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
        {ledgerRows.map((row) => (
          <div className="table-row" role="row" key={row.check}>
            <span>{row.check}</span>
            <span>{row.authorized}</span>
            <span>{row.deal}</span>
            <span>
              <StatusMark state={row.result} />
            </span>
          </div>
        ))}
        <p className="verifier-statement">
          The independent verifier checks the deal. AI agents cannot approve their own results.
        </p>
      </div>
    </section>
  );
}

function PaymentRail() {
  const [evidenceOpen, setEvidenceOpen] = useState(false);

  return (
    <aside className="payment-rail" aria-labelledby="payment-heading">
      <h2 id="payment-heading">Payment</h2>
      <div className="payment-values">
        <div>
          <span>Authorized up to</span>
          <strong>$450.00</strong>
        </div>
        <div>
          <span>Proposed capture</span>
          <strong>$418.50</strong>
        </div>
        <div>
          <span>Actually captured</span>
          <strong>$0.00</strong>
        </div>
      </div>
      <div className="payment-status">
        <p className="payment-status-title">Awaiting security verification</p>
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
          Capture $418.50
        </button>
        <p>Waiting on 5 of 5 checks · Powered by PayPal [official asset placeholder]</p>
      </div>
      {evidenceOpen && (
        <div className="evidence-list" aria-label="Evidence details">
          <div><span>Mandate validity</span><strong>Pending</strong></div>
          <div><span>Merchant reference</span><strong>Pending</strong></div>
          <div><span>Verifier timestamp</span><strong>Not available</strong></div>
          <div><span>PayPal transaction ID</span><strong className="mono">None</strong></div>
          <div><span>Negotiation ID</span><strong className="mono">HS-2048</strong></div>
        </div>
      )}
      <p className="rail-footnote">Capture is backend-confirmed, never inferred from a click.</p>
    </aside>
  );
}

export default function Home() {
  const [transcriptOpen, setTranscriptOpen] = useState(false);

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
              Active deal · demo data · Best Buy Marketplace · <span className="mono">HS-2048</span>
            </p>
            <h1 id="deal-title">27-inch 4K monitor + mechanical keyboard</h1>
            <p>Proposed by an AI agent. Only the independent verifier can approve it.</p>
          </section>

          <section className="figures-row" aria-label="Deal figures">
            <div className="hero-figure">
              <span>Proposed deal</span>
              <strong>$418.50</strong>
              <small>Claimed · awaiting independent verification</small>
            </div>
            <div className="supporting-figure">
              <span>Authorized cap</span>
              <strong>$450.00</strong>
              <small>$31.50 below cap</small>
            </div>
            <div className="supporting-figure">
              <span>Market reference</span>
              <strong>$478.00</strong>
              <small>$59.50 below reference</small>
            </div>
          </section>

          <section className="products-section" aria-labelledby="products-heading">
            <h2 id="products-heading">Candidate products</h2>
            <ProductRow name="27-inch 4K monitor" price="$329.00" kind="monitor" />
            <ProductRow name="Mechanical keyboard" price="$89.50" kind="keyboard" />
            <p className="product-caption">Illustrative catalogue data · product identity not yet verified</p>
          </section>

          <section className="negotiation-section" aria-labelledby="negotiation-heading">
            <h2 id="negotiation-heading">Negotiation</h2>
            <NegotiationLine />
            <p className="negotiation-caption">
              Illustrative demo sequence ·{" "}
              <button type="button" className="inline-link" onClick={() => setTranscriptOpen((open) => !open)}>
                View full transcript
              </button>
            </p>
            {transcriptOpen && (
              <div className="transcript" role="region" aria-label="Illustrative transcript">
                <p><span>Market data</span> $478.00 reference</p>
                <p><span>Best Buy</span> $465.00 opening offer</p>
                <p><span>Handshake agent</span> $410.00 counter-offer</p>
                <p><span>Best Buy</span> $418.50 final offer</p>
              </div>
            )}
          </section>

          <TermsTable />
        </section>
        <PaymentRail />
      </div>
    </main>
  );
}
