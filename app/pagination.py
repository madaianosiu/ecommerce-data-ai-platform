"""Opaque cursor for keyset pagination on (order_purchase_at, order_id)."""
import base64
import binascii
import json
from datetime import datetime


class InvalidCursor(ValueError):
    pass


def encode_cursor(purchase_at: datetime, order_id: str) -> str:
    raw = json.dumps({"t": purchase_at.isoformat(), "id": order_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        purchase_at = datetime.fromisoformat(data["t"])
        order_id = data["id"]
        if not isinstance(order_id, str) or not order_id:
            raise ValueError("empty id")
        return purchase_at, order_id
    except (binascii.Error, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise InvalidCursor("Invalid cursor: use the next_cursor value from a previous response.") from exc
