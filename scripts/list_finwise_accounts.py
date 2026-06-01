"""Print all FinWise accounts with their raw SDK data."""
import json
from finwise import FinWise

client = FinWise()
accounts = client.accounts.list()

for acc in accounts:
    data = acc.model_dump() if hasattr(acc, "model_dump") else acc.dict()
    print(json.dumps(data, indent=2, default=str))
    print()
