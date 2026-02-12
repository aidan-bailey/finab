from datetime import date
import re
from dotenv import load_dotenv
from finab.client import FinWiseClient
from finab.ynab_client import YNABClient
from finab.models import YNABTransaction  # Import needed for Starting Balance txn
from finab.config import (
    load_aliases,
    save_aliases,
    load_payee_rules,
    save_payee_rules,
    load_merchant_aliases,
    save_merchant_aliases,
    load_budget_id,
    save_budget_id,
    load_salt,
    save_salt,
)
import random
import string
import hashlib


def sync_accounts(
    finwise_client: FinWiseClient,
    ynab_client: YNABClient,
    budget_id: str,
):
    print("\n--- Account Sync ---")

    # Pre-calculate start_date for transaction fetching (used for balance adjustment)
    # Default to 1st of current month
    start_date = date.today().replace(day=1)

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

    # Fetch FinWise transactions for the period to adjust starting balances for new accounts
    print("Fetching transactions for balance adjustment...")
    fw_transactions_for_adj = []
    try:
        fw_transactions_for_adj = finwise_client.get_transactions(start_date=start_date)
    except Exception as e:
        print(f"Failed to fetch transactions for adjustment: {e}")
        return

    ynab_account_names = {acc.name for acc in ynab_accounts}
    # Create a map for ID lookup for starting balance creation
    ynab_map_name_to_id = {acc.name: acc.ynab_id for acc in ynab_accounts}

    account_aliases = load_aliases()
    aliases_modified = False

    for fw_acc in fw_accounts:
        # Determine the name to check against YNAB
        target_name = account_aliases.get(fw_acc.name, fw_acc.name)

        if target_name not in ynab_account_names:
            # Check if this name came from an explicit alias configuration
            if fw_acc.name in account_aliases:
                print(
                    f"\nAccount '{fw_acc.name}' mapped to '{target_name}' in config, but not found in YNAB."
                )
                final_name = target_name
            else:
                print(f"\nAccount '{fw_acc.name}' not found in YNAB.")
                user_input = input(
                    f"Enter YNAB account name for '{fw_acc.name}' (default: '{fw_acc.name}'): "
                ).strip()

                final_name = user_input if user_input else fw_acc.name

                # Ensure we save the alias even if it's the same, so we don't ask again next time if logic changes
                # But wait, current logic only asks if "target_name not in ynab_account_names".
                # If target_name IS found, we don't ask.
                # account_aliases.get(fw_acc.name, fw_acc.name) returns name if not in alias.
                # So if we want to explicitly store "My Bank" -> "My Bank" in config.json:

                account_aliases[fw_acc.name] = final_name
                aliases_modified = True
                if final_name != fw_acc.name:
                    print(f"Alias saved: '{fw_acc.name}' -> '{final_name}'")
                else:
                    print(
                        f"Alias saved: '{fw_acc.name}' -> '{final_name}' (Same as original)"
                    )

            if final_name in ynab_account_names:
                print(
                    f"Mapped '{fw_acc.name}' to existing YNAB account '{final_name}'."
                )
                target_name = final_name
            else:
                print(f"Creating account '{final_name}' in YNAB...")
                try:
                    adjustment = 0
                    account_txns = []
                    if fw_acc.finwise_id:
                        account_txns = [
                            t
                            for t in fw_transactions_for_adj
                            if t.account_id == fw_acc.finwise_id
                        ]
                        adjustment = sum(t.amount for t in account_txns)

                    original_balance = fw_acc.balance
                    calculated_starting_balance = original_balance - adjustment

                    print(f"  Current Balance: {original_balance / 1000:.2f}")
                    print(f"  Transactions since {start_date}: {len(account_txns)}")
                    print(f"  Adjustment: {adjustment / 1000:.2f}")
                    print(
                        f"  Calculated Starting Balance: {calculated_starting_balance / 1000:.2f}"
                    )

                    fw_acc.name = final_name
                    fw_acc.balance = int(calculated_starting_balance)
                    ynab_client.create_account(budget_id, fw_acc)
                    fw_acc.balance = original_balance

                    print(f"Account '{final_name}' created successfully.")
                    ynab_account_names.add(final_name)
                    # Refresh our ID map if possible, but YNAB creates SB txn automatically here.
                    continue  # Skip reset logic for brand new accounts
                except Exception as e:
                    print(f"Failed to create account '{final_name}': {e}")
                    continue
        else:
            print(
                f"Account '{fw_acc.name}' (mapped as '{target_name}') already exists in YNAB."
            )
            # Ensure mapping is saved even if implicit (same name) or if it exists in YNAB but not in config
            if fw_acc.name not in account_aliases:
                account_aliases[fw_acc.name] = target_name
                aliases_modified = True
                print(f"Implicit alias saved: '{fw_acc.name}' -> '{target_name}'")

    if aliases_modified:
        save_aliases(account_aliases)


