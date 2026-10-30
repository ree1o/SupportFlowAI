import hashlib
import hmac
import json
import re
import time
from uuid import UUID

from cryptography.fernet import InvalidToken
from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.customers.service import decrypt, encrypt, integration_ticket_notifications
from app.errors import AppError
from app.identity import security
from app.identity.models import Membership, Role
from app.identity.service import AuthContext, organization_context, transaction
from app.integrations.models import IntegrationSource, WebhookReceipt
from app.integrations.schemas import (
    PublicMessageEvent,
    ReceiptPage,
    ReceiptView,
    SourceCreate,
    SourceUpdate,
    SourceView,
    TicketCreatedEvent,
)
from app.tickets import service as ticket_service
from app.tickets.models import Contact, Priority

SIGNATURE_TOLERANCE_SECONDS = 300
MAX_BODY_BYTES = 64 * 1024
EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SIGNATURE_PATTERN = re.compile(r"^v1=([0-9a-f]{64})$")


def _manager(db: Session, auth: AuthContext, org: UUID, *, write: bool = False) -> Membership:
    member = organization_context(db, auth.user.id, org, shared=write)[1]
    if write and member.role not in {Role.OWNER, Role.ADMINISTRATOR}:
        raise AppError(403, "forbidden", "Organization management permission required")
    return member


def _source(db: Session, org: UUID, source_id: UUID, *, lock: bool = False) -> IntegrationSource:
    query = select(IntegrationSource).where(
        IntegrationSource.organization_id == org, IntegrationSource.id == source_id
    )
    if lock:
        query = query.with_for_update()
    row = db.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise AppError(404, "not_found", "Integration source not found")
    return row


def list_sources(db: Session, auth: AuthContext, org: UUID) -> list[SourceView]:
    _manager(db, auth, org)
    return [
        SourceView.model_validate(row)
        for row in db.scalars(
            select(IntegrationSource)
            .where(IntegrationSource.organization_id == org)
            .order_by(IntegrationSource.created_at, IntegrationSource.id)
        )
    ]


