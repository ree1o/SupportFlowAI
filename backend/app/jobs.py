import logging
import smtplib
from datetime import timedelta
from email.message import EmailMessage
from uuid import UUID

from celery import Celery  # type: ignore[import-untyped]
from sqlalchemy import or_, select

from app.config import Settings
from app.customers.models import BackgroundJob, EmailDelivery, OutboxEvent
from app.database import Database
from app.identity import models as identity_models  # noqa: F401 -- load all FK targets in worker metadata
from app.identity.models import Organization
from app.identity.security import utcnow
from app.incidents import models as incident_models  # noqa: F401 -- load M7 metadata in worker
from app.semantic import models as semantic_models  # noqa: F401 -- load M8 metadata in worker
from app.sla import models as sla_models  # noqa: F401 -- load M5 metadata in worker
from app.tenancy import set_tenant_context
from app.tickets import models as ticket_models  # noqa: F401 -- load all FK targets in worker metadata

logger = logging.getLogger("supportflow.jobs")
settings = Settings()
celery_app = Celery("supportflow", broker=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_expires=3600,
    beat_schedule={
        "poll-transactional-outbox": {
            "task": "supportflow.poll_outbox",
            "schedule": 10.0,
        },
        "scan-sla-deadlines": {
            "task": "supportflow.scan_sla_deadlines",
            "schedule": 60.0,
        },
        "schedule-incident-radar": {
            "task": "supportflow.schedule_incident_radar",
            "schedule": 900.0,
        },
        "recover-incident-radar-runs": {
            "task": "supportflow.recover_incident_radar_runs",
            "schedule": 30.0,
        },
        "backfill-semantic-index": {
            "task": "supportflow.backfill_semantic_index",
            "schedule": 60.0,
        },
        "cleanup-transient-data": {
            "task": "supportflow.cleanup_transient_data",
            "schedule": 86400.0,
        },
    },
)


def send_email(payload: dict) -> None:
    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = payload["to"]
    message["Subject"] = payload["subject"]
    message.set_content(payload["body"])
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.send_message(message)


