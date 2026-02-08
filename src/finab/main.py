from datetime import date
from dotenv import load_dotenv
from finab.client import FinWiseClient
from finab.ynab_client import YNABClient


def sync_accounts(finwise_client: FinWiseClient, ynab_client: YNABClient, budget_id: str):
    print("\n--- Account Sync ---")
    print("Fetching accounts...")
    
    try:
        fw_accounts = finwise_client.get_accounts()
        print(f"FinWise Accounts: {len(fw_accounts)}")
    except Exception as e:
        print(f"Failed to fetch FinWise accounts: {e}")
        return

    try:
        ynab_accounts = ynab_client.get_accounts(budget_id)
        print(f"YNAB Accounts: {len(ynab_accounts)}")
    except Exception as e:
        print(f"Failed to fetch YNAB accounts: {e}")
        return
    
    ynab_account_names = {acc.name for acc in ynab_accounts}
    
    for fw_acc in fw_accounts:
        if fw_acc.name not in ynab_account_names:
            print(f"Creating account '{fw_acc.name}' in YNAB...")
            try:
                ynab_client.create_account(budget_id, fw_acc)
                print(f"Account '{fw_acc.name}' created successfully.")
            except Exception as e:
                print(f"Failed to create account '{fw_acc.name}': {e}")
        else:
            print(f"Account '{fw_acc.name}' already exists in YNAB.")


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

        if not budgets:
            print("No budgets found in YNAB.")
            return

        budget_id = None
        if len(budgets) == 1:
            budget_id = budgets[0].id
            print(f"Using budget: {budgets[0].name}")
        else:
            print("\nMultiple budgets found. Please select one:")
            for i, budget in enumerate(budgets):
                print(f"{i + 1}. {budget.name} (ID: {budget.id})")
            
            while True:
                try:
                    selection = input("\nEnter the number of the budget to use: ")
                    index = int(selection) - 1
                    if 0 <= index < len(budgets):
                        budget_id = budgets[index].id
                        print(f"Selected budget: {budgets[index].name}")
                        break
                    else:
                        print("Invalid selection. Please try again.")
                except ValueError:
                    print("Invalid input. Please enter a number.")
            
        if budget_id:
            # Sync Accounts
            sync_accounts(client, ynab, budget_id)

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
