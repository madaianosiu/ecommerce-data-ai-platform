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
    summary="Top produse",
    description=(
        "Cele mai vândute produse după venit (`metric=revenue`, sum `price_brl`) sau cantitate "
        "(`metric=quantity`, număr de linii de comandă), doar din comenzile care intră în venit. "
        "La egalitate, ordinea e după `product_id`. `category` e categoria din `dim_products` "
        "(tradusă; `unknown` pentru produsele fără categorie). Exemplu: "
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
