"""Bounded cleanup of expired and transient records; business history is retained."""

from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, exists, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.customers.models import (
    AccountToken,
    BackgroundJob,
    CustomerInvitation,
    EmailDelivery,
    Notification,
    OutboxEvent,
)
from app.identity.models import AuthSession, Organization, User
from app.identity.security import utcnow
from app.incidents.models import RadarProposal, RadarRun
from app.integrations.models import WebhookReceipt
from app.semantic.models import TicketEmbedding
from app.tenancy import set_tenant_context, set_user_context


def _purge(db: Session, model: type[Any], ids: list[UUID], dry_run: bool) -> int:
    if not ids:
        return 0
    if not dry_run:
        db.execute(delete(model).where(model.id.in_(ids)))
    return len(ids)


def cleanup_transient_data(
    db: Session, settings: Settings, *, dry_run: bool = False
) -> dict[str, int]:
    """Delete at most one configured batch per family/tenant and report counts."""
    now = utcnow()
    batch = settings.retention_batch_size
    counts: dict[str, int] = {}

    session_cutoff = now - timedelta(days=settings.session_retention_days)
    ids = list(
        db.scalars(
            select(AuthSession.id)
            .where(
                or_(
                    AuthSession.expires_at < session_cutoff,
                    AuthSession.revoked_at < session_cutoff,
                )
            )
            .order_by(AuthSession.id)
            .limit(batch)
        )
    )
    counts["sessions"] = _purge(db, AuthSession, ids, dry_run)

    token_cutoff = now - timedelta(days=settings.token_retention_days)
    user_ids = list(db.scalars(select(User.id).order_by(User.id)))
    remaining = batch
    token_count = 0
    for user_id in user_ids:
        if remaining <= 0:
            break
        set_user_context(db, user_id)
        token_ids = list(
            db.scalars(
                select(AccountToken.id)
                .where(AccountToken.expires_at < token_cutoff)
                .order_by(AccountToken.id)
                .limit(remaining)
            )
        )
        token_count += _purge(db, AccountToken, token_ids, dry_run)
        remaining -= len(token_ids)
    counts["account_tokens"] = token_count

    invite_cutoff = now - timedelta(days=settings.invitation_retention_days)
    org_ids = list(db.scalars(select(Organization.id).order_by(Organization.id)))
    counts.update(
        {
            "invitations": 0,
            "webhook_receipts": 0,
            "read_notifications": 0,
            "radar_runs": 0,
            "stale_embeddings": 0,
        }
    )
    for org_id in org_ids:
        if all(counts[field] >= batch for field in counts if field not in {"account_tokens"}):
            break
        set_tenant_context(db, org_id)
        remaining = batch - counts["invitations"]
        invite_ids = (
            list(
                db.scalars(
                    select(CustomerInvitation.id)
                    .where(
                        or_(
                            CustomerInvitation.expires_at < invite_cutoff,
                            CustomerInvitation.accepted_at < invite_cutoff,
                            CustomerInvitation.revoked_at < invite_cutoff,
                        )
                    )
                    .order_by(CustomerInvitation.id)
                    .limit(remaining)
                )
            )
            if remaining
            else []
        )
        counts["invitations"] += _purge(db, CustomerInvitation, invite_ids, dry_run)

        receipt_cutoff = now - timedelta(days=settings.webhook_receipt_retention_days)
        remaining = batch - counts["webhook_receipts"]
        receipt_ids = (
            list(
                db.scalars(
                    select(WebhookReceipt.id)
                    .where(WebhookReceipt.received_at < receipt_cutoff)
                    .order_by(WebhookReceipt.id)
                    .limit(remaining)
                )
            )
            if remaining
            else []
        )
        counts["webhook_receipts"] += _purge(db, WebhookReceipt, receipt_ids, dry_run)

        notification_cutoff = now - timedelta(days=settings.notification_retention_days)
        remaining = batch - counts["read_notifications"]
        notification_ids = (
            list(
                db.scalars(
                    select(Notification.id)
                    .where(
                        Notification.organization_id == org_id,
                        Notification.read_at.is_not(None),
                        Notification.read_at < notification_cutoff,
                    )
                    .order_by(Notification.id)
                    .limit(remaining)
                )
            )
            if remaining
            else []
        )
        counts["read_notifications"] += _purge(db, Notification, notification_ids, dry_run)

        radar_cutoff = now - timedelta(days=settings.radar_run_retention_days)
        remaining = batch - counts["radar_runs"]
        radar_ids = (
            list(
                db.scalars(
                    select(RadarRun.id)
                    .where(
                        RadarRun.organization_id == org_id,
                        RadarRun.finished_at < radar_cutoff,
                        RadarRun.status.in_(["completed", "failed"]),
                        ~exists(
                            select(RadarProposal.id).where(
                                RadarProposal.organization_id == org_id,
                                RadarProposal.run_id == RadarRun.id,
                            )
                        ),
                    )
                    .order_by(RadarRun.id)
                    .limit(remaining)
                )
            )
            if remaining
            else []
        )
        counts["radar_runs"] += _purge(db, RadarRun, radar_ids, dry_run)

        embedding_cutoff = now - timedelta(days=settings.stale_embedding_retention_days)
        remaining = batch - counts["stale_embeddings"]
        embedding_ids = (
            list(
                db.scalars(
                    select(TicketEmbedding.id)
                    .where(
                        TicketEmbedding.organization_id == org_id,
                        TicketEmbedding.is_current.is_(False),
                        TicketEmbedding.status == "stale",
                        TicketEmbedding.updated_at < embedding_cutoff,
                    )
                    .order_by(TicketEmbedding.id)
                    .limit(remaining)
                )
            )
            if remaining
            else []
        )
        counts["stale_embeddings"] += _purge(db, TicketEmbedding, embedding_ids, dry_run)

    job_cutoff = now - timedelta(days=settings.completed_job_retention_days)
    job_ids = list(
        db.scalars(
            select(BackgroundJob.id)
            .join(OutboxEvent, OutboxEvent.id == BackgroundJob.outbox_event_id)
            .where(
                BackgroundJob.status == "completed",
                BackgroundJob.completed_at < job_cutoff,
                OutboxEvent.processed_at.is_not(None),
                OutboxEvent.processed_at < job_cutoff,
            )
            .order_by(BackgroundJob.id)
            .limit(batch)
        )
    )
    counts["completed_jobs"] = _purge(db, BackgroundJob, job_ids, dry_run)
    event_ids = list(
        db.scalars(
            select(OutboxEvent.id)
            .where(OutboxEvent.processed_at.is_not(None), OutboxEvent.processed_at < job_cutoff)
            .order_by(OutboxEvent.id)
            .limit(batch)
        )
    )
    if event_ids and not dry_run:
        db.execute(delete(OutboxEvent).where(OutboxEvent.id.in_(event_ids)))
    counts["outbox_events"] = len(event_ids)

    delivery_cutoff = now - timedelta(days=settings.completed_job_retention_days)
    delivery_ids = list(
        db.scalars(
            select(EmailDelivery.id)
            .where(EmailDelivery.delivered_at < delivery_cutoff)
            .order_by(EmailDelivery.id)
            .limit(batch)
        )
    )
    counts["email_deliveries"] = _purge(db, EmailDelivery, delivery_ids, dry_run)
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return counts
