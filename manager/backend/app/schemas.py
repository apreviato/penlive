from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class Capabilities(BaseModel):
    nativeBoot: bool = True
    kexec: bool = False
    vm: bool = False
    mount: bool = True


class ImageOut(BaseModel):
    id: str
    name: str
    family: str
    version: str | None = None
    architecture: str = "amd64"
    adapter: str | None = None
    path: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    status: str
    verified: bool = False
    origin: str = "catalog"
    inspection_error: str | None = None
    source_url: str | None = None
    capabilities: dict[str, Any] = {}


class DownloadStart(BaseModel):
    image_id: str


class DownloadOut(BaseModel):
    id: int
    image_id: str
    gid: str | None = None
    progress_bytes: int
    total_bytes: int | None = None
    speed_bps: int
    state: str
    error: str | None = None


class WifiNetwork(BaseModel):
    ssid: str
    signal: int
    security: str
    known: bool = False
    connected: bool = False


class NetworkStatus(BaseModel):
    connected: bool
    internet: bool | None = None
    connectivity: str | None = None
    ssid: str | None = None
    ip_address: str | None = None
    interface: str | None = None


class WifiConnectRequest(BaseModel):
    ssid: str
    password: str | None = None


class BootRequest(BaseModel):
    image_id: str
    method: Literal["linux", "chainload", "auto"] = "auto"
    allow_unverified: bool = False


class VmRequest(BaseModel):
    image_id: str
    memory_mib: int = 4096
    cpus: int = 2
    enable_kvm: bool = True


class WriteUsbRequest(BaseModel):
    image_id: str
    target_device: str


class PendingBootOut(BaseModel):
    image_id: str
    image_name: str
    method: str
    attempts: int
    max_attempts: int = 3
    created_at: str


class StorageOut(BaseModel):
    data_total_bytes: int
    data_free_bytes: int
    images_bytes: int


class SettingsOut(BaseModel):
    settings: dict[str, str]


class FileFolderRequest(BaseModel):
    parent: str = ""
    name: str


class FileRenameRequest(BaseModel):
    path: str
    name: str


class FileFolderSourceRequest(BaseModel):
    source: str = "pendata"
    parent: str = ""
    name: str


class FileRenameSourceRequest(BaseModel):
    source: str = "pendata"
    path: str
    name: str


class FileTransferRequest(BaseModel):
    source: str
    path: str
    destination: str
    destination_path: str = ""
    move: bool = False


class DeviceMountRequest(BaseModel):
    device: str
