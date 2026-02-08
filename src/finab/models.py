from pydantic import BaseModel, Field
from typing import Optional, List, Any
from decimal import Decimal
from datetime import datetime


class Amount(BaseModel):
    amount: Decimal
    currency_code: str = Field(alias="currencyCode")


class Transaction(BaseModel):
    id: str
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    description: str
    original_description: Optional[str] = Field(None, alias="originalDescription")
    account_id: str = Field(alias="accountId")
    amount: Amount
    date: datetime
    transaction_category_id: Optional[str] = Field(None, alias="transactionCategoryId")
    original_transaction_category_id: Optional[str] = Field(
        None, alias="originalTransactionCategoryId"
    )
    merchant_id: Optional[str] = Field(None, alias="merchantId")
    original_merchant_id: Optional[str] = Field(None, alias="originalMerchantId")
    user_id: str = Field(alias="userId")
    needs_review: bool = Field(alias="needsReview")
    transaction_tags: List[Any] = Field(default_factory=list, alias="transactionTags")
    file_records: List[Any] = Field(default_factory=list, alias="fileRecords")

    # Other optional fields
    parent_transaction_id: Optional[str] = Field(None, alias="parentTransactionId")
    splits: Optional[Any] = None
    is_manual: Optional[bool] = Field(None, alias="isManual")
    is_transfer: Optional[bool] = Field(None, alias="isTransfer")
    notes: Optional[str] = None
    archived_at: Optional[datetime] = Field(None, alias="archivedAt")
    effective_date: Optional[datetime] = Field(None, alias="effectiveDate")
    data_import_id: Optional[str] = Field(None, alias="dataImportId")
    is_pending: Optional[bool] = Field(None, alias="isPending")
    pending_transaction_id: Optional[str] = Field(None, alias="pendingTransactionId")
    internal_notes: Optional[str] = Field(None, alias="internalNotes")
    original_account_id: Optional[str] = Field(None, alias="originalAccountId")
