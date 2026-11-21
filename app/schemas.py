"""Modele Pydantic: parametri de query (validare) și răspunsuri (OpenAPI + serializare)."""
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

# Sumele vin din Postgres ca numeric (Decimal, rotunjite la 2 zecimale în SQL);
# în JSON le trimitem ca numere, nu ca string-uri.
Money = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


# ---------------------------------------------------------------- enum-uri
class Granularity(str, Enum):
    day = "day"
    month = "month"
    year = "year"


class Currency(str, Enum):
    BRL = "BRL"
    EUR = "EUR"


class TopMetric(str, Enum):
    revenue = "revenue"
    quantity = "quantity"


class OrderStatus(str, Enum):
    delivered = "delivered"
    shipped = "shipped"
    canceled = "canceled"
    unavailable = "unavailable"
    invoiced = "invoiced"
    processing = "processing"
    created = "created"
    approved = "approved"


class BrazilState(str, Enum):
    """Cele 27 de unități federative ale Braziliei (codul din customer_state)."""
    AC = "AC"; AL = "AL"; AP = "AP"; AM = "AM"; BA = "BA"; CE = "CE"; DF = "DF"  # noqa: E702
    ES = "ES"; GO = "GO"; MA = "MA"; MT = "MT"; MS = "MS"; MG = "MG"; PA = "PA"  # noqa: E702
    PB = "PB"; PR = "PR"; PE = "PE"; PI = "PI"; RJ = "RJ"; RN = "RN"; RS = "RS"  # noqa: E702
    RO = "RO"; RR = "RR"; SC = "SC"; SP = "SP"; SE = "SE"; TO = "TO"  # noqa: E702


# ---------------------------------------------------------------- parametri
class _QueryModel(BaseModel):
    # Parametrii necunoscuți (ex. un typo ca "granularty") dau 422 în loc să fie ignorați.
    model_config = ConfigDict(extra="forbid")


class RevenueParams(_QueryModel):
    date_from: date | None = Field(
        None, alias="from",
        description="Prima zi inclusă (data cumpărării comenzii), YYYY-MM-DD. Implicit: fără limită.",
        examples=["2017-01-01"],
    )
    date_to: date | None = Field(
        None, alias="to",
        description="Ultima zi inclusă, YYYY-MM-DD. Implicit: fără limită.",
        examples=["2017-12-31"],
    )
    granularity: Granularity = Field(Granularity.month, description="Mărimea perioadei din serie.")
    currency: Currency = Field(Currency.BRL, description="Moneda sumelor (EUR la cursul din ziua cumpărării).")

    @model_validator(mode="after")
    def _check_range(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("'from' must be on or before 'to'")
        return self


class DeliveryParams(_QueryModel):
    state: BrazilState | None = Field(
        None,
        description="Statul clientului (cod UF din 2 litere, ex. SP). Implicit: toată Brazilia.",
        examples=["SP"],
    )

    @field_validator("state", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.strip().upper() if isinstance(v, str) else v


class TopProductsParams(_QueryModel):
    category: str | None = Field(
        None, min_length=1, max_length=100, pattern=r"^[a-z0-9_]+$",
        description="Categoria produsului (dim_products.product_category), ex. health_beauty; "
                    "'unknown' pentru produsele fără categorie.",
        examples=["health_beauty"],
    )
    limit: int = Field(10, ge=1, le=100, description="Câte produse (1-100).")
    metric: TopMetric = Field(TopMetric.revenue, description="Criteriul de ordonare.")


class OrdersParams(_QueryModel):
    status: OrderStatus | None = Field(None, description="Filtru pe order_status.", examples=["delivered"])
    limit: int = Field(50, ge=1, le=200, description="Mărimea paginii (1-200).")
    cursor: str | None = Field(
        None, max_length=500,
        description="Valoarea next_cursor din răspunsul anterior. Lipsește pentru prima pagină.",
    )


# ---------------------------------------------------------------- răspunsuri
class ErrorResponse(BaseModel):
    detail: str = Field(examples=["Database unavailable"])


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    database: str = Field(examples=["ok"])


class RevenuePoint(BaseModel):
    period: date = Field(description="Începutul perioadei (zi / prima zi din lună / an).")
    revenue: Money
    orders: int
    items: int


class RevenueResponse(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={"examples": [{
            "currency": "BRL", "granularity": "month", "from": "2017-01-01", "to": "2017-02-28",
            "total_revenue": 238000.50, "orders": 2400, "items": 2750,
            "series": [{"period": "2017-01-01", "revenue": 120000.25, "orders": 800, "items": 950}],
        }]},
    )
    currency: Currency
    granularity: Granularity
    date_from: date | None = Field(serialization_alias="from")
    date_to: date | None = Field(serialization_alias="to")
    total_revenue: Money = Field(description="Venit = sum(price) pe comenzile care nu sunt canceled/unavailable.")
    orders: int
    items: int
    series: list[RevenuePoint]


class DeliveryPerformance(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "state": "SP", "orders": 40000, "avg_delivery_days": 8.7, "late_orders": 2300,
        "late_pct": 5.9, "excluded_missing_delivery_date": 2,
    }]})
    state: BrazilState | None
    orders: int = Field(description="Comenzi livrate (status delivered) cu dată de livrare.")
    avg_delivery_days: float | None = Field(description="Media zilelor de la cumpărare la livrare.")
    late_orders: int
    late_pct: float | None = Field(description="Procentul de comenzi livrate după data estimată.")
    excluded_missing_delivery_date: int = Field(
        description="Comenzi 'delivered' fără dată de livrare, excluse din calcul.")


