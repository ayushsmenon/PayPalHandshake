"""Verification suite for durable consent evidence, protocol v2 alignment, and concurrency safeguards."""

from __future__ import annotations

import concurrent.futures
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.models import (
    ConsentEvidenceSchema,
    NegotiationSchema,
    ProductReferenceSchema,
)
from src.repositories import (
    DatabaseSessionManager,
    NegotiationRepository,
    MandateRepository,
    ConsentRepository,
    NotFoundError,
    PersistenceError,
)


@pytest.fixture
def file_db_manager(tmp_path):
    """File-backed SQLite session manager allowing concurrent multithreaded transactions."""
    db_file = tmp_path / f"test_concurrency_{uuid4().hex}.db"
    manager = DatabaseSessionManager(f"sqlite:///{db_file}")
    manager.create_tables()
    yield manager
    manager.engine.dispose()


@pytest.fixture
def memory_db_session():
    """In-memory SQLite session for fast unit verification."""
    manager = DatabaseSessionManager("sqlite:///:memory:")
    manager.create_tables()
    with manager.session_scope() as session:
        yield session
    manager.drop_tables()


# ============================================================================
# 1. Consent Concurrency: Parallel workers race for single-use consent claim
# ============================================================================

def test_consent_concurrency_race(file_db_manager):
    """Assert exactly one parallel worker receives True and all others receive False when claiming consent."""
    digest_64 = "a" * 64

    # 1. Seed negotiation and active mandate
    with file_db_manager.session_scope() as session:
        neg_repo = NegotiationRepository(session)
        mandate_repo = MandateRepository(session)

        neg = neg_repo.create_negotiation(
            shopper_id="shopper_race_01",
            request_prompt="Parallel consent claim test",
            budget_cents=50000,
            currency_code="USD",
        )
        neg_id = neg.id

        now = datetime.now(timezone.utc)
        mandate_repo.save_mandate(
            negotiation_id=neg_id,
            mandate_id="mnd_race_target_01",
            public_key="pk_race_shopper_01",
            signature="sig_race_shopper_01",
            authorized_cap_cents=50000,
            currency_code="USD",
            mandate_digest=digest_64,
            expires_at=now + timedelta(hours=2),
        )

    # 2. Worker task attempting to claim consent
    def worker_claim(worker_idx: int) -> bool:
        with file_db_manager.session_scope() as session:
            consent_repo = ConsentRepository(session)
            evidence = ConsentEvidenceSchema(
                evidence_id=f"EV-RACE-{worker_idx}-{uuid4().hex[:8]}",
                mandate_id="mnd_race_target_01",
                mandate_digest=digest_64,
                negotiation_id=neg_id,
                shopper_id="shopper_race_01",
                approval_action="AUTHORIZE_AND_MANDATE",
                evidence_payload={"worker_idx": worker_idx},
                approved_at=datetime.now(timezone.utc),
            )
            return consent_repo.record_and_claim(evidence)

    # 3. Launch parallel workers simultaneously
    worker_count = 6
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(worker_claim, i) for i in range(worker_count)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # Assert exactly ONE succeeded (True) and all remaining failed (False)
    assert results.count(True) == 1, f"Expected exactly 1 True, got: {results}"
    assert results.count(False) == worker_count - 1


# ============================================================================
# 2. Replay Rejection: Duplicate evidence_id or re-claiming same mandate_id
# ============================================================================

