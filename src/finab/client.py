from finwise import FinWise
from typing import List, Optional
from datetime import date
from .models import Transaction


class FinWiseClient:
    """Wrapper around FinWise client specifically for transactions."""

    def __init__(self):
        self._client = FinWise()

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
            transactions = [Transaction.model_validate(txn) for txn in response]

            if start_date:
                transactions = [t for t in transactions if t.date.date() >= start_date]

            if end_date:
                transactions = [t for t in transactions if t.date.date() <= end_date]

            return transactions

        raise ValueError(
            f"Unexpected response format from FinWise API: {type(response)}"
        )
