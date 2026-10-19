from __future__ import annotations

import hashlib
import json

QQ_DELIVERY_RECEIPT_SECRET = "openrouter_managed_qq_delivery_receipt_v1"


def encode_receipt(
    *,
    key: str,
    installation: str,
    operation: str | None,
    reference: str,
    delivery: str,
    token: str,
    expires: str | None,
) -> str:
    return json.dumps(
        dict(
            version=1,
            source="qq",
            key_digest=hashlib.sha256(key.encode()).hexdigest(),
            installation=installation,
            operation=operation,
            reference=reference,
            delivery=delivery,
            token=token,
            expires=expires,
            acknowledged=False,
        ),
        separators=(",", ":"),
    )


def decode_receipt(value: str | None) -> dict | None:
    if value is None:
        return None
    receipt = json.loads(value)
    if (
        not isinstance(receipt, dict)
        or receipt.get("version") != 1
        or receipt.get("source") != "qq"
    ):
        raise ValueError("Invalid QQ delivery receipt")
    for name in ("key_digest", "installation", "reference", "delivery", "token"):
        if not isinstance(receipt.get(name), str) or not receipt[name]:
            raise ValueError("Incomplete QQ delivery receipt")
    if type(receipt.get("acknowledged")) is not bool:
        raise ValueError("Invalid QQ delivery confirmation")
    return receipt


def receipt_matches(
    receipt: dict, *, key: str | None, installation: str | None, reference: str | None
) -> bool:
    return bool(
        key
        and receipt["key_digest"] == hashlib.sha256(key.encode()).hexdigest()
        and receipt["installation"] == installation
        and receipt["reference"] == reference
    )
