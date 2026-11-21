from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app import queries
from app.db import get_conn
from app.routers._responses import AUTH_ERRORS, DB_ERRORS
from app.schemas import (
    DeliveryParams,
    DeliveryPerformance,
    RevenueParams,
    RevenuePoint,
    RevenueResponse,
)

router = APIRouter(prefix="/metrics", tags=["metrics"], responses={**AUTH_ERRORS, **DB_ERRORS})


@router.get(
    "/revenue",
    response_model=RevenueResponse,
    summary="Revenue by period",
    description=(
        "Revenue = sum of `price` in `core.fact_order_items` for orders that are **not** "
        "`canceled` or `unavailable` (`is_revenue = true`, the definition in dbt `_core.yml`). "
        "Freight is not included. Filtering and grouping use the order's purchase date; "
        "EUR uses the BRL→EUR rate of the purchase day. Example: "
        "`/v1/metrics/revenue?from=2017-01-01&to=2017-12-31&granularity=month&currency=EUR`."
    ),
)
def revenue(
    params: Annotated[RevenueParams, Query()],
    conn=Depends(get_conn),
) -> RevenueResponse:
    rows = queries.revenue(conn, params.date_from, params.date_to, params.granularity, params.currency)
    total = next((r for r in rows if r["period"] is None), None)
    return RevenueResponse(
        currency=params.currency,
        granularity=params.granularity,
        date_from=params.date_from,
        date_to=params.date_to,
        total_revenue=total["revenue"] if total and total["revenue"] is not None else Decimal("0.00"),
        orders=total["orders"] if total else 0,
        items=total["items"] if total else 0,
        series=[RevenuePoint(**r) for r in rows if r["period"] is not None],
    )


@router.get(
    "/delivery-performance",
    response_model=DeliveryPerformance,
    summary="Delivery performance",
    description=(
        "For `delivered` orders with a delivery date: number of orders, average days from "
        "purchase to delivery, and the percentage delivered after the estimated date. `delivered` "
        "orders without a delivery date are excluded and counted separately. `state` filters by the "
        "customer's state (address from the most recent order, from `dim_customers`). Example: "
        "`/v1/metrics/delivery-performance?state=SP`."
    ),
)
def delivery_performance(
    params: Annotated[DeliveryParams, Query()],
    conn=Depends(get_conn),
) -> DeliveryPerformance:
    state = params.state.value if params.state else None
    row = queries.delivery_performance(conn, state)
    return DeliveryPerformance(state=params.state, **row)
