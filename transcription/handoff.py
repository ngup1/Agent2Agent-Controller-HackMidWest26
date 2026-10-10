import json


async def handle_customer_turn(event: dict) -> None:
    # Replace this with your teammate's workflow integration later.
    print(json.dumps(event, indent=2), flush=True)
