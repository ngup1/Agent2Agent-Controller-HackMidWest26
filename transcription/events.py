from datetime import datetime, timezone


def customer_turn(call_id: str, turn_index: int, text: str) -> dict:
    return {
        "type": "customer_turn.completed",
        "call_id": call_id,
        "turn_id": f"{call_id}:{turn_index}",
        "text": text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
