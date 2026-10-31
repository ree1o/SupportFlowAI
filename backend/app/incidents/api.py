import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.identity.api import DB, Auth, WriteAuth
from app.incidents import service
from app.incidents.schemas import (
    IncidentCreate,
    IncidentDetail,
    IncidentLink,
    IncidentNote,
    IncidentPage,
    IncidentUpdate,
    ProposalConfirm,
    ProposalPage,
    ProposalReject,
    ProposalReviewResult,
    ProposalStatus,
    ProposalView,
    RadarRunPage,
    RadarRunView,
)
from app.rate_limit import limited

logger = logging.getLogger("supportflow.incidents")
router = APIRouter(prefix="/api/organizations/{organization_id}", tags=["incidents and radar"])
Page = Annotated[int, Query(ge=1, le=10000)]
Size = Annotated[int, Query(ge=1, le=100)]


@router.post(
    "/radar/runs",
    response_model=RadarRunView,
    status_code=202,
    dependencies=[Depends(limited("radar-trigger", 5, 900))],
)
def trigger_radar(organization_id: UUID, auth: WriteAuth, db: DB) -> RadarRunView:
    run = service.queue_run(db, auth, organization_id)
    _dispatch_run(organization_id, run.id)
    return run


def _dispatch_run(organization_id: UUID, run_id: UUID) -> None:
    try:
        from app.jobs import detect_incident_run

        detect_incident_run.delay(str(organization_id), str(run_id))
    except Exception as exc:
        # The database run remains pending; Beat recovery dispatches it after broker recovery.
        logger.warning(
            "radar_dispatch_deferred run_id=%s error_type=%s", str(run_id), type(exc).__name__
        )


@router.get("/radar/runs", response_model=RadarRunPage)
def radar_runs(
    organization_id: UUID, auth: Auth, db: DB, page: Page = 1, page_size: Size = 20
) -> RadarRunPage:
    return service.list_runs(db, auth, organization_id, page, page_size)


@router.post("/radar/runs/{run_id}/retry", response_model=RadarRunView, status_code=202)
def retry_radar_run(organization_id: UUID, run_id: UUID, auth: WriteAuth, db: DB) -> RadarRunView:
    result = service.retry_run(db, auth, organization_id, run_id)
    _dispatch_run(organization_id, run_id)
    return result


@router.get("/radar/proposals", response_model=ProposalPage)
def proposals(
    organization_id: UUID,
    auth: Auth,
    db: DB,
    status: ProposalStatus | None = None,
    page: Page = 1,
    page_size: Size = 20,
) -> ProposalPage:
    return service.list_proposals(db, auth, organization_id, status, page, page_size)


@router.get("/radar/proposals/{proposal_id}", response_model=ProposalView)
def proposal(organization_id: UUID, proposal_id: UUID, auth: Auth, db: DB) -> ProposalView:
    return service.get_proposal(db, auth, organization_id, proposal_id)


@router.post(
    "/radar/proposals/{proposal_id}/confirm",
    response_model=ProposalReviewResult,
)
def confirm_proposal(
    organization_id: UUID,
    proposal_id: UUID,
    data: ProposalConfirm,
    auth: WriteAuth,
    db: DB,
) -> ProposalReviewResult:
    return service.confirm_proposal(db, auth, organization_id, proposal_id, data)


@router.post(
    "/radar/proposals/{proposal_id}/reject",
    response_model=ProposalReviewResult,
)
def reject_proposal(
    organization_id: UUID,
    proposal_id: UUID,
    data: ProposalReject,
    auth: WriteAuth,
    db: DB,
) -> ProposalReviewResult:
    return service.reject_proposal(db, auth, organization_id, proposal_id, data)


@router.post("/incidents", response_model=IncidentDetail, status_code=201)
def create_incident(
    organization_id: UUID, data: IncidentCreate, auth: WriteAuth, db: DB
) -> IncidentDetail:
    return service.create_incident(db, auth, organization_id, data)


@router.get("/incidents", response_model=IncidentPage)
def incidents(
    organization_id: UUID,
    auth: Auth,
    db: DB,
    status: Literal["investigating", "monitoring", "resolved"] | None = None,
    page: Page = 1,
    page_size: Size = 20,
) -> IncidentPage:
    return service.list_incidents(db, auth, organization_id, status, page, page_size)


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def incident(organization_id: UUID, incident_id: UUID, auth: Auth, db: DB) -> IncidentDetail:
    return service.get_incident(db, auth, organization_id, incident_id)


@router.patch("/incidents/{incident_id}", response_model=IncidentDetail)
def update_incident(
    organization_id: UUID,
    incident_id: UUID,
    data: IncidentUpdate,
    auth: WriteAuth,
    db: DB,
) -> IncidentDetail:
    return service.update_incident(db, auth, organization_id, incident_id, data)


@router.post("/incidents/{incident_id}/notes", response_model=IncidentDetail)
def add_note(
    organization_id: UUID,
    incident_id: UUID,
    data: IncidentNote,
    auth: WriteAuth,
    db: DB,
) -> IncidentDetail:
    return service.add_incident_note(db, auth, organization_id, incident_id, data)


@router.post("/incidents/{incident_id}/tickets", response_model=IncidentDetail)
def link_ticket(
    organization_id: UUID,
    incident_id: UUID,
    data: IncidentLink,
    auth: WriteAuth,
    db: DB,
) -> IncidentDetail:
    return service.link_ticket(
        db, auth, organization_id, incident_id, data.ticket_id, data.expected_version
    )


@router.delete("/incidents/{incident_id}/tickets/{ticket_id}", response_model=IncidentDetail)
def unlink_ticket(
    organization_id: UUID,
    incident_id: UUID,
    ticket_id: UUID,
    expected_version: Annotated[int, Query(ge=1)],
    auth: WriteAuth,
    db: DB,
) -> IncidentDetail:
    return service.unlink_ticket(
        db, auth, organization_id, incident_id, ticket_id, expected_version
    )
