"""``/api/v1/audit-logs`` - read-only, paginated audit trail (newest first).

Concepts: query parameter validation, ``Literal`` types, generic ``Page[T]``.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from spendwise.api.deps import UowDep
from spendwise.api.schemas import AuditLogRead, Page

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("")
def list_audit_logs(
    uow: UowDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    entity_type: Literal["category", "expense", "import"] | None = None,
    entity_id: Annotated[int | None, Query(gt=0)] = None,
) -> Page[AuditLogRead]:
    with uow:
        entries, total = uow.audit_logs.list(
            page=page, page_size=page_size, entity_type=entity_type, entity_id=entity_id
        )
    return Page[AuditLogRead](
        items=[AuditLogRead.model_validate(e) for e in entries],
        total=total,
        page=page,
        page_size=page_size,
    )
