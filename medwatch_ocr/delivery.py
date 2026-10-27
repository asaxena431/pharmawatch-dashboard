"""Delivery of a generated message to a submission gateway's inbound folder.

``none`` keeps the message in the browser only; ``dev`` and ``dev-postmkt``
write it to the DEV gateway's inbound directory for CVM drug submissions and
for CDRH post-market submissions respectively, under a name no earlier run can
have used, so a resubmitted case never overwrites a message waiting to be
picked up.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime
from typing import Dict, Optional

DESTINATION_NONE = "none"
DESTINATION_DEV = "dev"
DESTINATION_DEV_POSTMARKET = "dev-postmkt"

# The DEV gateway's inbound folders, overridable where the shares are mapped elsewhere.
DEV_INBOUND = os.environ.get(
    "MEDWATCH_DEV_INBOUND",
    r"\\FDSWV26252\lsmvdev\aersesubdev\inbound\cvm-drug\xml_cvm-drug",
)
DEV_POSTMARKET_INBOUND = os.environ.get(
    "MEDWATCH_DEV_POSTMKT_INBOUND",
    r"\\FDSWV26252\lsmvdev\aersesubdev\inbound\cdrh\tt_3500A_emdr",
)

DESTINATIONS: Dict[str, Optional[str]] = {
    DESTINATION_NONE: None,
    DESTINATION_DEV: DEV_INBOUND,
    DESTINATION_DEV_POSTMARKET: DEV_POSTMARKET_INBOUND,
}


class DeliveryError(RuntimeError):
    """The message could not be written to the destination folder."""


def unique_name(source: str, extension: str = ".xml", moment: Optional[datetime] = None) -> str:
    """A name for the message that is this run's alone.

    The source form's name stays legible, and a timestamp to the millisecond
    plus a short random suffix keep two runs of the same PDF apart.
    """
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.splitext(os.path.basename(source or "message"))[0]).strip("_")
    stamp = (moment or datetime.now()).strftime("%Y%m%d%H%M%S%f")[:-3]
    return f"{stem or 'message'}-{stamp}-{uuid.uuid4().hex[:8]}{extension}"


def deliver(xml: str, destination: str, source: str, directory: Optional[str] = None) -> Optional[str]:
    """Write ``xml`` where ``destination`` says, returning the path it took.

    ``none`` writes nothing and returns ``None``.
    """
    destination = (destination or DESTINATION_NONE).strip().lower()
    if destination == DESTINATION_NONE:
        return None
    if destination not in DESTINATIONS:
        raise DeliveryError(f"unknown destination: {destination}")
    folder = directory or DESTINATIONS[destination]
    if not folder:
        raise DeliveryError(f"no folder is configured for destination {destination}")
    path = os.path.join(folder, unique_name(source))
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(xml)
    except OSError as exc:
        raise DeliveryError(f"{folder} is not writable: {exc}") from exc
    return path