def create_source(
    db: Session, auth: AuthContext, org: UUID, data: SourceCreate, settings: Settings
) -> tuple[SourceView, str]:
    with transaction(db, "Integration source conflicts with existing data"):
        _manager(db, auth, org, write=True)
        contact = db.scalar(
            select(Contact)
            .where(
                Contact.organization_id == org,
                Contact.id == data.contact_id,
                Contact.is_active.is_(True),
            )
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if contact is None:
            raise AppError(404, "not_found", "Active contact not found")
        raw = security.new_token()
        source = IntegrationSource(
            organization_id=org,
            contact_id=data.contact_id,
            name=data.name,
            secret_ciphertext=encrypt(settings, raw),
            credential_version=1,
        )
        db.add(source)
        db.flush()
        result = SourceView.model_validate(source)
    return result, raw


def update_source(
    db: Session, auth: AuthContext, org: UUID, source_id: UUID, data: SourceUpdate
) -> SourceView:
    with transaction(db, "Integration source conflicts with existing data"):
        _manager(db, auth, org, write=True)
        source = _source(db, org, source_id, lock=True)
        if source.revoked_at is not None:
            raise AppError(409, "source_revoked", "Revoked integration sources cannot be changed")
        for key, value in data.model_dump(exclude_unset=True).items():
            if value is not None:
                setattr(source, key, value)
        source.updated_at = security.utcnow()
        result = SourceView.model_validate(source)
    return result


def rotate_credential(
    db: Session, auth: AuthContext, org: UUID, source_id: UUID, settings: Settings
) -> tuple[SourceView, str]:
    with transaction(db, "Integration source conflicts with existing data"):
        _manager(db, auth, org, write=True)
        source = _source(db, org, source_id, lock=True)
        if source.revoked_at is not None:
            raise AppError(409, "source_revoked", "Revoked integration sources cannot be rotated")
        raw = security.new_token()
        source.secret_ciphertext = encrypt(settings, raw)
        source.credential_version += 1
        source.updated_at = security.utcnow()
        result = SourceView.model_validate(source)
    return result, raw


def revoke_source(db: Session, auth: AuthContext, org: UUID, source_id: UUID) -> None:
    with transaction(db, "Integration source conflicts with existing data"):
        _manager(db, auth, org, write=True)
        source = _source(db, org, source_id, lock=True)
        source.enabled = False
        source.revoked_at = source.revoked_at or security.utcnow()
        source.secret_ciphertext = ""
        source.updated_at = security.utcnow()


def receipts(
    db: Session,
    auth: AuthContext,
    org: UUID,
    source_id: UUID | None,
    status: str | None,
    page: int,
    size: int,
) -> ReceiptPage:
    _manager(db, auth, org)
    query = select(WebhookReceipt).where(WebhookReceipt.organization_id == org)
    if source_id is not None:
        query = query.where(WebhookReceipt.source_id == source_id)
    if status is not None:
        query = query.where(WebhookReceipt.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(WebhookReceipt.received_at.desc(), WebhookReceipt.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    return ReceiptPage(
        items=[ReceiptView.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=size,
    )


def _signature(secret: str, timestamp: str, event_id: str, raw_body: bytes) -> str:
    signed = timestamp.encode() + b"." + event_id.encode() + b"." + raw_body
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def ingest(
    db: Session,
    source_id: UUID,
    event_id: str,
    timestamp: str,
    signature: str,
    raw_body: bytes,
    settings: Settings,
) -> tuple[dict, bool]:
    if not EVENT_ID_PATTERN.fullmatch(event_id):
        raise AppError(400, "invalid_event_id", "A valid event ID is required")
    if len(raw_body) > MAX_BODY_BYTES:
        raise AppError(413, "payload_too_large", "Webhook payload exceeds the 64 KiB limit")
    if not timestamp.isascii() or not timestamp.isdigit() or len(timestamp) > 12:
        raise AppError(400, "invalid_timestamp", "A Unix timestamp is required")
    try:
        timestamp_int = int(timestamp)
    except ValueError as exc:
        raise AppError(400, "invalid_timestamp", "A Unix timestamp is required") from exc
    if abs(int(time.time()) - timestamp_int) > SIGNATURE_TOLERANCE_SECONDS:
        raise AppError(401, "signature_expired", "Webhook timestamp is outside the allowed window")
    match = SIGNATURE_PATTERN.fullmatch(signature)
    if match is None:
        raise AppError(401, "invalid_signature", "Webhook signature is invalid")
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppError(400, "invalid_json", "Webhook body must contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise AppError(422, "invalid_payload", "Webhook body must be a JSON object")
    event_type = payload.get("type")
    if not isinstance(event_type, str):
        raise AppError(422, "invalid_payload", "Unsupported webhook event type")
    if event_type not in {"ticket.created", "ticket.public_message"}:
        raise AppError(422, "invalid_payload", "Unsupported webhook event type")
    try:
        event = TypeAdapter(TicketCreatedEvent | PublicMessageEvent).validate_python(payload)
    except Exception as exc:
        raise AppError(422, "invalid_payload", "Webhook payload is invalid") from exc

    digest = hashlib.sha256(raw_body).hexdigest()
    with transaction(db, "Webhook event conflicts with an existing receipt"):
        source = db.scalar(
            select(IntegrationSource)
            .where(IntegrationSource.id == source_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if source is None or not source.enabled or source.revoked_at is not None:
            raise AppError(404, "not_found", "Integration source not found")
        try:
            secret = decrypt(settings, source.secret_ciphertext)
        except InvalidToken as exc:
            raise AppError(
                503, "integration_unavailable", "Integration source is unavailable"
            ) from exc
        expected = _signature(secret, timestamp, event_id, raw_body)
        if not hmac.compare_digest(expected, match.group(1)):
            raise AppError(401, "invalid_signature", "Webhook signature is invalid")

        statement = (
            insert(WebhookReceipt)
            .values(
                organization_id=source.organization_id,
                source_id=source.id,
                external_event_id=event_id,
                payload_digest=digest,
                event_type=event.type,
                received_at=security.utcnow(),
                processed_at=security.utcnow(),
                status="rejected",
                ticket_id=None,
                message_id=None,
                error_category="processing",
                result={},
            )
            .on_conflict_do_nothing(constraint="uq_webhook_receipts_event")
            .returning(WebhookReceipt.id)
        )
        receipt_id = db.scalar(statement)
        if receipt_id is None:
            prior = db.scalar(
                select(WebhookReceipt).where(
                    WebhookReceipt.source_id == source.id,
                    WebhookReceipt.external_event_id == event_id,
                )
            )
            if prior is None:
                raise AppError(503, "receipt_unavailable", "Webhook receipt could not be read")
            if prior.payload_digest != digest:
                raise AppError(
                    409, "event_id_reused", "Event ID was already used with another payload"
                )
            if prior.status == "rejected":
                error = prior.result.get("error", {})
                raise AppError(
                    int(error.get("status", 422)),
                    str(error.get("code", "event_rejected")),
                    "The original event was rejected",
                )
            return prior.result, True

        receipt = db.get(WebhookReceipt, receipt_id)
        assert receipt is not None
        rejection: AppError | None = None
        try:
            with db.begin_nested():
                if isinstance(event, TicketCreatedEvent):
                    ticket, message = ticket_service.create_integration_ticket(
                        db,
                        source.organization_id,
                        source.id,
                        source.name,
                        source.contact_id,
                        event.title,
                        event.message,
                        Priority(event.priority),
                    )
                    integration_ticket_notifications(db, ticket, security.utcnow(), settings)
                else:
                    ticket, message = ticket_service.append_integration_message(
                        db,
                        source.organization_id,
                        source.id,
                        source.name,
                        source.contact_id,
                        event.ticket_id,
                        event.message,
                    )
        except AppError as exc:
            receipt.status = "rejected"
            receipt.error_category = exc.code
            receipt.result = {"error": {"status": exc.status, "code": exc.code}}
            db.flush()
            rejection = exc
        else:
            receipt.status = "succeeded"
            receipt.error_category = None
            receipt.ticket_id = ticket.id
            receipt.message_id = message.id
            receipt.result = {
                "ticket_id": str(ticket.id),
                "message_id": str(message.id),
                "status_code": 201,
            }
            db.flush()
            rejection = None
        result = receipt.result
    if rejection is not None:
        raise AppError(
            rejection.status, rejection.code, "Webhook event was rejected"
        ) from rejection
    return result, False
