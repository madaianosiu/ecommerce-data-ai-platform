from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app import queries
from app.db import get_conn
from app.pagination import InvalidCursor, decode_cursor, encode_cursor
from app.routers._responses import AUTH_ERRORS, BAD_REQUEST, DB_ERRORS
from app.schemas import Order, OrdersPage, OrdersParams

router = APIRouter(prefix="/orders", tags=["orders"],
                   responses={**AUTH_ERRORS, **DB_ERRORS, **BAD_REQUEST})


@router.get(
    "",
    response_model=OrdersPage,
    summary="Order list (cursor pagination)",
    description=(
        "Orders in a stable order: `order_purchase_at` descending, then `order_id`. "
        "Keyset pagination (no OFFSET): for the next page send `cursor=<next_cursor>` "
        "with the same filters; `next_cursor` is `null` on the last page. Example: "
        "`/v1/orders?status=delivered&limit=50`."
    ),
)
def list_orders(
    params: Annotated[OrdersParams, Query()],
    conn=Depends(get_conn),
) -> OrdersPage:
    after = None
    if params.cursor:
        try:
            after = decode_cursor(params.cursor)
        except InvalidCursor as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    status_value = params.status.value if params.status else None
    rows = queries.orders_page(conn, status_value, after, params.limit)
    page, has_more = rows[: params.limit], len(rows) > params.limit
    next_cursor = (
        encode_cursor(page[-1]["order_purchase_at"], page[-1]["order_id"]) if has_more and page else None
    )
    return OrdersPage(limit=params.limit, next_cursor=next_cursor, items=[Order(**r) for r in page])
