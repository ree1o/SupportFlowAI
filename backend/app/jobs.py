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
from app.identity.security import utcnow
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
        }
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
        payload = dict(event.payload)

    try:
        if event_key.startswith("email:"):
            raise ValueError("Invalid event key")
        # A completed marker prevents routine task redelivery from sending again.
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
            if current_job.attempts >= 10:
                current_job.status = "failed"
            else:
                current_job.status = "pending"
                current_job.available_at = utcnow() + timedelta(
                    seconds=min(3600, 2**current_job.attempts * 5)
                )
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
            if (
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