def process_one(database: Database) -> bool:
    now = utcnow()
    with database.session() as db:
        row = db.execute(
            select(BackgroundJob, OutboxEvent)
            .join(OutboxEvent, OutboxEvent.id == BackgroundJob.outbox_event_id)
            .where(
                OutboxEvent.processed_at.is_(None),
                BackgroundJob.available_at <= now,
                or_(
                    BackgroundJob.status == "pending",
                    (BackgroundJob.status == "processing") & (BackgroundJob.locked_until < now),
                ),
            )
            .order_by(BackgroundJob.available_at, BackgroundJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if row is None:
            return False
        job, event = row
        job.status = "processing"
        job.attempts += 1
        job.locked_until = now + timedelta(minutes=5)
        db.commit()
        job_id = job.id
        event_id = event.id
        event_key = event.event_key
        organization_id = event.organization_id
        payload = dict(event.payload)

    try:
        if event_key.startswith("email:"):
            raise ValueError("Invalid event key")
        # A completed marker prevents routine task redelivery from sending again.
        if event_key.startswith("semantic-index:"):
            from app.semantic.service import process_outbox_embedding

            with database.session() as db:
                if organization_id is None:
                    raise ValueError("Tenant-scoped semantic event is missing its organization")
                set_tenant_context(db, organization_id)
                process_outbox_embedding(db, UUID(payload["embedding_id"]), Settings())
        else:
            with database.session() as db:
                if db.scalar(select(EmailDelivery.id).where(EmailDelivery.event_key == event_key)):
                    mark_complete(database, job_id, event_id, event_key)
                    return True
            send_email(payload)
        mark_complete(database, job_id, event_id, event_key)
    except Exception as exc:
        with database.session() as db:
            current_job = db.scalar(
                select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update()
            )
            if current_job is None or current_job.status == "completed":
                return True
            current_job.locked_until = None
            # Persist exception class only: SMTP exception messages can contain addresses.
            current_job.last_error = type(exc).__name__[:120]
            max_attempts = (
                Settings().semantic_index_max_attempts
                if event_key.startswith("semantic-index:")
                else 10
            )
            if current_job.attempts >= max_attempts:
                current_job.status = "failed"
            else:
                current_job.status = "pending"
                current_job.available_at = utcnow() + timedelta(
                    seconds=min(3600, 2**current_job.attempts * 5)
                )
            if event_key.startswith("semantic-index:"):
                from app.semantic.service import fail_outbox_embedding

                try:
                    if organization_id is None:
                        raise ValueError("Tenant-scoped semantic event is missing its organization")
                    set_tenant_context(db, organization_id)
                    fail_outbox_embedding(
                        db,
                        UUID(payload["embedding_id"]),
                        current_job.attempts,
                        type(exc).__name__,
                        Settings(),
                    )
                except Exception:
                    db.rollback()
            db.commit()
        logger.warning(
            "background_delivery_failed job_id=%s error_type=%s",
            str(job_id),
            type(exc).__name__,
        )
    return True


def mark_complete(database: Database, job_id: UUID, event_id: UUID, event_key: str) -> None:
    with database.session() as db:
        job = db.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update())
        event = db.get(OutboxEvent, event_id)
        if job is None or event is None:
            return
        if job.status != "completed":
            if not event_key.startswith("semantic-index:") and (
                db.scalar(select(EmailDelivery.id).where(EmailDelivery.event_key == event_key))
                is None
            ):
                db.add(EmailDelivery(event_key=event_key))
            now = utcnow()
            job.status = "completed"
            job.completed_at = now
            job.locked_until = None
            job.last_error = None
            event.processed_at = now
        db.commit()


@celery_app.task(
    name="supportflow.poll_outbox",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def poll_outbox() -> int:
    database = Database(Settings())
    try:
        processed = 0
        for _ in range(25):
            if not process_one(database):
                break
            processed += 1
        return processed
    finally:
        database.dispose()


@celery_app.task(
    name="supportflow.scan_sla_deadlines",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def scan_sla_deadlines() -> int:
    from app.sla.service import scan_deadlines

    database = Database(Settings())
    try:
        with database.session() as db:
            organization_ids = list(db.scalars(select(Organization.id).order_by(Organization.id)))
            count = 0
            for organization_id in organization_ids:
                set_tenant_context(db, organization_id)
                count += scan_deadlines(db, batch_size=100)
                db.commit()
            return count
    except Exception:
        database.dispose()
        raise
    finally:
        database.dispose()


@celery_app.task(
    name="supportflow.detect_incident_run",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def detect_incident_run(organization_id: str, run_id: str) -> int:
    from app.incidents.service import process_detection_run

    database = Database(Settings())
    try:
        with database.session() as db:
            set_tenant_context(db, UUID(organization_id))
            return process_detection_run(db, UUID(organization_id), UUID(run_id), Settings())
    finally:
        database.dispose()


@celery_app.task(
    name="supportflow.recover_incident_radar_runs",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def recover_incident_radar_runs() -> int:
    from app.incidents.service import load_pending_run_ids

    database = Database(Settings())
    try:
        with database.session() as db:
            organization_ids = list(db.scalars(select(Organization.id).order_by(Organization.id)))
            queued: list[tuple[UUID, UUID]] = []
            for organization_id in organization_ids:
                set_tenant_context(db, organization_id)
                queued.extend(
                    (organization_id, run_id)
                    for run_id in load_pending_run_ids(db, limit=max(0, 50 - len(queued)))
                )
                if len(queued) >= 50:
                    break
        for organization_id, run_id in queued:
            detect_incident_run.delay(str(organization_id), str(run_id))
        return len(queued)
    finally:
        database.dispose()


@celery_app.task(
    name="supportflow.schedule_incident_radar",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def schedule_incident_radar() -> int:
    from app.incidents.service import schedule_org_runs

    database = Database(Settings())
    try:
        with database.session() as db:
            run_orgs = schedule_org_runs(db, Settings(), limit=25)
        for organization_id, run_id in run_orgs:
            detect_incident_run.delay(str(organization_id), str(run_id))
        return len(run_orgs)
    finally:
        database.dispose()


@celery_app.task(
    name="supportflow.backfill_semantic_index",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def backfill_semantic_index() -> int:
    from app.semantic.service import schedule_missing

    database = Database(Settings())
    try:
        with database.session() as db:
            return schedule_missing(db, Settings(), limit=50)
    finally:
        database.dispose()


@celery_app.task(
    name="supportflow.cleanup_transient_data",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def cleanup_transient_data() -> dict[str, int]:
    from app.retention import cleanup_transient_data as cleanup

    database = Database(Settings())
    try:
        with database.session() as db:
            counts = cleanup(db, Settings())
        logger.info("retention_cleanup_completed counts=%s", counts)
        return counts
    finally:
        database.dispose()
