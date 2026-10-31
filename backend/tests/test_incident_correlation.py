import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.incidents.correlation import TicketText, candidate_groups, pair_signal, significant_terms
from app.incidents.schemas import IncidentUpdate, ProposalConfirm

FIXTURE = Path(__file__).parent / "fixtures" / "radar_pairs.json"


def _ticket(settings, record, category_match, minutes, index):
    category = UUID(int=1) if category_match else None
    instant = datetime(2026, 10, 31, tzinfo=UTC) + timedelta(minutes=minutes)
    return TicketText(
        id=uuid4(),
        organization_id=uuid4(),
        requester_contact_id=uuid4(),
        title=record["title"],
        body=record["body"],
        status="open",
        priority="normal",
        category_id=category,
        category_name="Fixture" if category_match else None,
        requester_name=f"Requester {index}",
        created_at=instant,
    )


def test_generic_terms_are_not_significant():
    assert significant_terms("Error problem help please not working") == frozenset()


def test_mutation_schemas_reject_values_that_would_violate_database_constraints():
    with pytest.raises(ValidationError):
        IncidentUpdate(expected_version=1, severity=None)
    with pytest.raises(ValidationError):
        ProposalConfirm(ticket_ids=[uuid4(), uuid4()], title="  ")


def test_pair_rules_use_terms_category_and_time(settings):
    left = _ticket(
        settings,
        {"title": "Payment timeout at checkout", "body": "Card payment timeout."},
        True,
        0,
        0,
    )
    right = _ticket(
        settings,
        {"title": "Checkout payment timeout", "body": "Card authorization timeout."},
        True,
        0,
        1,
    )
    signal = pair_signal(left, right, settings)
    assert signal is not None
    assert set(signal.shared_terms) >= {"payment", "timeout"}

    generic_left = _ticket(
        settings,
        {"title": "Error problem help", "body": "Something is not working."},
        False,
        0,
        2,
    )
    generic_right = _ticket(
        settings,
        {"title": "Help with a problem", "body": "The error is not working."},
        False,
        0,
        3,
    )
    assert pair_signal(generic_left, generic_right, settings) is None
    outside_window = _ticket(
        settings,
        {"title": "Checkout payment timeout", "body": "Card payment authorization timeout."},
        True,
        700,
        1,
    )
    assert pair_signal(left, outside_window, settings) is None


def test_fixture_metrics_and_simple_unigram_baseline(settings):
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    category = uuid4()
    truth: list[bool] = []
    detector: list[bool] = []
    baseline: list[bool] = []
    for case in cases:
        left = TicketText(
            id=uuid4(),
            organization_id=uuid4(),
            requester_contact_id=uuid4(),
            title=case["left"]["title"],
            body=case["left"]["body"],
            status="open",
            priority="normal",
            category_id=category if case["category_match"] else None,
            category_name="Fixture" if case["category_match"] else None,
            requester_name="A",
            created_at=datetime(2026, 10, 31, tzinfo=UTC),
        )
        right = TicketText(
            id=uuid4(),
            organization_id=uuid4(),
            requester_contact_id=uuid4(),
            title=case["right"]["title"],
            body=case["right"]["body"],
            status="open",
            priority="normal",
            category_id=category if case["category_match"] else None,
            category_name="Fixture" if case["category_match"] else None,
            requester_name="B",
            created_at=datetime(2026, 10, 31, tzinfo=UTC)
            + timedelta(minutes=case["minutes_apart"]),
        )
        truth.append(case["label"] == "related")
        detector.append(pair_signal(left, right, settings) is not None)
        baseline.append(bool(left.terms & right.terms))
    metrics = _binary_metrics(truth, detector)
    baseline_metrics = _binary_metrics(truth, baseline)
    assert metrics["precision"] >= 0.75
    assert metrics["recall"] >= 0.75
    assert metrics["precision"] > baseline_metrics["precision"]


def _binary_metrics(truth: list[bool], predicted: list[bool]) -> dict[str, float]:
    tp = sum(actual and guess for actual, guess in zip(truth, predicted, strict=True))
    fp = sum((not actual) and guess for actual, guess in zip(truth, predicted, strict=True))
    fn = sum(actual and (not guess) for actual, guess in zip(truth, predicted, strict=True))
    return {
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
    }


def test_multilingual_and_short_descriptions_are_safe(settings):
    assert significant_terms("La factura no carga") >= {"factura", "carga"}
    assert significant_terms("no") == frozenset()
    same_contact = [
        TicketText(
            id=uuid4(),
            organization_id=uuid4(),
            requester_contact_id=(contact := uuid4()),
            title="Payment gateway timeout",
            body="Card payment times out at gateway.",
            status="open",
            priority="normal",
            category_id=None,
            category_name=None,
            requester_name="A",
            created_at=datetime(2026, 10, 31, tzinfo=UTC),
        ),
        TicketText(
            id=uuid4(),
            organization_id=uuid4(),
            requester_contact_id=contact,
            title="Payment gateway timeout",
            body="Card payment times out at gateway.",
            status="open",
            priority="normal",
            category_id=None,
            category_name=None,
            requester_name="A",
            created_at=datetime(2026, 10, 31, tzinfo=UTC),
        ),
    ]
    assert candidate_groups(same_contact, settings) == []
