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


def sync_transactions(finwise_client: FinWiseClient, ynab_client: YNABClient, budget_id: str):
    print("\n--- Transaction Sync ---")
    
    # 1. Fetch Accounts to map IDs
    print("Mapping accounts...")
    try:
        fw_accounts = finwise_client.get_accounts()
        ynab_accounts = ynab_client.get_accounts(budget_id)
        
        # Map FinWise Account ID -> YNAB Account ID via Name
        # We need a map: {fw_acc.id: ynab_acc.ynab_id}
        # First build name map for YNAB
        ynab_map_by_name = {acc.name: acc.ynab_id for acc in ynab_accounts if acc.ynab_id}
        
        fw_id_to_ynab_id = {}
        for fw_acc in fw_accounts:
            if fw_acc.finwise_id: # Account model from FinWise has finwise_id populated mapping from its own id
                # FinWiseClient.get_accounts returns Account objects where finwise_id is set to fw_acc.id
                if fw_acc.name in ynab_map_by_name:
                    fw_id_to_ynab_id[fw_acc.finwise_id] = ynab_map_by_name[fw_acc.name]
                else:
                    print(f"Warning: Account '{fw_acc.name}' found in FinWise but not mapped to YNAB. Transactions for this account will be skipped.")
        
        if not fw_id_to_ynab_id:
            print("No accounts mapped. Aborting transaction sync.")
            return

    except Exception as e:
        print(f"Failed to map accounts: {e}")
        return

    # 2. Fetch Transactions
    print("Fetching transactions...")
    try:

        # Fetch last 30 days by default or maybe year to date? User didn't specify, let's do YTD for now based on main() default
        end_date = None #date.today()
        start_date = date.today().replace(day=1)
        
        fw_transactions = finwise_client.get_transactions(start_date=start_date, end_date=end_date)
        ynab_transactions = ynab_client.get_transactions(budget_id, start_date=start_date) # YNAB API handles start_date
        
        print(f"FinWise Transactions: {len(fw_transactions)}")
        print(f"YNAB Transactions: {len(ynab_transactions)}")
        
    except Exception as e:
        print(f"Failed to fetch transactions: {e}")
        return

    # 3. Duplicate Detection & Filtering
    print("Checking for missing transactions...")
    
    # Build a set of existing YNAB transactions for fast lookup
    # Key: (date, amount_milliunits, payee_name_lowercase)
    # Note: Payee name might be None in YNAB or FinWise, handle gracefully
    
    existing_txns = set()
    for txn in ynab_transactions:
        # ynab_api transaction object
        t_date = txn.date
        t_amount = txn.amount
        t_payee = txn.payee_name.lower() if txn.payee_name else ""
        existing_txns.add((t_date, t_amount, t_payee))
        
    transactions_to_create = []
    
    for fw_txn in fw_transactions:
        # fw_txn is Transaction model
        
        # Check if account is mapped
        if fw_txn.account_id not in fw_id_to_ynab_id:
            continue
            
        ynab_account_id = fw_id_to_ynab_id[fw_txn.account_id]
        
        check_date = fw_txn.date
        check_amount = fw_txn.amount # already in milliunits in Transaction model
        
        # Truncate payee_name to 50 chars if necessary (YNAB limit) BEFORE check
        # because YNAB will have the truncated version
        payee_name = fw_txn.payee_name if fw_txn.payee_name else ""
        if len(payee_name) > 50:
            payee_name = payee_name[:50]
        
        check_payee = payee_name.lower()
        
        if (check_date, check_amount, check_payee) not in existing_txns:
            # Prepare for creation
            # We need to update the account_id to the YNAB one
            fw_txn.account_id = ynab_account_id
            
            # Update the object with the truncated name for creation
            fw_txn.payee_name = payee_name
            
            # Force re-import if previously deleted
            if fw_txn.import_id:
                # Truncate to 36 chars max (UUID is 36). 
                # We need 5 chars for _rev1, so take first 31.
                fw_txn.import_id = f"{fw_txn.import_id[:31]}_rev1"
            
            transactions_to_create.append(fw_txn)

    # 4. Create Transactions
    if transactions_to_create:
        print(f"Found {len(transactions_to_create)} missing transactions.")
        print("Creating transactions in YNAB...")
        try:
            ynab_client.create_transactions(budget_id, transactions_to_create)
            print("Successfully created transactions.")
        except Exception as e:
            print(f"Failed to create transactions: {e}")
    else:
        print("No missing transactions found. YNAB is up to date.")


def main():
    load_dotenv()

    print("Hello from finab!")

    # Use the wrapper instead of raw FinWise client
    fw_client = FinWiseClient()
    
    # Initialize YNAB Client
    try:
        ynab_client = YNABClient()
    except Exception as e:
        print(f"Failed to initialize YNAB Client: {e}")
        return

    try:
        print("\nFetching budgets via YNABClient...")
        budgets = ynab_client.get_budgets()
        
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
            sync_accounts(fw_client, ynab_client, budget_id)
            
            # Sync Transactions
            sync_transactions(fw_client, ynab_client, budget_id)

    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