def sync_transactions(
    finwise_client: FinWiseClient, ynab_client: YNABClient, budget_id: str
):
    print("\n--- Transaction Sync ---")

    # 1. Fetch Accounts to map IDs
    print("Mapping accounts...")
    try:
        fw_accounts = finwise_client.get_accounts()
        ynab_accounts = ynab_client.get_accounts(budget_id)
        account_aliases = load_aliases()

        # Build map of FinWise ID -> FinWise Account Name for display
        fw_id_to_name = {}
        for acc in fw_accounts:
            if acc.finwise_id:
                fw_id_to_name[acc.finwise_id] = acc.name

        # Map FinWise Account ID -> YNAB Account ID via Name
        # We need a map: {fw_acc.id: ynab_acc.ynab_id}
        # First build name map for YNAB
        ynab_map_by_name = {
            acc.name: acc.ynab_id for acc in ynab_accounts if acc.ynab_id
        }

        fw_id_to_ynab_id = {}
        for fw_acc in fw_accounts:
            if fw_acc.finwise_id:  # Account model from FinWise has finwise_id populated mapping from its own id
                # Determine the lookup name using alias if available
                lookup_name = account_aliases.get(fw_acc.name, fw_acc.name)

                if lookup_name in ynab_map_by_name:
                    fw_id_to_ynab_id[fw_acc.finwise_id] = ynab_map_by_name[lookup_name]
                else:
                    print(
                        f"Warning: Account '{fw_acc.name}' (mapped as '{lookup_name}') found in FinWise but not mapped to YNAB. Transactions for this account will be skipped."
                    )

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
        end_date = None  # date.today()
        start_date = date.today().replace(day=1)

        fw_transactions = finwise_client.get_transactions(
            start_date=start_date, end_date=end_date
        )
        ynab_transactions = ynab_client.get_transactions(
            budget_id, start_date=start_date
        )  # YNAB API handles start_date

        print(f"FinWise Transactions: {len(fw_transactions)}")
        print(f"YNAB Transactions: {len(ynab_transactions)}")

    except Exception as e:
        print(f"Failed to fetch transactions: {e}")
        return

    # 2.5 Apply Payee Aliasing
    print("Processing payee aliases...")
    payee_rules = load_payee_rules()
    merchant_aliases = load_merchant_aliases()

    # Fetch existing YNAB payees to avoid prompting for known ones
    try:
        ynab_payees_list = ynab_client.get_payees(budget_id)
        ynab_payee_names = {p.name for p in ynab_payees_list}
    except Exception as e:
        print(f"Failed to fetch payees: {e}")
        ynab_payee_names = set()

    session_ignored_payees = set()
    session_ignored_merchants = set()
    rules_modified = False
    merchant_aliases_modified = False

    for fw_txn in fw_transactions:
        # FinWiseTransaction uses 'description' as payee_name and memo in 'from_finwise'
        # Transaction model has 'payee_name'
        original_payee = fw_txn.payee_name if fw_txn.payee_name else ""
        if not original_payee:
            continue

        # Priority 1: Merchant ID Aliasing
        if fw_txn.merchant_id:
            if fw_txn.merchant_id in merchant_aliases:
                fw_txn.payee_name = merchant_aliases[fw_txn.merchant_id]
                continue

            if fw_txn.merchant_id in session_ignored_merchants:
                continue

            # Check if we should prompt for this merchant ID
            # If the original payee (description) is already a known YNAB payee,
            # we might want to skip, BUT since the user wants to map IDs explicitly,
            # maybe we should check if the ID is unmapped first.

            # Let's check if the description matches a regex rule first as a fallback/helper?
            # No, requirement says "map merchant_id to a payee alias".

            # However, if the description exactly matches a YNAB payee, maybe we don't need an alias?
            # But the user said "merchant_id is unreadable", so relying on description
            # matching YNAB payee might be flaky if the description changes.
            # Let's prompt if ID is unknown, but maybe offer the description as default.

            # Get account name for display
            account_name = fw_id_to_name.get(fw_txn.account_id, "Unknown Account")
            amount_val = fw_txn.amount / 1000.0

            print(f"\nAccount: '{account_name}' | Amount: {amount_val:.2f}")
            print(f"Merchant ID: {fw_txn.merchant_id}")
            print(f"Merchant Name: {fw_txn.merchant_name}")
            print(f"Description: {original_payee}")

            target = input(
                f"Enter YNAB Payee for this merchant (or Press Enter to skip/ignore): "
            ).strip()

            if target:
                merchant_aliases[fw_txn.merchant_id] = target
                merchant_aliases_modified = True
                save_merchant_aliases(merchant_aliases)  # Save immediately

                fw_txn.payee_name = target
                ynab_payee_names.add(target)
                print(f"Mapping saved: {fw_txn.merchant_id} -> {target}")
                continue
            else:
                session_ignored_merchants.add(fw_txn.merchant_id)
                # Fall through to regex logic or keep original?
                # If ignored, we probably just keep original description
                # or let regex logic try to clean it up.
                pass

        # Priority 2: Regex / Description Aliasing (Fallback)
        final_payee = original_payee
        matched_rule = False

        # Check existing rules (iterate through current list which includes newly added ones)
        for rule in payee_rules:
            try:
                if re.search(rule["pattern"], original_payee):
                    final_payee = rule["target"]
                    matched_rule = True
                    break
            except re.error:
                print(
                    f"Warning: Invalid regex pattern '{rule['pattern']}' in config. Skipping."
                )
                continue

        if matched_rule:
            fw_txn.payee_name = final_payee
            continue

        # If not matched, check if exists in YNAB or ignored
        if final_payee in ynab_payee_names:
            continue

        if original_payee in session_ignored_payees:
            continue

        # Only prompt for regex alias if we haven't already prompted for Merchant ID
        # (If we prompted for Merchant ID and they skipped, we might still fall here.
        # That's okay, maybe they want to regex map the description instead.)

        # Get account name for display
        account_name = fw_id_to_name.get(fw_txn.account_id, "Unknown Account")

        # Format amount
        amount_val = fw_txn.amount / 1000.0

        # Prompt user
        print(
            f"\nAccount: '{account_name}' | Amount: {amount_val:.2f} | Unknown Payee: '{original_payee}'"
        )

        while True:
            target = input(
                f"Enter alias for '{original_payee}' (or Press Enter to keep as-is): "
            ).strip()

            if not target:
                session_ignored_payees.add(original_payee)
                break

            pattern_input = input(
                f"Enter search term to match for alias '{target}' (default: matches '{original_payee}'): "
            ).strip()

            try:
                # Generate case-insensitive regex pattern
                if pattern_input:
                    escaped_choice = re.escape(pattern_input)
                else:
                    escaped_choice = re.escape(original_payee)

                pattern = f"(?i).*{escaped_choice}.*"

                # Validate regex (should be valid by construction, but good practice)
                re.compile(pattern)

                # Verify that the generated pattern actually matches the current payee
                if not re.search(pattern, original_payee):
                    print(
                        f"Error: The generated regex ({pattern}) does not match the current payee '{original_payee}'. Please try again."
                    )
                    continue

                new_rule = {"pattern": pattern, "target": target}
                payee_rules.append(new_rule)
                rules_modified = True
                # Save immediately to ensure safety
                save_payee_rules(payee_rules)

                fw_txn.payee_name = target
                ynab_payee_names.add(
                    target
                )  # Add to known list so we don't prompt again for target
                print(f"Rule added: Matches '{pattern}' -> Maps to '{target}'")
                break
            except re.error:
                print(
                    "Generated regex was invalid (unexpected). Skipping rule creation."
                )
                # Loop back to let user retry
                continue

    if rules_modified:
        save_payee_rules(payee_rules)

    if merchant_aliases_modified:
        save_merchant_aliases(merchant_aliases)

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
        check_amount = fw_txn.amount  # already in milliunits in Transaction model

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
                # Load current salt
                current_salt = load_salt()
                # Truncate to 36 chars max (UUID is 36).
                # We need length of salt.
                # Assuming salt is short, e.g. "_rev8" (5 chars)
                salt_len = len(current_salt)
                prefix_len = 36 - salt_len

                fw_txn.import_id = f"{fw_txn.import_id[:prefix_len]}{current_salt}"

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

    import sys

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

        budget_id = load_budget_id()

        # Verify stored budget_id is valid
        if budget_id:
            found_budget = next((b for b in budgets if b.id == budget_id), None)
            if found_budget:
                print(f"Using stored budget: {found_budget.name}")
            else:
                print(
                    f"Stored budget ID {budget_id} not found in YNAB. Please re-select."
                )
                budget_id = None

        if not budget_id:
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

            # Save the new selection
            if budget_id:
                save_budget_id(budget_id)

        if budget_id:
            # Sync Accounts
            sync_accounts(fw_client, ynab_client, budget_id)

            # Sync Transactions
            sync_transactions(fw_client, ynab_client, budget_id)

    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
