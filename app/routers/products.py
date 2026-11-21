from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app import queries
from app.db import get_conn
from app.routers._responses import AUTH_ERRORS, DB_ERRORS
from app.schemas import TopProduct, TopProductsParams, TopProductsResponse

router = APIRouter(prefix="/products", tags=["products"], responses={**AUTH_ERRORS, **DB_ERRORS})


@router.get(
    "/top",
    response_model=TopProductsResponse,
    summary="Top products",
    description=(
        "Best-selling products by revenue (`metric=revenue`, sum of `price_brl`) or quantity "
        "(`metric=quantity`, number of order lines), only from orders that count as revenue. "
        "Ties are ordered by `product_id`. `category` is the category from `dim_products` "
        "(translated; `unknown` for products without a category). Example: "
        "`/v1/products/top?category=health_beauty&limit=5&metric=quantity`."
    ),
)
def top_products(
    params: Annotated[TopProductsParams, Query()],
    conn=Depends(get_conn),
) -> TopProductsResponse:
    rows = queries.top_products(conn, params.category, params.limit, params.metric)
    return TopProductsResponse(
        metric=params.metric,
        category=params.category,
        limit=params.limit,
        items=[TopProduct(**r) for r in rows],
    )
