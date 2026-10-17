"""Provider-neutral invoice contract; unknown values remain null, never guessed."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

INVOICE_SCHEMA_VERSION: Final = "1.0.0"
Money = Annotated[Decimal, Field(allow_inf_nan=False, max_digits=20, decimal_places=6)]
Text = Annotated[str, Field(max_length=2000)]


class InvoiceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def blank_is_unknown(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class InvoiceParty(InvoiceModel):
    name: Text | None = None
    address: Text | None = None
    tax_id: Text | None = None
    registration_number: Text | None = None
    email: Text | None = None
    phone: Text | None = None


class InvoiceLineItem(InvoiceModel):
    line_number: int | None = Field(default=None, ge=1, strict=True)
    description: Text | None = None
    product_code: Text | None = None
    quantity: Money | None = None
    unit: Text | None = None
    unit_price: Money | None = None
    discount: Money | None = None
    tax_rate: Money | None = None
    tax_amount: Money | None = None
    amount: Money | None = None


class InvoiceTotals(InvoiceModel):
    subtotal: Money | None = None
    discount_total: Money | None = None
    tax_total: Money | None = None
    shipping_total: Money | None = None
    grand_total: Money | None = None
    amount_paid: Money | None = None
    amount_due: Money | None = None


class InvoiceDocument(InvoiceModel):
    document_type: Literal["invoice"] = "invoice"
    invoice_number: Text | None = None
    purchase_order_number: Text | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    supplier: InvoiceParty = Field(default_factory=InvoiceParty)
    customer: InvoiceParty = Field(default_factory=InvoiceParty)
    line_items: list[InvoiceLineItem] = Field(default_factory=list, max_length=500)
    totals: InvoiceTotals = Field(default_factory=InvoiceTotals)
    payment_terms: Text | None = None
    payment_reference: Text | None = None
    bank_details: Text | None = None
    notes: Text | None = None

    @model_validator(mode="after")
    def require_invoice_evidence(self):
        parties = any(
            value and str(value).strip()
            for party in (self.supplier, self.customer)
            for value in party.model_dump().values()
        )
        items = any(
            any(value is not None and str(value).strip() for value in item.model_dump().values())
            for item in self.line_items
        )
        if not (
            self.invoice_number
            and self.invoice_number.strip()
            or self.invoice_date
            or parties
            or items
            or any(value is not None for value in self.totals.model_dump().values())
        ):
            raise ValueError("No usable invoice fields were extracted")
        return self


class InvoiceExtractionOut(BaseModel):
    success: Literal[True] = True
    schema_version: Literal["1.0.0"] = INVOICE_SCHEMA_VERSION
    prompt_version: Literal["1.0.0"] = "1.0.0"
    request_id: str
    provider: str
    model: str
    page_count: int
    images_sent: int
    json_repaired: bool
    repair_attempts: int
    warnings: list[str]
    data: InvoiceDocument


class InvoiceErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False
    violations: list[str] = Field(default_factory=list)


class InvoiceErrorOut(BaseModel):
    success: Literal[False] = False
    schema_version: Literal["1.0.0"] = INVOICE_SCHEMA_VERSION
    request_id: str
    error: InvoiceErrorDetail
