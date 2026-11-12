"use client";

import { useCallback, useEffect, useState } from "react";

import {
  AuditTrailItem,
  DealApprovalCaptureResponse,
  DealVerificationResponse,
  DemoNegotiation,
  VerificationCheckDetail,
  VerificationState,
  approveAndCaptureDeal,
  fetchAuditTrail,
  fetchDemoNegotiation,
  formatCents,
  verifyDeal,
} from "../lib/api";

function displayState(state: VerificationState): string {
  return state.charAt(0) + state.slice(1).toLowerCase();
}

function StatusMark({ state }: { state: VerificationState }) {
  const label = displayState(state);
  const icon =
    state === "PASSED"
      ? "✓"
      : state === "BLOCKED"
        ? "✕"
        : state === "RUNNING"
          ? "◌"
          : "○";

  return (
    <span className={`status status-${state.toLowerCase()}`}>
      <span className="status-icon" aria-hidden="true">
        {icon}
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
      <div
        className={`product-thumb ${
          product.name.toLowerCase().includes("keyboard")
            ? "product-thumb-keyboard"
            : "product-thumb-monitor"
        }`}
        aria-hidden="true"
      />
      <div className="product-name">
        <p style={{ margin: 0 }}>
          {product.name}{" "}
          {product.product_identifier ? (
            <span className="badge-tag badge-tag-verified">
              SKU: {product.product_identifier} · Verified
            </span>
          ) : (
            <span>· SKU unverified</span>
          )}
        </p>
        {product.canonical_category && (
          <p style={{ margin: "2px 0 0", fontSize: "12px", color: "var(--muted)" }}>
            Canonical Category: <span className="mono">{product.canonical_category}</span> · Merchant:{" "}
            <span className="mono">{product.merchant_domain ?? "merchant.example"}</span>
          </p>
        )}
      </div>
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
        const label =
          index === 0
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

function TermsTable({
  negotiation,
  verificationDetails,
}: {
  negotiation: DemoNegotiation;
  verificationDetails: VerificationCheckDetail[] | null;
}) {
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
        {verificationDetails && verificationDetails.length > 0
          ? verificationDetails.map((check) => {
              const state: VerificationState =
                check.verdict === "PASS"
                  ? "PASSED"
                  : check.verdict === "BLOCKED"
                    ? "BLOCKED"
                    : "RUNNING";

              return (
                <div className="table-row" role="row" key={check.name}>
                  <div>
                    <strong>{check.name}</strong>
                    <div style={{ fontSize: "11px", color: "var(--muted)" }}>
                      {check.detail}
                    </div>
                  </div>
                  <span>Mandate v2 rules</span>
                  <span>Evaluated</span>
                  <span>
                    <StatusMark state={state} />
                  </span>
                </div>
              );
            })
          : negotiation.verification_checks.map((check) => {
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

function ShopperApprovalModal({
  isOpen,
  onClose,
  onConfirm,
  negotiation,
  verification,
  isCapturing,
}: {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
  negotiation: DemoNegotiation;
  verification: DealVerificationResponse;
  isCapturing: boolean;
}) {
  if (!isOpen) return null;

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div className="modal-card">
        <div className="modal-header">
          <h3 id="modal-title">Shopper Final Approval</h3>
          <p>Deterministic verification succeeded. Review and approve final capture.</p>
        </div>
        <div className="modal-body">
          <div className="modal-items">
            {verification.products.map((p) => (
              <div className="modal-item-row" key={p.product_identifier}>
                <div className="modal-item-desc">
                  <strong>{p.name}</strong>
                  <small>
                    SKU: {p.product_identifier} · Category: {p.canonical_category}
                  </small>
                </div>
                <span>{formatCents(p.price_cents, negotiation.currency)}</span>
              </div>
            ))}
            <div className="modal-total-row">
              <span>Total capture amount</span>
              <span>{formatCents(negotiation.proposed_total_cents, negotiation.currency)}</span>
            </div>
          </div>
          <div className="modal-consent-notice">
            <strong>Single-Use Consent Notice:</strong> Approving this transaction issues an atomic,
            cryptographically-bound consent token for Mandate{" "}
            <span className="mono">{verification.mandate_id.slice(0, 16)}…</span> and releases funds to{" "}
            <span className="mono">merchant.example</span>. Replay or tampering is prevented by the
            database consent contract.
          </div>
        </div>
        <div className="modal-footer">
          <button
            className="modal-cancel-button"
            type="button"
            onClick={onClose}
            disabled={isCapturing}
          >
            Cancel
          </button>
          <button
            className="modal-confirm-button"
            type="button"
            onClick={onConfirm}
            disabled={isCapturing}
          >
            {isCapturing ? "Capturing via PayPal…" : `Approve & Pay ${formatCents(negotiation.proposed_total_cents, negotiation.currency)}`}
          </button>
        </div>
      </div>
    </div>
  );
}

function PaymentRail({
  negotiation,
  verification,
  captureResult,
  onOpenApprovalModal,
  isCapturing,
}: {
  negotiation: DemoNegotiation;
  verification: DealVerificationResponse | null;
  captureResult: DealApprovalCaptureResponse | null;
  onOpenApprovalModal: () => void;
  isCapturing: boolean;
}) {
  const [evidenceOpen, setEvidenceOpen] = useState(false);

  const statusText = captureResult
    ? "Payment captured via PayPal Sandbox"
    : verification?.verdict === "PASS"
      ? "Verified · Shopper approval required"
      : verification?.verdict === "BLOCKED"
        ? "Blocked by Process B security gate"
        : "Awaiting security verification";

  const isCaptureReady = Boolean(verification?.payment_ready && !captureResult && !isCapturing);
  const capturedAmount = captureResult
    ? captureResult.captured_amount_cents
    : negotiation.captured_amount_cents;

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
          <strong>{formatCents(capturedAmount, negotiation.currency)}</strong>
        </div>
      </div>
      <div className="payment-status">
        <p className="payment-status-title">{statusText}</p>
        <p>
          {captureResult
            ? "Capture completed under cryptographic consent."
            : "No capture until every required check completes."}
        </p>
        <button
          className="text-link"
          type="button"
          aria-expanded={evidenceOpen}
          onClick={() => setEvidenceOpen((open) => !open)}
        >
          {evidenceOpen ? "Hide evidence" : "View evidence"}
        </button>
      </div>
      <div className="capture-block">
        <button
          className="capture-button"
          type="button"
          disabled={!isCaptureReady}
          onClick={onOpenApprovalModal}
        >
          {isCapturing
            ? "Capturing…"
            : captureResult
              ? `Captured ${formatCents(capturedAmount, negotiation.currency)}`
              : `Review & Pay ${formatCents(negotiation.proposed_total_cents, negotiation.currency)}`}
        </button>
        <p>
          {captureResult
            ? "PayPal Sandbox Transaction Completed"
            : verification?.verdict === "PASS"
              ? "All 14 checks passed · Ready for checkout"
              : "Waiting on checks · Powered by PayPal Sandbox"}
        </p>
      </div>
      {evidenceOpen && (
        <div className="evidence-list" aria-label="Evidence details">
          <div>
            <span>Mandate validity</span>
            <strong>{verification ? "Verified (Ed25519)" : "Pending"}</strong>
          </div>
          <div>
            <span>Merchant reference</span>
            <strong>{verification ? "merchant.example" : "Pending"}</strong>
          </div>
          <div>
            <span>PayPal Order ID</span>
            <strong className="mono">{captureResult?.paypal_order_id ?? verification?.paypal_order_id ?? "None"}</strong>
          </div>
          <div>
            <span>PayPal Auth ID</span>
            <strong className="mono">{captureResult?.paypal_authorization_id ?? verification?.paypal_auth_id ?? "None"}</strong>
          </div>
          <div>
            <span>PayPal Capture ID</span>
            <strong className="mono">{captureResult?.paypal_capture_id ?? "None"}</strong>
          </div>
          <div>
            <span>Negotiation ID</span>
            <strong className="mono">{negotiation.negotiation_id}</strong>
          </div>
        </div>
      )}
      <p className="rail-footnote">Capture is backend-confirmed, never inferred from a click.</p>
    </aside>
  );
}

function AuditTrailSection({
  auditEvents,
  negotiationId,
}: {
  auditEvents: AuditTrailItem[];
  negotiationId: string;
}) {
  if (auditEvents.length === 0) return null;

  return (
    <section className="audit-section" id="activity" aria-labelledby="audit-heading">
      <h2 id="audit-heading">Audit Log ({negotiationId})</h2>
      <table className="audit-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Type</th>
            <th>Gate / Target</th>
            <th>Verdict / Status</th>
            <th>Amount</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody>
          {auditEvents.map((evt) => (
            <tr key={evt.event_id}>
              <td className="mono">{new Date(evt.created_at).toLocaleTimeString()}</td>
              <td>{evt.event_type}</td>
              <td>
                <span className="mono">{evt.name_or_gate}</span>
              </td>
              <td>
                <span
                  className={`badge-tag ${
                    evt.decision_or_status === "PASSED" ||
                    evt.decision_or_status === "CAPTURED" ||
                    evt.decision_or_status === "CLAIMED"
                      ? "badge-tag-pass"
                      : "badge-tag-blocked"
                  }`}
                >
                  {evt.decision_or_status}
                </span>
              </td>
              <td>{evt.amount_cents ? formatCents(evt.amount_cents) : "—"}</td>
              <td className="mono">{evt.details ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default function Home() {
  const [negotiation, setNegotiation] = useState<DemoNegotiation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [transcriptOpen, setTranscriptOpen] = useState(false);

  const [verifying, setVerifying] = useState(false);
  const [verificationResult, setVerificationResult] = useState<DealVerificationResponse | null>(null);
  const [verificationError, setVerificationError] = useState<string | null>(null);

  const [approvalModalOpen, setApprovalModalOpen] = useState(false);
  const [isCapturing, setIsCapturing] = useState(false);
  const [captureResult, setCaptureResult] = useState<DealApprovalCaptureResponse | null>(null);
  const [captureError, setCaptureError] = useState<string | null>(null);

  const [auditEvents, setAuditEvents] = useState<AuditTrailItem[]>([]);

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

  const refreshAuditTrail = useCallback((negotiationId: string) => {
    fetchAuditTrail(negotiationId)
      .then((res) => setAuditEvents(res.events))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchDemoNegotiation(controller.signal)
      .then((neg) => {
        setNegotiation(neg);
        refreshAuditTrail(neg.negotiation_id);
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError("We couldn’t load the demo negotiation. Check that the backend is running and try again.");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [refreshAuditTrail]);

  const handleVerifyDeal = async () => {
    if (!negotiation) return;
    setVerifying(true);
    setVerificationError(null);
    try {
      const result = await verifyDeal(negotiation.negotiation_id);
      setVerificationResult(result);
      if (result.products && result.products.length > 0) {
        setNegotiation((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            status: result.verdict === "PASS" ? "VERIFIED" : "VERIFYING",
            products: result.products.map((p) => ({
              name: p.name,
              price_cents: p.price_cents,
              illustrative: false,
              verified: p.verified,
              product_identifier: p.product_identifier,
              canonical_category: p.canonical_category,
              merchant_domain: p.merchant_domain,
              offer_url: p.offer_url,
            })),
          };
        });
      }
      refreshAuditTrail(negotiation.negotiation_id);
    } catch (err: unknown) {
      setVerificationError(err instanceof Error ? err.message : "Verification failed");
    } finally {
      setVerifying(false);
    }
  };

  const handleApproveAndCapture = async () => {
    if (!negotiation || !verificationResult) return;
    setIsCapturing(true);
    setCaptureError(null);
    try {
      const response = await approveAndCaptureDeal({
        negotiation_id: negotiation.negotiation_id,
        mandate_id: verificationResult.mandate_id,
        shopper_id: "shopper_avery_singh",
        confirmed_amount_cents: negotiation.proposed_total_cents,
        confirmed_merchant: "merchant.example",
      });
      setCaptureResult(response);
      setApprovalModalOpen(false);
      setNegotiation((prev) => (prev ? { ...prev, status: "CAPTURED", captured_amount_cents: response.captured_amount_cents } : prev));
      refreshAuditTrail(negotiation.negotiation_id);
    } catch (err: unknown) {
      setCaptureError(err instanceof Error ? err.message : "Capture failed");
    } finally {
      setIsCapturing(false);
    }
  };

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
              Active deal · {negotiation.is_demo && !verificationResult ? "demo data" : "cryptographically verified"} · {negotiation.merchant} ·{" "}
              <span className="mono">{negotiation.negotiation_id}</span>
            </p>
            <h1 id="deal-title">{negotiation.shopping_goal}</h1>
            <p>Proposed by an AI agent. Only the independent verifier can approve it.</p>
          </section>

          <section className="figures-row" aria-label="Deal figures">
            <div className="hero-figure">
              <span>Proposed deal</span>
              <strong>{formatCents(negotiation.proposed_total_cents, negotiation.currency)}</strong>
              <small>
                {captureResult
                  ? "Captured via PayPal Sandbox"
                  : verificationResult?.verdict === "PASS"
                    ? "Verified · Shopper approval ready"
                    : "Awaiting independent verification"}
              </small>
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

          <section className="verify-banner">
            <div>
              <p>
                <strong>Process B Mandate Verification Gate</strong>
              </p>
              <small>
                {verificationResult
                  ? `Evaluated 14 deterministic gates: Status is ${verificationResult.verdict}`
                  : "Cryptographic Ed25519 mandate, category registry, and ceiling verification"}
              </small>
              {verificationError && (
                <div style={{ color: "#cf222e", fontSize: "12px", marginTop: "4px" }}>
                  {verificationError}
                </div>
              )}
            </div>
            <button
              className="primary-action-button"
              type="button"
              disabled={verifying}
              onClick={handleVerifyDeal}
            >
              {verifying ? "Verifying with Process B…" : verificationResult ? "Re-verify Deal" : "Verify Deal (Process B)"}
            </button>
          </section>

          {captureResult && (
            <section className="capture-success-card" role="alert">
              <div className="capture-success-header">
                <span>✓</span>
                <span>Payment Captured Successfully</span>
              </div>
              <div style={{ fontSize: "13px", lineHeight: "1.6" }}>
                <div>PayPal Order ID: <strong className="mono">{captureResult.paypal_order_id}</strong></div>
                <div>PayPal Authorization ID: <strong className="mono">{captureResult.paypal_authorization_id}</strong></div>
                <div>PayPal Capture ID: <strong className="mono">{captureResult.paypal_capture_id}</strong></div>
                <div>Amount: <strong>{formatCents(captureResult.captured_amount_cents, captureResult.currency)}</strong></div>
                <div>Timestamp: <span className="mono">{new Date(captureResult.timestamp).toLocaleString()}</span></div>
              </div>
            </section>
          )}

          {captureError && (
            <div style={{ marginTop: "16px", padding: "12px 16px", background: "#ffebe9", border: "1px solid #cf222e", borderRadius: "8px", color: "#cf222e", fontSize: "13px" }}>
              <strong>Capture Blocked:</strong> {captureError}
            </div>
          )}

          {verificationResult && (
            <section className="evidence-card" aria-labelledby="evidence-heading">
              <h3 id="evidence-heading">Cryptographic Verification Evidence (Process B & C)</h3>
              <div className="evidence-grid">
                <div className="evidence-item">
                  <span>Mandate ID</span>
                  <strong className="mono">{verificationResult.mandate_id}</strong>
                </div>
                <div className="evidence-item">
                  <span>Mandate Digest (SHA-256)</span>
                  <strong className="mono">{verificationResult.mandate_digest_b64.slice(0, 24)}…</strong>
                </div>
                <div className="evidence-item">
                  <span>Authorized Spending Cap</span>
                  <strong>{formatCents(verificationResult.authorized_cap_cents)}</strong>
                </div>
                <div className="evidence-item">
                  <span>PayPal Order ID</span>
                  <strong className="mono">{verificationResult.paypal_order_id}</strong>
                </div>
                <div className="evidence-item">
                  <span>PayPal Auth ID</span>
                  <strong className="mono">{verificationResult.paypal_auth_id}</strong>
                </div>
                <div className="evidence-item">
                  <span>Category Registry Checks</span>
                  <strong>
                    <span className="badge-tag badge-tag-pass">Human-Reviewed Registry PASS</span>
                  </strong>
                </div>
              </div>
            </section>
          )}

          <section className="products-section" aria-labelledby="products-heading">
            <h2 id="products-heading">Candidate products</h2>
            {negotiation.products.map((product) => (
              <ProductRow product={product} currency={negotiation.currency} key={product.name} />
            ))}
            <p className="product-caption">
              {verificationResult
                ? "Items matched against human-reviewed category registry and cryptographically referenced"
                : "Illustrative catalogue data · product identity awaiting independent verification"}
            </p>
          </section>

          <section className="negotiation-section" aria-labelledby="negotiation-heading">
            <h2 id="negotiation-heading">Negotiation</h2>
            <NegotiationLine negotiation={negotiation} />
            <p className="negotiation-caption">
              Illustrative demo sequence ·{" "}
              <button
                type="button"
                className="inline-link"
                onClick={() => setTranscriptOpen((open) => !open)}
              >
                {transcriptOpen ? "Hide transcript" : "View full transcript"}
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

          <TermsTable
            negotiation={negotiation}
            verificationDetails={verificationResult ? verificationResult.checks : null}
          />

          <AuditTrailSection
            auditEvents={auditEvents}
            negotiationId={negotiation.negotiation_id}
          />
        </section>

        <PaymentRail
          negotiation={negotiation}
          verification={verificationResult}
          captureResult={captureResult}
          onOpenApprovalModal={() => setApprovalModalOpen(true)}
          isCapturing={isCapturing}
        />
      </div>

      {verificationResult && (
        <ShopperApprovalModal
          isOpen={approvalModalOpen}
          onClose={() => setApprovalModalOpen(false)}
          onConfirm={handleApproveAndCapture}
          negotiation={negotiation}
          verification={verificationResult}
          isCapturing={isCapturing}
        />
      )}
    </main>
  );
}
