from finwise import FinWise
from typing import List, Optional
from datetime import date
from .models import Transaction, FinWiseTransaction, Account, FinWiseAccount


class FinWiseClient:
    """Wrapper around FinWise client specifically for transactions."""

    def __init__(self):
        self._client = FinWise()

    def get_accounts(self) -> List[Account]:
        """Fetches all accounts from FinWise and converts them to the unified Account model."""
        # Use the SDK's accounts resource
        response = self._client.accounts.list()
        
        accounts = []
        for acc in response:
            # acc is likely a Pydantic model from the SDK
            # Convert to our internal FinWiseAccount model for validation/mapping
            # extraction via model_dump() (if pydantic v2) or dict() (v1)
            # safe approach: getattr or dict access
            
            data = acc.model_dump() if hasattr(acc, "model_dump") else acc.dict()
            fw_acc = FinWiseAccount.model_validate(data)
            accounts.append(Account.from_finwise(fw_acc))
            
        return accounts

    def get_transactions(
        self, start_date: Optional[date] = None, end_date: Optional[date] = None
    ) -> List[Transaction]:
        """
        Fetches ALL transactions from FinWise via pagination and optionally
        filters by date client-side.

        FinWise paginates with a JSON-encoded query param:
            /transactions?pagination={"pageNumber":N,"pageSize":M}
        The transport returns a bare list (headers discarded), so we stop
        when a page comes back shorter than the requested page size.
        """
        import json

        PAGE_SIZE = 500
        finwise_txns = []
        page = 1
        while True:
            batch = self._client._transport.get(
                "/transactions",
                params={"pagination": json.dumps({"pageNumber": page, "pageSize": PAGE_SIZE})},
            )
            if not isinstance(batch, list):
                raise ValueError(
                    f"Unexpected response format from FinWise API: {type(batch)}"
                )
            finwise_txns.extend(FinWiseTransaction.model_validate(t) for t in batch)
            if len(batch) < PAGE_SIZE:
                break
            page += 1

        if start_date:
            finwise_txns = [t for t in finwise_txns if t.date.date() >= start_date]
        if end_date:
            finwise_txns = [t for t in finwise_txns if t.date.date() <= end_date]

        return [Transaction.from_finwise(t) for t in finwise_txns]
