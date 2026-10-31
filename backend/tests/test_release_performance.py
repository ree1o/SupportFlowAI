"""Small, repeatable request-path measurements on disposable PostgreSQL data."""

import hashlib
import hmac
import json
import logging
import math
import os
import platform
import re
import time
from contextlib import ExitStack
from datetime import timedelta
from urllib.parse import urlencode
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.customers.models import OutboxEvent
from app.identity.models import Membership
from app.identity.security import utcnow
from app.incidents.correlation import candidate_groups
from app.incidents.service import _candidate_rows
from app.main import create_app
from app.semantic.models import MODEL_VERSION, TicketEmbedding
from app.tenancy import set_tenant_context
from app.tickets.models import Contact, Message, Ticket, Visibility

pytestmark = pytest.mark.integration
ORIGIN = "http://localhost:5173"


def _write(client, method: str, path: str, data: dict | None = None):
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    return client.request(
        method,
        f"/api{path}",
        json=data,
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
    )


def _measure(operation, repeats: int, expected: int) -> dict:
    durations = []
    errors = 0
    for _ in range(repeats):
        started = time.perf_counter()
        response = operation()
        durations.append((time.perf_counter() - started) * 1000)
        errors += response.status_code != expected
    assert errors == 0
    ordered = sorted(durations)
    return {
        "samples": repeats,
        "errors": errors,
        "p50_ms": round(ordered[math.ceil(0.50 * repeats) - 1], 2),
        "p95_ms": round(ordered[math.ceil(0.95 * repeats) - 1], 2),
        "mean_ms": round(sum(durations) / repeats, 2),
        "sequential_requests_per_second": round(1000 * repeats / sum(durations), 2),
    }


