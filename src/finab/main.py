from datetime import date
from dotenv import load_dotenv
from finab.client import FinWiseClient


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


if __name__ == "__main__":
    main()
