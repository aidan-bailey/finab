import os
from datetime import date
from typing import Any, List, Optional

import dataclasses
from dotenv import load_dotenv

import ynab
from ynab import (
    AccountsApi,
    CategoriesApi,
    PayeesApi,
    TransactionsApi,
    SaveAccount,
    PostAccountWrapper,
    NewTransaction,
    PostTransactionsWrapper,
    ExistingTransaction,
    PatchTransactionsWrapper,
    SaveSubTransaction,
    PostPayee,
    PostPayeeWrapper,
    NewCategory,
    SaveCategoryGroup,
    PostCategoryWrapper,
    PostCategoryGroupWrapper,
)

from finab.models import YNABTransaction, Transaction, Account


class YNABClient:
    """Wrapper around ynab client."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the YNAB client.

        Args:
            api_key: YNAB API access token. If not provided, it will be read
                     from the YNAB_ACCESS_TOKEN environment variable.
        """
        load_dotenv()

        self.api_key = api_key or os.getenv("YNAB_ACCESS_TOKEN")
        if not self.api_key:
            raise ValueError(
                "YNAB_ACCESS_TOKEN environment variable is not set and no api_key provided."
            )

        if self.api_key.lower().startswith("bearer "):
            self.api_key = self.api_key[7:].strip()

        self.configuration = ynab.Configuration(access_token=self.api_key)
        self.api_client = ynab.ApiClient(self.configuration)

    def get_budgets(self) -> Any:
        """
        Fetches all budgets from YNAB.

        Returns:
            List of budget summary objects from ynab.
        """
        # Note: BudgetsApi was removed in newer ynab SDK versions.
        # Using PlansApi instead (plans = budgets in new terminology).
        from ynab import PlansApi
        plans_api = PlansApi(self.api_client)
        response = plans_api.get_plans()
        return response.data.plans

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
            List of transaction objects from ynab.
        """
        transactions_api = TransactionsApi(self.api_client)

        kwargs = {}
        if start_date:
            kwargs["since_date"] = start_date

        response = transactions_api.get_transactions(budget_id, **kwargs)
        transactions = response.data.transactions

        if end_date:
            transactions = [t for t in transactions if t.var_date <= end_date]

        return transactions

    def get_accounts(self, budget_id: str) -> List[Account]:
        """
        Fetches all accounts from a specific budget.

        Args:
            budget_id: The ID of the budget to fetch accounts from.

        Returns:
            List of Account objects.
        """
        accounts_api = AccountsApi(self.api_client)
        response = accounts_api.get_accounts(budget_id)

        accounts = []
        for acc in response.data.accounts:
            if acc.closed:
                continue
            # New ynab SDK types id/transfer_payee_id as uuid.UUID; the internal
            # Account model expects str. Stringify at the boundary.
            # acc.type is an AccountType enum that inherits from str — pass it
            # directly (str() would return "AccountType.X" instead of the value).
            accounts.append(
                Account(
                    name=acc.name,
                    type=acc.type,
                    balance=acc.balance,
                    currency_code="",
                    finwise_id=None,
                    ynab_id=str(acc.id) if acc.id is not None else None,
                    transfer_payee_id=(
                        str(acc.transfer_payee_id)
                        if acc.transfer_payee_id is not None
                        else None
                    ),
                )
            )
        return accounts

    def get_payees(self, budget_id: str) -> List[Any]:
        """
        Fetches all payees from a specific budget.

        Args:
            budget_id: The ID of the budget.

        Returns:
            List of payee objects.
        """
        payees_api = PayeesApi(self.api_client)
        response = payees_api.get_payees(budget_id)
        return response.data.payees

    def get_categories(self, budget_id: str) -> List[Any]:
        """
        Fetches all categories from a specific budget.

        Args:
            budget_id: The ID of the budget.

        Returns:
            List of category objects.
        """
        categories_api = CategoriesApi(self.api_client)
        response = categories_api.get_categories(budget_id)
        all_categories = []
        for group in response.data.category_groups:
            all_categories.extend(group.categories)
        return all_categories

    def create_account(self, budget_id: str, account: Account) -> Any:
        """
        Creates a new account in a specific budget.

        Args:
            budget_id: The ID of the budget.
            account: The Account object to create.

        Returns:
            The response from the API.
        """
        accounts_api = AccountsApi(self.api_client)
        save_account = SaveAccount(
            name=account.name, type=account.type, balance=account.balance
        )
        data = PostAccountWrapper(account=save_account)
        return accounts_api.create_account(budget_id, data)

    def create_payee(self, budget_id: str, name: str) -> Any:
        """Create a new payee in the budget. Returns the created payee record."""
        payees_api = PayeesApi(self.api_client)
        wrapper = PostPayeeWrapper(payee=PostPayee(name=name))
        response = payees_api.create_payee(budget_id, wrapper)
        return response.data.payee

    def create_category_group(self, budget_id: str, name: str) -> Any:
        """Create a new category group. Returns the created CategoryGroup."""
        categories_api = CategoriesApi(self.api_client)
        wrapper = PostCategoryGroupWrapper(category_group=SaveCategoryGroup(name=name))
        response = categories_api.create_category_group(budget_id, wrapper)
        return response.data.category_group

    def create_category(
        self, budget_id: str, name: str, category_group_id: str
    ) -> Any:
        """Create a new category in the given group. Returns the created Category."""
        categories_api = CategoriesApi(self.api_client)
        wrapper = PostCategoryWrapper(
            category=NewCategory(name=name, category_group_id=category_group_id)
        )
        response = categories_api.create_category(budget_id, wrapper)
        return response.data.category

    def create_transactions(
        self,
        budget_id: str,
        transactions: list[dict | NewTransaction | YNABTransaction | Transaction],
    ) -> Any:
        """
        Creates one or more transactions in a specific budget.

        Args:
            budget_id: The ID of the budget.
            transactions: A list of dictionaries, YNABTransaction objects, Transaction objects, or NewTransaction objects.

        Returns:
            The response from the API (SaveTransactionsResponse).
        """
        transactions_api = TransactionsApi(self.api_client)

        new_transactions = []
        for txn in transactions:
            if isinstance(txn, Transaction):
                txn = txn.to_ynab()

            if isinstance(txn, dict):
                new_transactions.append(NewTransaction(**txn))
            elif isinstance(txn, YNABTransaction):
                txn_dict = {
                    k: v for k, v in dataclasses.asdict(txn).items() if v is not None
                }
                if "subtransactions" in txn_dict:
                    txn_dict["subtransactions"] = [
                        SaveSubTransaction(**sub) if isinstance(sub, dict) else sub
                        for sub in txn_dict["subtransactions"]
                    ]
                new_transactions.append(NewTransaction(**txn_dict))
            else:
                new_transactions.append(txn)

        data = PostTransactionsWrapper(transactions=new_transactions)
        return transactions_api.create_transaction(budget_id, data)

    def update_transactions(
        self,
        budget_id: str,
        transactions: list[dict | YNABTransaction | Transaction],
    ) -> Any:
        """
        Updates one or more transactions in a specific budget.

        Args:
            budget_id: The ID of the budget.
            transactions: A list of dictionaries, YNABTransaction objects, or Transaction objects.

        Returns:
            The response from the API (SaveTransactionsResponse).
        """
        transactions_api = TransactionsApi(self.api_client)

        existing_list = []
        for txn in transactions:
            txn_dict = {}
            if isinstance(txn, Transaction):
                if not txn.ynab_id:
                    continue
                txn_dict = {
                    "id": txn.ynab_id,
                    "account_id": txn.account_id,
                    "date": txn.date,
                    "amount": txn.amount,
                    "payee_id": txn.payee_id,
                    "payee_name": txn.payee_name,
                    "category_id": txn.category_id,
                    "memo": txn.memo,
                    "cleared": txn.cleared,
                    "approved": txn.approved,
                    "flag_color": txn.flag_color,
                    "import_id": txn.import_id,
                }
                if txn.subtransactions:
                    txn_dict["subtransactions"] = [
                        SaveSubTransaction(**sub) if isinstance(sub, dict) else sub
                        for sub in txn.subtransactions
                    ]
            elif isinstance(txn, dict):
                txn_dict = txn

            txn_dict = {k: v for k, v in txn_dict.items() if v is not None}
            if "id" not in txn_dict:
                continue

            existing_list.append(ExistingTransaction(**txn_dict))

        if not existing_list:
            return None

        data = PatchTransactionsWrapper(transactions=existing_list)
        return transactions_api.update_transactions(budget_id, data)

    def delete_transaction(self, budget_id: str, transaction_id: str) -> Any:
        """
        Deletes a transaction.

        Args:
            budget_id: The ID of the budget.
            transaction_id: The ID of the transaction to delete.

        Returns:
            The response from the API.
        """
        transactions_api = TransactionsApi(self.api_client)
        return transactions_api.delete_transaction(budget_id, transaction_id)


if __name__ == "__main__":
    try:
        client = YNABClient()
        print("YNABClient initialized successfully.")
    except ValueError as e:
        print(f"Error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
