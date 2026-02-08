import os
from datetime import date
from typing import Any, Optional

import json
from types import SimpleNamespace
from typing import List
import ynab_api
import ynab_api.apis
from ynab_api.model.save_transaction import SaveTransaction
from ynab_api.model.save_transactions_wrapper import SaveTransactionsWrapper
from ynab_api.model.save_sub_transaction import SaveSubTransaction
from ynab_api.model.save_account import SaveAccount
from ynab_api.model.save_account_wrapper import SaveAccountWrapper
from dotenv import load_dotenv
import dataclasses
from finab.models import YNABTransaction, Transaction, Account


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

        if self.api_key.lower().startswith("bearer "):
            self.api_key = self.api_key[7:].strip()

        self.configuration = ynab_api.Configuration()
        # Disable client-side validation due to schema mismatch with 'default_budget'
        self.configuration.client_side_validation = False

        # Try to use certifi for SSL certs if available
        try:
            import certifi

            self.configuration.ssl_ca_cert = certifi.where()
        except ImportError:
            pass

        self.configuration.api_key["bearer"] = self.api_key
        self.configuration.api_key_prefix["bearer"] = "Bearer"
        # Create an API client with the configuration
        self.api_client = ynab_api.ApiClient(self.configuration)

    def get_budgets(self) -> Any:
        """
        Fetches all budgets from YNAB.

        Returns:
            List of budget summary objects from ynab_api.
        """
        budgets_api = ynab_api.apis.BudgetsApi(self.api_client)
        # Use _preload_content=False to bypass broken deserialization of default_budget
        response = budgets_api.get_budgets(_preload_content=False)
        data = json.loads(response.data)
        return [SimpleNamespace(**b) for b in data["data"]["budgets"]]

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
        transactions_api = ynab_api.apis.TransactionsApi(self.api_client)

        # ynab_api accepts 'since_date' for start_date
        kwargs = {}
        if start_date:
            kwargs["since_date"] = start_date

        response = transactions_api.get_transactions(budget_id, **kwargs)

        transactions = response.data.transactions

        # Filter by end_date client-side as the API only supports 'since_date'
        if end_date:
            transactions = [t for t in transactions if t.date <= end_date]

        return transactions

        return transactions

    def get_accounts(self, budget_id: str) -> List[Account]:
        """
        Fetches all accounts from a specific budget.

        Args:
            budget_id: The ID of the budget to fetch accounts from.

        Returns:
            List of Account objects.
        """
        accounts_api = ynab_api.apis.AccountsApi(self.api_client)
        response = accounts_api.get_accounts(budget_id)
        
        accounts = []
        for acc in response.data.accounts:
            if acc.closed:
                continue
                
            accounts.append(Account(
                name=acc.name,
                type=acc.type, # Should be compatible with our enum strings
                balance=acc.balance,
                currency_code="", # YNAB doesn't return currency per account
                finwise_id=None
            ))
        return accounts

    def create_account(self, budget_id: str, account: Account) -> Any:
        """
        Creates a new account in a specific budget.

        Args:
            budget_id: The ID of the budget.
            account: The Account object to create.

        Returns:
            The response from the API.
        """
        accounts_api = ynab_api.apis.AccountsApi(self.api_client)
        
        # SaveAccount needs name, type, balance
        save_account = SaveAccount(
            name=account.name,
            type=account.type,
            balance=account.balance
        )
        
        data = SaveAccountWrapper(account=save_account)
        return accounts_api.create_account(budget_id, data)

    def create_transactions(
        self,
        budget_id: str,
        transactions: list[dict | SaveTransaction | YNABTransaction | Transaction],
    ) -> Any:
        """
        Creates one or more transactions in a specific budget.

        Args:
            budget_id: The ID of the budget.
            transactions: A list of dictionaries, YNABTransaction objects, Transaction objects, or SaveTransaction objects.

        Returns:
            The response from the API (SaveTransactionsResponse).
        """
        transactions_api = ynab_api.apis.TransactionsApi(self.api_client)

        save_transactions = []
        for txn in transactions:
            if isinstance(txn, Transaction):
                txn = txn.to_ynab()

            if isinstance(txn, dict):
                save_transactions.append(SaveTransaction(**txn))
            elif isinstance(txn, YNABTransaction):
                # Filter out None values to let SaveTransaction handle defaults
                txn_dict = {
                    k: v for k, v in dataclasses.asdict(txn).items() if v is not None
                }

                # Convert subtransactions dictionaries to SaveSubTransaction objects if present
                if "subtransactions" in txn_dict:
                    txn_dict["subtransactions"] = [
                        SaveSubTransaction(**sub) if isinstance(sub, dict) else sub
                        for sub in txn_dict["subtransactions"]
                    ]

                save_transactions.append(SaveTransaction(**txn_dict))
            else:
                save_transactions.append(txn)

        data = SaveTransactionsWrapper(transactions=save_transactions)

        return transactions_api.create_transaction(budget_id, data)


if __name__ == "__main__":
    try:
        client = YNABClient()
        print("YNABClient initialized successfully.")
    except ValueError as e:
        print(f"Error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
