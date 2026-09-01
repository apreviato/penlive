import pytest

from app import plugins
from app.daemon import operations, procedures

KNOWN_KINDS = set(operations.OPERATIONS) | set(procedures.PROCEDURES)


def test_registry_has_the_eight_documented_tools():
    assert {p.id for p in plugins.REGISTRY} == {
        "backup", "restore", "smart", "disk-repair",
        "linux-repair", "windows-repair", "file-recovery", "provisioning",
    }


def test_plugin_ids_are_unique():
    ids = [p.id for p in plugins.REGISTRY]
    assert len(ids) == len(set(ids))


def test_every_plugin_declares_a_known_category():
    for plugin in plugins.REGISTRY:
        assert plugin.category in plugins.CATEGORY_LABELS, f"{plugin.id} has an unlabelled category"


def test_manifests_serialize():
    for plugin in plugins.REGISTRY:
        manifest = plugin.manifest()
        assert manifest["id"] and manifest["name"] and manifest["description"]
        for param in manifest["params"]:
            assert param["name"] and param["label"] and param["type"]


@pytest.mark.parametrize(
    "plugin_id,values",
    [
        ("smart", {"device": "/dev/sda", "mode": "report"}),
        ("smart", {"device": "/dev/sda", "mode": "short"}),
        ("disk-repair", {"device": "/dev/sda1", "fstype": "ext", "repair": False}),
        ("disk-repair", {"device": "/dev/sda1", "fstype": "ntfs", "repair": True}),
        ("backup", {"device": "/dev/sda1", "name": "b1", "fstype": "ext"}),
        ("backup", {"device": "/dev/sda1", "name": "b1", "fstype": "raw"}),
        ("restore", {"image": "b1.pcl", "device": "/dev/sda1", "fstype": "ext"}),
        ("file-recovery", {"device": "/dev/sda1", "name": "r1", "filetype": "everything"}),
        ("linux-repair", {"root_device": "/dev/sda2", "esp_device": "/dev/sda1", "action": "both"}),
        ("windows-repair", {"windows_device": "/dev/sda3", "action": "fix_filesystem"}),
    ],
)
def test_build_job_targets_a_real_daemon_operation(plugin_id, values):
    """A typo in a plugin's `kind` would produce a tool that always fails at the
    daemon boundary, and only at runtime."""
    spec = plugins.get(plugin_id).build_job(values)
    assert spec.kind in KNOWN_KINDS, f"{plugin_id} builds unknown kind {spec.kind!r}"
    assert spec.title


def test_disk_repair_check_and_repair_map_to_different_operations():
    check = plugins.get("disk-repair").build_job({"device": "/dev/sda1", "fstype": "ext", "repair": False})
    repair = plugins.get("disk-repair").build_job({"device": "/dev/sda1", "fstype": "ext", "repair": True})
    assert check.kind == "fsck_check"
    assert repair.kind == "fsck_repair"


def test_backup_raw_uses_the_sector_copy_operation():
    spec = plugins.get("backup").build_job({"device": "/dev/sda1", "name": "b", "fstype": "raw"})
    assert spec.kind == "backup_partition_raw"
    assert "fstype" not in spec.args  # raw copies have no filesystem to declare


def test_destructive_plugins_are_flagged():
    """The UI gates on this to require an explicit confirmation checkbox."""
    assert plugins.get("restore").danger == "destructive"
    assert plugins.get("provisioning").danger == "destructive"
    assert plugins.get("smart").danger == "safe"


def test_provisioning_rejects_an_image_that_is_not_downloaded(temp_db):
    from app.plugins.base import PluginError
    with pytest.raises(PluginError, match="not downloaded"):
        plugins.get("provisioning").build_job(
            {"image_id": "nope", "target_device": "/dev/sdb"}
        )


def test_unknown_plugin_id_raises():
    from app.plugins.base import PluginError
    with pytest.raises(PluginError, match="unknown tool"):
        plugins.get("does-not-exist")
