from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.identity.api import DB, Auth, WriteAuth
from app.tickets import service
from app.tickets.models import Priority, Status, Visibility
from app.tickets.schemas import (
    ActivityPage,
    CategoryCreate,
    CategoryUpdate,
    CategoryView,
    ContactCreate,
    ContactPage,
    ContactUpdate,
    ContactView,
    MessageCreate,
    MessagePage,
    PublicConversation,
    TicketCreate,
    TicketPage,
    TicketSort,
    TicketUpdate,
    TicketView,
)

router = APIRouter(prefix="/api/organizations/{organization_id}", tags=["helpdesk"])
Search = Annotated[str, Query(max_length=200)]
Page = Annotated[int, Query(ge=1, le=10000)]
Size = Annotated[int, Query(ge=1, le=100)]
After = Annotated[int, Query(ge=0)]


@router.post("/contacts", response_model=ContactView, status_code=201)
def create_contact(
    organization_id: UUID, data: ContactCreate, auth: WriteAuth, db: DB
) -> ContactView:
    return service.create_contact(db, auth, organization_id, data)


@router.get("/contacts", response_model=ContactPage)
def contacts(
    organization_id: UUID,
    auth: Auth,
    db: DB,
    q: Search = "",
    include_inactive: bool = False,
    page: Page = 1,
    page_size: Size = 20,
) -> ContactPage:
    return service.contacts(db, auth, organization_id, q, include_inactive, page, page_size)


@router.get("/contacts/{contact_id}", response_model=ContactView)
def contact(organization_id: UUID, contact_id: UUID, auth: Auth, db: DB) -> ContactView:
    return service.get_contact(db, auth, organization_id, contact_id)


@router.patch("/contacts/{contact_id}", response_model=ContactView)
def update_contact(
    organization_id: UUID, contact_id: UUID, data: ContactUpdate, auth: WriteAuth, db: DB
) -> ContactView:
    return service.update_contact(db, auth, organization_id, contact_id, data)


@router.delete("/contacts/{contact_id}", status_code=204)
def archive_contact(organization_id: UUID, contact_id: UUID, auth: WriteAuth, db: DB) -> None:
    service.update_contact(db, auth, organization_id, contact_id, ContactUpdate(is_active=False))


@router.get("/categories", response_model=list[CategoryView])
def categories(
    organization_id: UUID, auth: Auth, db: DB, include_inactive: bool = False
) -> list[CategoryView]:
    return service.categories(db, auth, organization_id, include_inactive)


@router.post("/categories", response_model=CategoryView, status_code=201)
def create_category(
    organization_id: UUID, data: CategoryCreate, auth: WriteAuth, db: DB
) -> CategoryView:
    return service.create_category(db, auth, organization_id, data)


@router.patch("/categories/{category_id}", response_model=CategoryView)
def update_category(
    organization_id: UUID, category_id: UUID, data: CategoryUpdate, auth: WriteAuth, db: DB
) -> CategoryView:
    return service.update_category(db, auth, organization_id, category_id, data)


@router.post("/tickets", response_model=TicketView, status_code=201)
def create_ticket(organization_id: UUID, data: TicketCreate, auth: WriteAuth, db: DB) -> TicketView:
    return service.create_ticket(db, auth, organization_id, data)


@router.get("/tickets", response_model=TicketPage)
def tickets(
    organization_id: UUID,
    auth: Auth,
    db: DB,
    q: Search = "",
    status: Status | None = None,
    priority: Priority | None = None,
    category_id: UUID | None = None,
    assigned_to: UUID | None = None,
    unassigned: bool = False,
    requester_id: UUID | None = None,
    sort: TicketSort = "created_desc",
    page: Page = 1,
    page_size: Size = 20,
    sla_status: Literal["overdue", "approaching", "none"] | None = None,
) -> TicketPage:
    return service.tickets(
        db,
        auth,
        organization_id,
        q,
        status,
        priority,
        category_id,
        assigned_to,
        unassigned,
        requester_id,
        sort,
        page,
        page_size,
        sla_status,
    )


@router.get("/tickets/{ticket_id}", response_model=TicketView)
def ticket(organization_id: UUID, ticket_id: UUID, auth: Auth, db: DB) -> TicketView:
    return service.get_ticket(db, auth, organization_id, ticket_id)


@router.patch("/tickets/{ticket_id}", response_model=TicketView)
def update_ticket(
    organization_id: UUID, ticket_id: UUID, data: TicketUpdate, auth: WriteAuth, db: DB
) -> TicketView:
    return service.update_ticket(db, auth, organization_id, ticket_id, data)


@router.post("/tickets/{ticket_id}/replies", response_model=TicketView, status_code=201)
def reply(
    organization_id: UUID,
    ticket_id: UUID,
    data: MessageCreate,
    auth: WriteAuth,
    db: DB,
    request: Request,
) -> TicketView:
    return service.post_message(
        db, auth, organization_id, ticket_id, data, Visibility.PUBLIC, request.app.state.settings
    )


@router.post("/tickets/{ticket_id}/notes", response_model=TicketView, status_code=201)
def note(
    organization_id: UUID,
    ticket_id: UUID,
    data: MessageCreate,
    auth: WriteAuth,
    db: DB,
    request: Request,
) -> TicketView:
    return service.post_message(
        db, auth, organization_id, ticket_id, data, Visibility.INTERNAL, request.app.state.settings
    )


@router.get("/tickets/{ticket_id}/messages", response_model=MessagePage)
def messages(
    organization_id: UUID,
    ticket_id: UUID,
    auth: Auth,
    db: DB,
    after_version: After = 0,
    limit: Size = 50,
) -> MessagePage:
    return cast(
        MessagePage,
        service.conversation(db, auth, organization_id, ticket_id, after_version, limit),
    )


@router.get("/tickets/{ticket_id}/public-conversation", response_model=PublicConversation)
def public_conversation(
    organization_id: UUID,
    ticket_id: UUID,
    auth: Auth,
    db: DB,
    after_version: After = 0,
    limit: Size = 50,
) -> PublicConversation:
    # Staff authorization remains required. This is a safe projection, not a customer portal.
    return cast(
        PublicConversation,
        service.conversation(
            db, auth, organization_id, ticket_id, after_version, limit, public=True
        ),
    )


@router.get("/tickets/{ticket_id}/activity", response_model=ActivityPage)
def activity(
    organization_id: UUID,
    ticket_id: UUID,
    auth: Auth,
    db: DB,
    after_version: After = 0,
    limit: Size = 20,
) -> ActivityPage:
    return service.activity(db, auth, organization_id, ticket_id, after_version, limit)