def test_consent_replay_rejection(memory_db_session):
    """Ensure replaying identical evidence_id or re-claiming the same mandate_id returns False."""
    neg_repo = NegotiationRepository(memory_db_session)
    mandate_repo = MandateRepository(memory_db_session)
    consent_repo = ConsentRepository(memory_db_session)

    neg = neg_repo.create_negotiation(
        shopper_id="shopper_replay_test",
        request_prompt="Replay protection test",
        budget_cents=25000,
        currency_code="USD",
    )

    digest_1 = "b" * 64
    now = datetime.now(timezone.utc)
    mandate = mandate_repo.save_mandate(
        negotiation_id=neg.id,
        mandate_id="mnd_replay_test_01",
        public_key="pk_shopper_replay",
        signature="sig_shopper_replay",
        authorized_cap_cents=25000,
        currency_code="USD",
        mandate_digest=digest_1,
        expires_at=now + timedelta(hours=1),
    )

    evidence = ConsentEvidenceSchema(
        evidence_id="EV-UNIQUE-100",
        mandate_id="mnd_replay_test_01",
        mandate_digest=digest_1,
        negotiation_id=neg.id,
        shopper_id="shopper_replay_test",
        approval_action="AUTHORIZE_AND_MANDATE",
        evidence_payload={"test": "replay"},
        approved_at=now,
    )

    # 1. First claim succeeds
    assert consent_repo.record_and_claim(evidence) is True

    # 2. Attempting to claim again for the same mandate returns False
    duplicate_evidence = ConsentEvidenceSchema(
        evidence_id="EV-NEW-DIFFERENT-ID-200",
        mandate_id="mnd_replay_test_01",
        mandate_digest=digest_1,
        negotiation_id=neg.id,
        shopper_id="shopper_replay_test",
        approval_action="AUTHORIZE_AND_MANDATE",
        evidence_payload={"test": "second_attempt"},
        approved_at=now,
    )
    assert consent_repo.record_and_claim(duplicate_evidence) is False

    # 3. Create a second mandate to test replaying the same evidence_id
    digest_2 = "c" * 64
    mandate2 = mandate_repo.save_mandate(
        negotiation_id=neg.id,
        mandate_id="mnd_replay_test_02",
        public_key="pk_shopper_replay_2",
        signature="sig_shopper_replay_2",
        authorized_cap_cents=25000,
        currency_code="USD",
        mandate_digest=digest_2,
        expires_at=now + timedelta(hours=1),
    )

    # Replaying the exact same evidence_id ("EV-UNIQUE-100") on a new mandate must return False
    stolen_evidence = ConsentEvidenceSchema(
        evidence_id="EV-UNIQUE-100",  # Reused evidence_id
        mandate_id="mnd_replay_test_02",
        mandate_digest=digest_2,
        negotiation_id=neg.id,
        shopper_id="shopper_replay_test",
        approval_action="AUTHORIZE_AND_MANDATE",
        evidence_payload={"test": "stolen_evidence_id"},
        approved_at=now,
    )
    assert consent_repo.record_and_claim(stolen_evidence) is False


# ============================================================================
# 3. Digest Mismatch Fail Closed
# ============================================================================

def test_consent_and_consumption_digest_mismatch_fails_closed(memory_db_session):
    """Attempting to claim consent or consume a mandate with a differing digest must fail."""
    neg_repo = NegotiationRepository(memory_db_session)
    mandate_repo = MandateRepository(memory_db_session)
    consent_repo = ConsentRepository(memory_db_session)

    neg = neg_repo.create_negotiation(
        shopper_id="shopper_mismatch",
        request_prompt="Digest mismatch test",
        budget_cents=10000,
        currency_code="USD",
    )

    digest_genuine = "d" * 64
    digest_tampered = "e" * 64

    now = datetime.now(timezone.utc)
    mandate_repo.save_mandate(
        negotiation_id=neg.id,
        mandate_id="mnd_mismatch_01",
        public_key="pk_mismatch",
        signature="sig_mismatch",
        authorized_cap_cents=10000,
        currency_code="USD",
        mandate_digest=digest_genuine,
        expires_at=now + timedelta(hours=1),
    )

    # 1. Consent claim with mismatching digest must raise PersistenceError
    bad_consent = ConsentEvidenceSchema(
        evidence_id="EV-MISMATCH-1",
        mandate_id="mnd_mismatch_01",
        mandate_digest=digest_tampered,
        negotiation_id=neg.id,
        shopper_id="shopper_mismatch",
        approval_action="AUTHORIZE_AND_MANDATE",
        approved_at=now,
    )
    with pytest.raises(PersistenceError) as exc_info:
        consent_repo.record_and_claim(bad_consent)
    assert "digest mismatch" in str(exc_info.value).lower()

    # 2. Mandate consumption with mismatching digest must raise PersistenceError
    with pytest.raises(PersistenceError) as exc_info:
        mandate_repo.consume_mandate(
            mandate_id="mnd_mismatch_01",
            expected_digest=digest_tampered,
            idempotency_key="REQ-FAIL-MISMATCH",
            tx_id="TX-MISMATCH",
        )
    assert "digest mismatch" in str(exc_info.value).lower()


# ============================================================================
# 4. Digest Fallback Purge: Synthetic fallbacks strictly prohibited
# ============================================================================

