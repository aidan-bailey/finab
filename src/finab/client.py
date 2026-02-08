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
        Fetches all transactions from FinWise and optionally filters by date.
        Uses the internal transport to bypass SDK issues.
        Filtering is done client-side as the API endpoint does not support query parameters.
        """
        # Fetch all transactions (no params)
        response = self._client._transport.get("/transactions")

        if isinstance(response, list):
            # Parse response into FinWiseTransaction objects
            finwise_txns = [FinWiseTransaction.model_validate(txn) for txn in response]

            # Filter by date
            if start_date:
                finwise_txns = [t for t in finwise_txns if t.date.date() >= start_date]

            if end_date:
                finwise_txns = [t for t in finwise_txns if t.date.date() <= end_date]

            # Convert to unified Transaction model
            return [Transaction.from_finwise(t) for t in finwise_txns]

        raise ValueError(
            f"Unexpected response format from FinWise API: {type(response)}"
        )