def test_release_request_path_benchmark(postgres, monkeypatch):
    print("benchmark: fixture ready", flush=True)
    settings, database, _ = postgres
    monkeypatch.setattr(logging.getLogger("supportflow.api"), "level", logging.WARNING)
    with ExitStack() as stack:
        owner = stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
        customer = stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
        registration = _write(
            owner,
            "POST",
            "/auth/register",
            {
                "email": "release-benchmark@example.com",
                "password": "disposable release benchmark passphrase",
                "full_name": "Release Benchmark Owner",
                "organization": {"name": "Release Benchmark", "slug": "release-benchmark"},
            },
        )
        assert registration.status_code == 201, registration.text
        print("benchmark: account created", flush=True)
        profile = registration.json()
        org_id = UUID(profile["selected_organization_id"])
        root = f"/organizations/{org_id}"
        contact_response = _write(
            owner,
            "POST",
            f"{root}/contacts",
            {"name": "Synthetic Benchmark Contact", "email": "release-customer@example.com"},
        )
        assert contact_response.status_code == 201, contact_response.text
        contact_id = UUID(contact_response.json()["id"])
        category_response = _write(owner, "POST", f"{root}/categories", {"name": "Synthetic"})
        assert category_response.status_code == 201, category_response.text
        category_id = UUID(category_response.json()["id"])
        with database.session_factory() as db:
            membership_id = db.scalar(
                select(Membership.id).where(Membership.organization_id == org_id)
            )
            assert membership_id is not None
            contact_ids = [contact_id]
            for index in range(4):
                contact = Contact(
                    organization_id=org_id,
                    name=f"Synthetic Contact {index}",
                    email=f"release-contact-{index}@example.com",
                )
                db.add(contact)
                db.flush()
                contact_ids.append(contact.id)

            def seed_batch(size: int, offset: int) -> list[UUID]:
                now = utcnow()
                ids = [uuid4() for _ in range(size)]
                tickets = []
                messages = []
                embeddings = []
                for index, ticket_id in enumerate(ids):
                    related_pair = index + offset < 6
                    terms = (
                        f"payment processor timeout group {index // 2}"
                        if related_pair
                        else f"synthetic case {index + offset}"
                    )
                    created = now - timedelta(minutes=(index + offset) % 350)
                    tickets.append(
                        Ticket(
                            id=ticket_id,
                            organization_id=org_id,
                            requester_contact_id=contact_ids[index % len(contact_ids)],
                            category_id=category_id,
                            assigned_membership_id=None,
                            created_by_membership_id=membership_id,
                            title=f"{terms} {index + offset}",
                            status="open",
                            priority="normal",
                            version=1,
                            created_at=created,
                            updated_at=created,
                        )
                    )
                    messages.append(
                        Message(
                            organization_id=org_id,
                            ticket_id=ticket_id,
                            author_membership_id=membership_id,
                            author_name="Synthetic agent",
                            visibility=Visibility.PUBLIC,
                            body=f"Customer reports {terms} during checkout.",
                            ticket_version=1,
                            created_at=created,
                        )
                    )
                    digest = hashlib.sha256(str(ticket_id).encode()).hexdigest()
                    vector = [1.0] + [0.0] * 383
                    embeddings.append(
                        TicketEmbedding(
                            organization_id=org_id,
                            ticket_id=ticket_id,
                            model_version=MODEL_VERSION,
                            content_digest=digest,
                            source_version=1,
                            is_current=True,
                            status="ready",
                            embedding=vector,
                            indexed_at=created,
                        )
                    )
                db.add_all(tickets)
                db.flush()
                db.add_all(messages)
                db.flush()
                db.add_all(embeddings)
                db.flush()
                return ids

            reports = {}
            all_ids: list[UUID] = []
            period_start = (utcnow() - timedelta(days=30)).isoformat()
            period_end = (utcnow() + timedelta(days=1)).isoformat()
            report_period = urlencode({"period_start": period_start, "period_end": period_end})
            for size, batch_size in ((100, 100), (500, 400)):
                print(f"benchmark: seeding {size} tickets", flush=True)
                all_ids.extend(seed_batch(batch_size, len(all_ids)))
                db.commit()
                set_tenant_context(db, org_id)
                reports[f"ticket_dataset_{size}"] = {
                    "ticket_list": _measure(
                        lambda: owner.get(f"/api{root}/tickets?page_size=100"), 20, 200
                    ),
                    "operations_report": _measure(
                        lambda: owner.get(f"/api{root}/reports/operations?{report_period}"),
                        20,
                        200,
                    ),
                    "semantic_related_query": _measure(
                        lambda: owner.get(f"/api{root}/tickets/{all_ids[0]}/related?limit=5"),
                        20,
                        200,
                    ),
                }
                started = time.perf_counter()
                ticket_rows = _candidate_rows(db, org_id, settings)
                candidate_groups(ticket_rows, settings)
                reports[f"radar_detection_{size}"] = {
                    "ticket_count": len(ticket_rows),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                }
                print(f"benchmark: measured {size} ticket queries", flush=True)

        def create_operation():
            return _write(
                owner,
                "POST",
                f"{root}/tickets",
                {
                    "requester_contact_id": str(contact_id),
                    "title": "Measured ticket creation",
                    "initial_message": "Synthetic request for the local request path benchmark.",
                },
            )

        reports["ticket_creation"] = _measure(create_operation, 10, 201)
        print("benchmark: measured ticket creation", flush=True)

        reply_records = []
        for _ in range(10):
            created = create_operation()
            assert created.status_code == 201, created.text
            reply_records.append(created.json())
        reply_iter = iter(reply_records)
        reports["staff_public_reply"] = _measure(
            lambda: _write(
                owner,
                "POST",
                f"{root}/tickets/{(record := next(reply_iter))['id']}/replies",
                {"expected_version": record["version"], "body": "Measured public response."},
            ),
            10,
            201,
        )
        print("benchmark: measured staff replies", flush=True)

        customer_registration = _write(
            owner,
            "POST",
            "/customer/register",
            {
                "email": "release-customer@example.com",
                "password": "disposable customer benchmark passphrase",
                "full_name": "Release Benchmark Customer",
                "organization_slug": "release-benchmark",
            },
        )
        assert customer_registration.status_code == 202
        print("benchmark: customer account created", flush=True)
        with database.session_factory() as db:
            event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key.like("verify:%")))
            assert event is not None
            token_match = re.search(r"customer/verify\?token=([^\s]+)", event.payload["body"])
            assert token_match is not None
        assert (
            _write(owner, "POST", "/customer/verify", {"token": token_match.group(1)}).status_code
            == 204
        )
        login = _write(
            customer,
            "POST",
            "/auth/login",
            {
                "email": "release-customer@example.com",
                "password": "disposable customer benchmark passphrase",
            },
        )
        assert login.status_code == 200, login.text
        customer_ticket = _write(
            customer,
            "POST",
            f"/customer/organizations/{org_id}/tickets",
            {"title": "Customer reply measurement", "message": "Synthetic initial request."},
        )
        assert customer_ticket.status_code == 201, customer_ticket.text
        version = customer_ticket.json()["version"]

        def customer_reply():
            nonlocal version
            result = _write(
                customer,
                "POST",
                f"/customer/organizations/{org_id}/tickets/{customer_ticket.json()['id']}/replies",
                {"expected_version": version, "body": "Synthetic customer follow-up."},
            )
            if result.status_code == 201:
                version = result.json()["version"]
            return result

        reports["customer_reply"] = _measure(customer_reply, 10, 201)

        source = _write(
            owner,
            "POST",
            f"{root}/integrations",
            {"name": "Benchmark source", "contact_id": str(contact_id)},
        )
        assert source.status_code == 201, source.text
        source_body = source.json()
        event_index = 0

        def webhook_request():
            nonlocal event_index
            event_index += 1
            timestamp = str(int(time.time()))
            event_id = f"perf-{event_index}-{uuid4()}"
            body = json.dumps(
                {
                    "type": "ticket.created",
                    "title": "Measured webhook ticket",
                    "message": "Synthetic webhook request.",
                },
                separators=(",", ":"),
            ).encode()
            signed = timestamp.encode() + b"." + event_id.encode() + b"." + body
            signature = hmac.new(
                source_body["credential"].encode(), signed, hashlib.sha256
            ).hexdigest()
            return owner.post(
                f"/api/integrations/{source_body['source']['id']}/events",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-SupportFlow-Event-Id": event_id,
                    "X-SupportFlow-Timestamp": timestamp,
                    "X-SupportFlow-Signature": f"v1={signature}",
                },
            )

        reports["webhook_ingestion"] = _measure(webhook_request, 10, 201)
        report = {
            "environment": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "cpu_description": platform.processor() or None,
                "logical_cpu_count": os.cpu_count(),
                "database": "PostgreSQL 16 + pgvector 0.8.1 in local Docker Compose",
                "data": "synthetic; 100 and 500 tickets in a per-test disposable schema",
                "request_mode": (
                    "sequential TestClient; no production endpoint or concurrency claim"
                ),
            },
            "measurements": reports,
        }
        print("RELEASE_PERFORMANCE_REPORT=" + json.dumps(report, sort_keys=True))
