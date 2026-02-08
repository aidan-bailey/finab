import os
from datetime import date
from typing import Any, Optional

import ynab_api
from dotenv import load_dotenv


class YNABClient:
    """Wrapper around ynab_api client."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the YNAB client.

        Args:
            api_key: YNAB API access token. If not provided, it will be read
                     from the YNAB_ACCESS_TOKEN environment variable.
        """
        # Load environment variables if not already loaded
        load_dotenv()

        self.api_key = api_key or os.getenv("YNAB_ACCESS_TOKEN")
        if not self.api_key:
            raise ValueError(
                "YNAB_ACCESS_TOKEN environment variable is not set and no api_key provided."
            )

        self.configuration = ynab_api.Configuration()
        self.configuration.api_key["Authorization"] = self.api_key
        self.configuration.api_key_prefix["Authorization"] = "Bearer"
        # Create an API client with the configuration
        self.api_client = ynab_api.ApiClient(self.configuration)

    def get_budgets(self) -> Any:
        """
        Fetches all budgets from YNAB.

        Returns:
            List of budget summary objects from ynab_api.
        """
        budgets_api = ynab_api.BudgetsApi(self.api_client)
        response = budgets_api.get_budgets()
        return response.data.budgets

    def get_transactions(
        self,
        budget_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Any:
        """
        Fetches transactions from a specific budget.

        Args:
            budget_id: The ID of the budget to fetch transactions from.
            start_date: Optional start date to filter transactions (inclusive).
            end_date: Optional end date to filter transactions (inclusive).

        Returns:
            List of transaction objects from ynab_api.
        """
        transactions_api = ynab_api.TransactionsApi(self.api_client)

        # ynab_api accepts 'since_date' for start_date
        since_date = start_date if start_date else None

        response = transactions_api.get_transactions(budget_id, since_date=since_date)

        transactions = response.data.transactions

        # Filter by end_date client-side as the API only supports 'since_date'
        if end_date:
            transactions = [t for t in transactions if t.date <= end_date]

        return transactions


if __name__ == "__main__":
    try:
        client = YNABClient()
        print("YNABClient initialized successfully.")
    except ValueError as e:
        print(f"Error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
