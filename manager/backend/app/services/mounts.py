"""'Mount' action: expose a downloaded ISO's filesystem read-only, via the daemon (loop mount needs root)."""
from __future__ import annotations

from .. import paths
from ..daemon import client as daemon_client

_active_mounts: dict[str, str] = {}  # image_id -> mountpoint


async def mount(image_id: str, iso_path: str) -> str:
    mountpoint = str(paths.MOUNTS_DIR / image_id)
    result = await daemon_client.call("mount_image", path=iso_path, mountpoint=mountpoint, readonly=True)
    _active_mounts[image_id] = result["mountpoint"]
    return result["mountpoint"]


async def unmount(image_id: str) -> None:
    mountpoint = _active_mounts.pop(image_id, str(paths.MOUNTS_DIR / image_id))
    await daemon_client.call("umount", mountpoint=mountpoint)