class TopProduct(BaseModel):
    product_id: str
    product_category: str
    quantity: int = Field(description="Număr de unități vândute (linii de comandă).")
    revenue_brl: Money
    revenue_eur: Money


class TopProductsResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "metric": "revenue", "category": None, "limit": 1,
        "items": [{"product_id": "bb50f2e236e5eea0100680137654686c", "product_category": "health_beauty",
                   "quantity": 195, "revenue_brl": 63885.00, "revenue_eur": 15531.33}],
    }]})
    metric: TopMetric
    category: str | None
    limit: int
    items: list[TopProduct]


class Order(BaseModel):
    order_id: str
    customer_unique_id: str
    order_status: OrderStatus
    order_purchase_at: datetime
    order_approved_at: datetime | None
    order_delivered_customer_at: datetime | None
    order_estimated_delivery_at: datetime | None
    delivery_days: int | None
    is_late: bool | None


class OrdersPage(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "limit": 1, "next_cursor": "eyJ0IjoiMjAxOC0xMC0xN1QxNzozMDoxOCIsImlkIjoiLi4uIn0",
        "items": [{
            "order_id": "10a045cdf6a5650c21e9cfeb60384c16", "customer_unique_id": "...",
            "order_status": "canceled", "order_purchase_at": "2018-10-17T17:30:18",
            "order_approved_at": None, "order_delivered_customer_at": None,
            "order_estimated_delivery_at": "2018-10-30T00:00:00", "delivery_days": None, "is_late": None,
        }],
    }]})
    limit: int
    next_cursor: str | None = Field(description="Cursor pentru pagina următoare; null pe ultima pagină.")
    items: list[Order]


# ---------------------------------------------------------------- /v1/ask (RAG)
class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [
        {"question": "Why are customers unhappy with delivery?", "top_k": 15, "min_score": None, "max_score": 2},
    ]})
    question: str = Field(min_length=3, max_length=500,
                          description="Întrebarea, în orice limbă (răspunsul vine în aceeași limbă).")
    top_k: int = Field(15, ge=1, le=30, description="Câte recenzii-sursă (după deduplicare), 1-30.")
    min_score: int | None = Field(None, ge=1, le=5, description="Scor minim al recenziei (1-5).")
    max_score: int | None = Field(None, ge=1, le=5, description="Scor maxim al recenziei (1-5).")

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("question must contain at least 3 non-space characters")
        return v

    @model_validator(mode="after")
    def _check_scores(self):
        if self.min_score is not None and self.max_score is not None and self.min_score > self.max_score:
            raise ValueError("'min_score' must be less than or equal to 'max_score'")
        return self


class AskSource(BaseModel):
    n: int = Field(ge=1, description="Numărul citării (1, 2, ...), corespunde marcajelor [n] din answer.")
    review_id: str
    review_score: int
    text: str
    similarity: float = Field(description="Similaritate cosinus cu întrebarea (1 = identic).")


class AskResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "route": "rag",
        "answer": "Customers mostly complain about late deliveries [1][2] and orders that never arrived [3]. "
                  "These are written reviews only, which skew toward low scores.",
        "sources": [{"n": 1, "review_id": "a1074ef6...", "review_score": 1, "text": "Atraso na entrega",
                     "similarity": 0.89}],
        "retrieved_count": 60,
        "notes": ["Sources are written reviews only ..."],
    }]})
    route: Literal["rag"] = Field("rag", description="Ruta aleasă de router: 'rag' = răspuns din recenzii.")
    answer: str
    sources: list[AskSource] = Field(description="În ordinea citărilor: [1] = primul element.")
    retrieved_count: int = Field(description="Candidați aduși din baza vectorială, înainte de deduplicare.")
    notes: list[str]


class SqlAskResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "route": "sql",
        "answer": "Total revenue was R$ 13,494,400.74.",
        "sql": "SELECT * FROM (SELECT ROUND(SUM(price_brl), 2) AS revenue_brl FROM core.fact_order_items "
               "WHERE is_revenue) AS capped_result LIMIT 200",
        "rows": [{"revenue_brl": 13494400.74}],
        "row_count": 1,
        "notes": ["Read-only query on schema core as api_reader, 10 s timeout, capped at 200 rows."],
    }]})
    route: Literal["sql"] = "sql"
    answer: str
    sql: str | None = Field(description="Interogarea executată (validată, cu LIMIT 200); null dacă nu s-a putut răspunde.")
    rows: list[dict[str, Any]] = Field(description="Rândurile rezultatului, maxim 200.")
    row_count: int
    notes: list[str]


class UnsupportedAskResponse(BaseModel):
    route: Literal["unsupported"] = "unsupported"
    answer: str
    notes: list[str]


AskResult = Annotated[AskResponse | SqlAskResponse | UnsupportedAskResponse, Field(discriminator="route")]
