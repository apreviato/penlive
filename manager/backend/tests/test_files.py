from pathlib import Path

import pytest

from app import paths
from app.services import files


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "images" / ".downloads").mkdir(parents=True)
    (root / "catalog").mkdir()
    (root / "logs").mkdir()
    (root / "documents").mkdir()
    (root / "documents" / "notes.txt").write_text("hello")
    monkeypatch.setattr(paths, "DATA_MOUNT", root)
    monkeypatch.setattr(paths, "IMAGES_DIR", root / "images")
    return root


def test_file_manager_is_confined_to_pendata(data_root):
    with pytest.raises(files.FileManagerError):
        files.resolve("../../etc/passwd")
    with pytest.raises(files.FileManagerError):
        files.resolve("/etc/passwd")


def test_file_manager_creates_renames_and_deletes(data_root):
    created = files.create_folder("", "Portable files")
    assert created["created"] == "Portable files"
    renamed = files.rename("documents/notes.txt", "renamed.txt")
    assert renamed["path"] == "documents/renamed.txt"
    files.delete("documents/renamed.txt")
    assert not (data_root / "documents" / "renamed.txt").exists()


def test_file_manager_protects_manager_owned_paths(data_root):
    for relative in ("images", "images/.downloads", "catalog", "logs"):
        with pytest.raises(files.FileManagerError, match="managed"):
            files.delete(relative, recursive=True)


def test_listing_marks_iso_and_managed_entries(data_root):
    (data_root / "images" / "rescue.ISO").write_bytes(b"iso")
    root = files.list_directory("")
    assert next(entry for entry in root["entries"] if entry["name"] == "images")["protected"] is True
    images = files.list_directory("images")
    iso = next(entry for entry in images["entries"] if entry["name"] == "rescue.ISO")
    assert iso["iso"] is True
    assert iso["protected"] is False


def test_copy_and_move_between_validated_roots(data_root, tmp_path):
    external = tmp_path / "external"
    external.mkdir()

    copied = files.transfer(
        "documents/notes.txt", "", source_root=data_root, destination_root=external
    )
    assert copied == {"path": "notes.txt", "moved": False}
    assert (external / "notes.txt").read_text() == "hello"

    (data_root / "imports").mkdir()
    moved = files.transfer(
        "notes.txt", "imports", source_root=external, destination_root=data_root, move=True
    )
    assert moved == {"path": "imports/notes.txt", "moved": True}
    assert not (external / "notes.txt").exists()


def test_transfer_refuses_overwrite(data_root, tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    (external / "notes.txt").write_text("keep")
    with pytest.raises(files.FileManagerError, match="already exists"):
        files.transfer("documents/notes.txt", "", source_root=data_root, destination_root=external)


def test_transfer_refuses_folder_destination_inside_itself(data_root):
    (data_root / "documents" / "nested").mkdir()
    with pytest.raises(files.FileManagerError, match="inside itself"):
        files.transfer(
            "documents",
            "documents/nested",
            source_root=data_root,
            destination_root=data_root,
        )
