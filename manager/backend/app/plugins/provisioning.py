from __future__ import annotations

from typing import Any

from .. import paths, repo
from .base import JobSpec, Param, Plugin, PluginError


class ProvisioningPlugin(Plugin):
    id = "provisioning"
    name = "Provision Machine"
    description = (
        "Write a downloaded system image directly onto this machine's disk and "
        "optionally seed an unattended-install answer file. Intended for "
        "deploying many identical machines."
    )
    category = "provisioning"
    danger = "destructive"
    icon = "⚑"
    required_tools = ("dd",)

    params = (
        Param(
            name="image_id",
            label="System image",
            type="select",
            options=[],  # filled in per-request by available_options()
            help="Only downloaded and verified images can be deployed.",
        ),
        Param(
            name="target_device",
            label="Target disk",
            type="device",
            help="The ENTIRE disk is erased. The PenLive stick itself and the running system disk are refused.",
        ),
        Param(
            name="verify",
            label="Verify after writing",
            type="checkbox",
            required=False,
            default=True,
            help="Reads the target back and compares it against the source image.",
        ),
    )

    def available_options(self) -> dict[str, list[dict[str, str]]]:
        """Downloaded images, offered as choices for image_id."""
        ready = [
            img for img in repo.list_images()
            if img.get("path") and img["status"] in ("downloaded", "ready")
        ]
        return {
            "image_id": [{"value": img["id"], "label": img["name"]} for img in ready]
        }

    def build_job(self, values: dict[str, Any]) -> JobSpec:
        image = repo.get_image(values.get("image_id", ""))
        if not image or not image.get("path"):
            raise PluginError("that image is not downloaded")

        seed_path = None
        if values.get("seed_name"):
            candidate = paths.DATA_MOUNT / "provisioning" / str(values["seed_name"])
            if not candidate.is_file():
                raise PluginError(f"no such seed file: {values['seed_name']}")
            seed_path = str(candidate)

        return JobSpec(
            "provision_apply",
            {
                "image_path": image["path"],
                "target_device": values["target_device"],
                "seed_path": seed_path,
                "verify": bool(values.get("verify", True)),
            },
            f"Provision {values['target_device']} with {image['name']}",
        )
