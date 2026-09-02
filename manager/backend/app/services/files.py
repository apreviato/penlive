"""Safe file management scoped to the user-visible PENDATA partition."""
from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath

from .. import paths

PROTECTED_ROOTS = {"images", "catalog", "logs"}


class FileManagerError(ValueError):
    pass


def _parts(relative: str) -> tuple[str, ...]:
    if not isinstance(relative, str) or "\x00" in relative:
        raise FileManagerError("invalid path")
    normalized = relative.replace("\\", "/")
    if normalized.startswith("/"):
        raise FileManagerError("path must stay inside the PenLive data partition")
    clean = normalized.strip("/")
    path = PurePosixPath(clean)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        if clean:
            raise FileManagerError("path must stay inside the PenLive data partition")
        return ()
    return path.parts


def resolve(relative: str, *, root: Path | None = None, must_exist: bool = True) -> Path:
    root = (root or paths.DATA_MOUNT).resolve()
    candidate = root.joinpath(*_parts(relative)).resolve(strict=False)
    if candidate != root and root not in candidate.parents:
        raise FileManagerError("path escapes the PenLive data partition")
    if must_exist and not candidate.exists():
        raise FileManagerError(f"path does not exist: {relative}")
    return candidate


def _relative(path: Path, root: Path | None = None) -> str:
    return path.relative_to((root or paths.DATA_MOUNT).resolve()).as_posix()


def _protected(relative: str, *, allow_inside_images: bool = True) -> bool:
    parts = _parts(relative)
    if not parts:
        return True
    if parts[0] in {"catalog", "logs"}:
        return True
    if parts[0] == "images":
        if len(parts) == 1:
            return True
        if parts[1] == ".downloads":
            return True
        return not allow_inside_images
    return False


def list_directory(relative: str = "", *, root: Path | None = None, managed: bool = True) -> dict:
    root = (root or paths.DATA_MOUNT).resolve()
    directory = resolve(relative, root=root)
    if not directory.is_dir():
        raise FileManagerError("the selected path is not a folder")
    entries = []
    for path in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        try:
            stat = path.stat()
        except OSError:
            continue
        rel = _relative(path, root)
        entries.append({
            "name": path.name,
            "path": rel,
            "kind": "directory" if path.is_dir() else "file",
            "size": None if path.is_dir() else stat.st_size,
            "modified": stat.st_mtime,
            "extension": path.suffix.lower() if path.is_file() else None,
            "protected": managed and _protected(rel),
            "iso": path.is_file() and path.suffix.lower() == ".iso",
        })
    return {"path": PurePosixPath(relative.replace("\\", "/").strip("/")).as_posix() if relative else "", "entries": entries}


def _valid_name(name: str) -> str:
    if not isinstance(name, str):
        raise FileManagerError("name must be text")
    name = name.strip()
    if not name or len(name) > 128 or name in {".", ".."}:
        raise FileManagerError("name must contain 1-128 characters")
    if any(char in name for char in ("/", "\\", "\x00", "\n", "\r")):
        raise FileManagerError("name cannot contain path separators or control characters")
    return name


def create_folder(parent: str, name: str, *, root: Path | None = None, managed: bool = True) -> dict:
    root = (root or paths.DATA_MOUNT).resolve()
    parent_path = resolve(parent, root=root)
    if not parent_path.is_dir():
        raise FileManagerError("parent is not a folder")
    target = parent_path / _valid_name(name)
    relative = _relative(target, root)
    if managed and _protected(relative):
        raise FileManagerError("that location is managed by PenLive")
    try:
        target.mkdir()
    except FileExistsError as exc:
        raise FileManagerError("a file or folder with that name already exists") from exc
    except OSError as exc:
        raise FileManagerError(f"could not create folder: {exc}") from exc
    return {"created": relative}


def rename(relative: str, name: str, *, root: Path | None = None, managed: bool = True) -> dict:
    root = (root or paths.DATA_MOUNT).resolve()
    source = resolve(relative, root=root)
    if managed and _protected(relative):
        raise FileManagerError("that file or folder is managed by PenLive")
    target = source.with_name(_valid_name(name))
    target_rel = _relative(target, root)
    if (managed and _protected(target_rel)) or target.exists():
        raise FileManagerError("the destination is protected or already exists")
    try:
        source.rename(target)
    except OSError as exc:
        raise FileManagerError(f"could not rename item: {exc}") from exc
    return {"old_path": relative, "path": target_rel}


def delete(
    relative: str, *, recursive: bool = False, root: Path | None = None, managed: bool = True
) -> dict:
    root = (root or paths.DATA_MOUNT).resolve()
    target = resolve(relative, root=root)
    if managed and _protected(relative):
        raise FileManagerError("that file or folder is managed by PenLive")
    try:
        if target.is_dir():
            if recursive:
                shutil.rmtree(target)
            else:
                target.rmdir()
        else:
            target.unlink()
    except OSError as exc:
        raise FileManagerError(f"could not delete item: {exc}") from exc
    return {"deleted": relative}


def transfer(
    source_path: str,
    destination_folder: str,
    *,
    source_root: Path,
    destination_root: Path,
    move: bool = False,
    source_managed: bool = False,
    destination_managed: bool = False,
) -> dict:
    """Copy or move one item between validated, mounted file sources."""
    source = resolve(source_path, root=source_root)
    if source.is_symlink():
        raise FileManagerError("symbolic links cannot be copied through the file manager")
    if source_managed and move and _protected(source_path):
        raise FileManagerError("that file or folder is managed by PenLive")
    destination_dir = resolve(destination_folder, root=destination_root)
    if not destination_dir.is_dir():
        raise FileManagerError("destination is not a folder")
    target = destination_dir / source.name
    target_relative = _relative(target, destination_root)
    if (destination_managed and _protected(target_relative)) or target.exists():
        raise FileManagerError("the destination is protected or already exists")
    resolved_target = target.resolve(strict=False)
    if source.is_dir() and (resolved_target == source or source in resolved_target.parents):
        raise FileManagerError("a folder cannot be copied or moved inside itself")
    try:
        if move:
            shutil.move(str(source), str(target))
        elif source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    except OSError as exc:
        raise FileManagerError(f"could not {'move' if move else 'copy'} item: {exc}") from exc
    return {"path": target_relative, "moved": move}


def affects_images(*relatives: str) -> bool:
    return any(_parts(relative)[:1] == ("images",) for relative in relatives)
