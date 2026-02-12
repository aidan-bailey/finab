from pydantic import BaseModel, Field
from typing import Optional, List, Any
from decimal import Decimal
from datetime import datetime, date
from dataclasses import dataclass


@dataclass
class YNABTransaction:
    account_id: str
    date: date
    amount: int  # amount in milliunits
    payee_id: Optional[str] = None
    payee_name: Optional[str] = None
    category_id: Optional[str] = None
    memo: Optional[str] = None
    cleared: Optional[str] = None  # 'cleared', 'uncleared', 'reconciled'
    approved: Optional[bool] = None
    flag_color: Optional[str] = None
    import_id: Optional[str] = None
    subtransactions: Optional[List[dict]] = None


class Amount(BaseModel):
    model_config = {"populate_by_name": True}
    amount: Decimal
    currency_code: str = Field(alias="currencyCode")


class FinWiseTransaction(BaseModel):
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
    merchant_name: Optional[str] = Field(None, alias="merchantName")
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


class FinWiseAccount(BaseModel):
    id: str
    name: str
    type: str
    sub_type: Optional[str] = None
    current_balance: Optional[Amount] = None

    # We can add more fields if needed, but these are the strict minimum for sync


class Account(BaseModel):
    name: str
    type: str
    balance: int  # milliunits
    currency_code: str
    finwise_id: Optional[str] = None
    ynab_id: Optional[str] = None

    @classmethod
    def from_finwise(cls, account: FinWiseAccount) -> "Account":
        # Map FinWise types to YNAB types
        # FinWise types: https://finwiseapp.io/docs (or inferred from data)
        # YNAB types: checking, savings, creditCard, cash, lineOfCredit, otherAsset, otherLiability

        ynab_type = "otherAsset"  # Default

        fw_type = account.type.lower()
        fw_sub_type = account.sub_type.lower() if account.sub_type else ""

        if fw_type == "depository":
            if "checking" in fw_sub_type:
                ynab_type = "checking"
            elif "savings" in fw_sub_type:
                ynab_type = "savings"
            else:
                ynab_type = "checking"  # Default for depository
        elif fw_type == "credit" or fw_type == "credit_card":
            ynab_type = "creditCard"
        elif fw_type == "loan":
            ynab_type = "otherLiability"
        elif fw_type == "investment":
            ynab_type = "otherAsset"

        # Balance conversion: FinWise is decimal, YNAB is milliunits (int)
        # However, FinWise balance might be positive for assets and negative for liabilities?
        # Usually APIs return absolute values or consistent signed.
        # Let's assume standard behavior: assets +, liabilities - (or + if it's "balance").
        # Detailed verification needed, but for now strict conversion.

        balance_amount = (
            account.current_balance.amount if account.current_balance else 0
        )
        currency = (
            account.current_balance.currency_code if account.current_balance else "ZAR"
        )

        return cls(
            name=account.name,
            type=ynab_type,
            balance=int(balance_amount * 1000),
            currency_code=currency,
            finwise_id=account.id,
        )


class Transaction(BaseModel):
    account_id: str
    date: date
    amount: int  # milliunits
    payee_name: Optional[str] = None
    category_id: Optional[str] = None
    memo: Optional[str] = None
    merchant_id: Optional[str] = None
    merchant_name: Optional[str] = None
    import_id: Optional[str] = None
    cleared: str = "uncleared"
    approved: bool = False
    flag_color: Optional[str] = None
    subtransactions: List[dict] = Field(default_factory=list)

    @classmethod
    def from_finwise(cls, txn: FinWiseTransaction) -> "Transaction":
        return cls(
            account_id=txn.account_id,
            date=txn.date.date(),
            amount=int(txn.amount.amount * 1000),
            payee_name=txn.description,
            memo=txn.description,
            merchant_id=txn.merchant_id,
            merchant_name=txn.merchant_name,
            import_id=txn.id,
            cleared="cleared",
            approved=False,
        )

    def to_ynab(self) -> YNABTransaction:
        return YNABTransaction(
            account_id=self.account_id,
            date=self.date,
            amount=self.amount,
            payee_name=self.payee_name,
            category_id=self.category_id,
            memo=self.memo,
            import_id=self.import_id,
            cleared=self.cleared,
            approved=self.approved,
            flag_color=self.flag_color,
            subtransactions=self.subtransactions,
        )
