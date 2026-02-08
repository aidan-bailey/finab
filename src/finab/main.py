from datetime import date
from dotenv import load_dotenv
from finab.client import FinWiseClient
from finab.ynab_client import YNABClient


def main():
    load_dotenv()

    print("Hello from finab!")

    # Use the wrapper instead of raw FinWise client
    client = FinWiseClient()

    try:
        print("Fetching transactions via FinWiseClient...")
        transactions = client.get_transactions(
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        print(f"Found {len(transactions)} transactions.")

        # Print a few to verify
        if transactions:
            print("First transaction:", transactions[0])

    except Exception as e:
        print(f"Error fetching transactions: {e}")

    try:
        print("\nFetching budgets via YNABClient...")
        ynab = YNABClient()
        budgets = ynab.get_budgets()
        print(f"Found {len(budgets)} budgets:")
        for budget in budgets:
            print(f"- {budget.name} (ID: {budget.id})")

        if budgets:
            budget_id = budgets[0].id
            print(f"\nFetching transactions for budget '{budgets[0].name}'...")
            transactions = ynab.get_transactions(budget_id)
            print(f"Found {len(transactions)} transactions.")
            if transactions:
                # transactions might be raw objects or SimpleNamespace if I apply the same fix,
                # but currently get_transactions uses the standard API call.
                # Let's see what it returns.
                first_txn = transactions[0]
                print(f"First transaction: {first_txn}")
    except Exception as e:
        print(f"Error fetching YNAB data: {e}")


if __name__ == "__main__":
    main()