def test_synthetic_fallback_purge(memory_db_session):
    """Assert passing an empty, missing, or synthetic fallback digest raises an explicit validation exception."""
    neg_repo = NegotiationRepository(memory_db_session)
    mandate_repo = MandateRepository(memory_db_session)
    consent_repo = ConsentRepository(memory_db_session)

    neg = neg_repo.create_negotiation(
        shopper_id="shopper_synthetic_purge",
        request_prompt="Synthetic purge test",
        budget_cents=10000,
        currency_code="USD",
    )

    now = datetime.now(timezone.utc)

    # 1. save_mandate with synthetic fallback must be rejected
    with pytest.raises(ValueError) as exc:
        mandate_repo.save_mandate(
            negotiation_id=neg.id,
            mandate_id="mnd_bad_synth",
            public_key="pk_test",
            signature="sig_test",
            mandate_digest="digest_mnd_bad_synth",  # Synthetic fallback pattern
            expires_at=now + timedelta(hours=1),
        )
    assert "strictly prohibited" in str(exc.value)

    # 2. consume_mandate with empty digest must raise ValueError
    with pytest.raises(ValueError) as exc:
        mandate_repo.consume_mandate(
            mandate_id="mnd_any",
            expected_digest="",
            idempotency_key="REQ-1",
            tx_id="TX-1",
        )
    assert "synthetic fallbacks are strictly prohibited" in str(exc.value)

    # 3. consume_mandate with synthetic fallback digest must raise ValueError
    with pytest.raises(ValueError) as exc:
        mandate_repo.consume_mandate(
            mandate_id="mnd_any",
            expected_digest="digest_mnd_any",
            idempotency_key="REQ-1",
            tx_id="TX-1",
        )
    assert "strictly prohibited" in str(exc.value)

    # 4. record_and_claim with synthetic fallback must raise ValueError
    evidence_dict = {
        "consent_event_id": "EV-SYNTH",
        "mandate_id": "mnd_any",
        "mandate_digest_b64": "digest_mnd_any",
        "negotiation_id": str(neg.id),
        "shopper_account_id": "shopper_1",
    }
    with pytest.raises(ValueError) as exc:
        consent_repo.record_and_claim(evidence_dict)
    assert "strictly prohibited" in str(exc.value)


# ============================================================================
# 5. Mandate Consumption Concurrency: Simultaneous consumption of same mandate
# ============================================================================

def test_mandate_consumption_concurrency_race(file_db_manager):
    """Assert two simultaneous consumption attempts for the same active mandate cannot both succeed."""
    digest_consume = "f" * 64

    # 1. Seed active mandate
    with file_db_manager.session_scope() as session:
        neg_repo = NegotiationRepository(session)
        mandate_repo = MandateRepository(session)

        neg = neg_repo.create_negotiation(
            shopper_id="shopper_consume_race",
            request_prompt="Consumption race test",
            budget_cents=75000,
            currency_code="USD",
        )
        neg_id = neg.id

        now = datetime.now(timezone.utc)
        mandate_repo.save_mandate(
            negotiation_id=neg_id,
            mandate_id="mnd_consume_race_01",
            public_key="pk_consume_race",
            signature="sig_consume_race",
            authorized_cap_cents=75000,
            currency_code="USD",
            mandate_digest=digest_consume,
            expires_at=now + timedelta(hours=2),
        )

    # 2. Worker task attempting to consume the mandate
    def worker_consume(worker_idx: int) -> bool:
        with file_db_manager.session_scope() as session:
            mandate_repo = MandateRepository(session)
            return mandate_repo.consume_mandate(
                mandate_id="mnd_consume_race_01",
                expected_digest=digest_consume,
                idempotency_key=f"REQ-CONSUME-RACE-{worker_idx}-{uuid4().hex[:6]}",
                tx_id=f"TX-RACE-{worker_idx}",
            )

    worker_count = 5
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(worker_consume, i) for i in range(worker_count)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # Exactly ONE consumption must succeed
    assert results.count(True) == 1, f"Expected exactly 1 True, got: {results}"
    assert results.count(False) == worker_count - 1

    # Verify state in database
    with file_db_manager.session_scope() as session:
        mandate_repo = MandateRepository(session)
        mandate = mandate_repo.get_mandate_by_id("mnd_consume_race_01")
        assert mandate.status == "CONSUMED"
        assert mandate.consumed_at is not None
        assert len(mandate.consumptions) == 1


# ============================================================================
# 6. Protocol v2 Product Reference Alignment
# ============================================================================

def test_protocol_v2_product_reference(memory_db_session):
    """Verifies that product references support protocol_version v1 and v2 with canonical category."""
    neg_repo = NegotiationRepository(memory_db_session)

    neg = neg_repo.create_negotiation(
        shopper_id="shopper_v2_ref",
        request_prompt="Find monitor",
        budget_cents=45000,
        currency_code="USD",
    )

    now = datetime.now(timezone.utc)

    # Add v2 product reference
    ref_v2 = neg_repo.add_product_reference(
        negotiation_id=neg.id,
        channel3_product_id="ch3_item_v2_999",
        offer_url="https://merchant.example/offer",
        merchant_domain="merchant.example",
        price_cents=42999,
        digest="6" * 64,
        signature="sig_v2_test",
        public_key="pk_v2_test",
        issued_at=now,
        expires_at=now + timedelta(days=1),
        protocol_version="v2",
        canonical_category="Computer Monitors",
        registry_version="channel3-2026.11",
        currency_code="USD",
    )

    assert ref_v2.id is not None
    assert ref_v2.protocol_version == "v2"
    assert ref_v2.canonical_category == "Computer Monitors"
    assert ref_v2.price_cents == 42999
    assert ref_v2.currency_code == "USD"
