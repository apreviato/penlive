# Boot adapters

An adapter teaches PenLive how to boot one family of ISOs. It is the project's
main extension point.

## Interface

```python
class BootAdapter(ABC):
    family: str

    def detect(self, iso: IsoImage) -> int:
        """Confidence 0-100 that this adapter knows how to boot this ISO."""

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        """Extract whatever is needed and return how to boot it."""
```

`detect()` returns **confidence, not a boolean**. An Ubuntu ISO matches
`/casper/` and also the generic adapter's `/EFI/BOOT/BOOTX64.EFI`; scoring makes
the registry pick the more specific one instead of the first to answer "maybe".

The scale in use:

| Range | Meaning |
|---|---|
| 85–95 | distribution-specific signature (`/casper/vmlinuz`) |
| 40–60 | broader family, variant not certain |
| 10 | generic fallback (only `BOOTX64.EFI` exists) |
| 0 | no match |

## `BootConfig`

```python
BootConfig(
    method="linux",              # or "chainload"
    label="Ubuntu",
    kernel="vmlinuz",            # file name inside extract_dir
    initrd="initrd",
    cmdline="boot=casper iso-scan/filename=/images/x.iso quiet ---",
    iso_rel_path="images/x.iso", # path relative to the root of PENDATA
)
```

`kernel`/`initrd` are **file names only**. The `bootmanager` builds the final
path (`boot/extracted/<image_id>/...`); an adapter does not need to know where
the file ends up.

`iso_rel_path` is the path of the ISO **on the stick**, and it is what goes into
the `cmdline`. Using the temporary inspection path here is the classic mistake:
it boots and kernel-panics because the target system cannot find its own root.
There is a test for exactly that.

## Methods

**`linux`** — extracts kernel and initrd, boots straight from GRUB, and points
the target system's initrd at the original ISO. Preferred: it does not depend on
the ISO's own bootloader.

**`chainload`** — GRUB `loopback`s the ISO and `chainloader`s its
`BOOTX64.EFI`. Extracts nothing. A fallback for hybrid ISOs with no dedicated
adapter.

## Existing adapters

| Family | Signature | Score | cmdline |
|---|---|---|---|
| `ubuntu` | `/casper/vmlinuz` | 95 | `boot=casper iso-scan/filename=` |
| `debian` | `/live/vmlinuz` | 90 | `boot=live findiso=` |
| `proxmox` | `/boot/linux26` | 90 | `ro ramdisk_size=... findiso=` |
| `fedora` | `/images/pxeboot/vmlinuz` | 85 | `inst.stage2=hd:LABEL=PENDATA:` |
| `arch` | `/arch/boot/x86_64/vmlinuz-linux` | 85 | `img_dev=... img_loop=` |
| `generic` | `/EFI/BOOT/BOOTX64.EFI` | 10 | chainload |

Several catalog entries reuse an existing adapter rather than needing a new
one: Linux Mint is Ubuntu-derived and uses the casper layout, and Rocky Linux
is RHEL-family with the Anaconda `/images/pxeboot` layout.

> The Arch and Proxmox `cmdline` values vary between releases. Treat them as a
> starting point: check `/loader/entries/*.conf` or `/boot/grub/grub.cfg` inside
> the specific ISO before relying on them in production.

## Writing a new adapter

1. Create `manager/backend/app/adapters/mydistro.py`.
2. Find the signature: mount the ISO (or use `pycdlib`) and locate
   kernel/initrd.
3. Find the `cmdline`: read `isolinux.cfg`, `grub.cfg` or
   `loader/entries/*.conf` **from inside the ISO** — that is the authoritative
   source.
4. Register it in `registry.py` (`REGISTRY`).
5. Add a fixture to `tests/isofactory.py` and a case to the
   `@pytest.mark.parametrize` in `tests/test_adapters.py`.

```python
class MyDistroAdapter(BootAdapter):
    family = "mydistro"

    def detect(self, iso: IsoImage) -> int:
        return 85 if iso.exists("/mydistro/vmlinuz") else 0

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        kernel = iso.extract_file("/mydistro/vmlinuz", extract_dir / "vmlinuz")
        initrd = iso.extract_file("/mydistro/initrd", extract_dir / "initrd")
        return BootConfig(
            method="linux",
            label="My Distro",
            kernel=kernel.name,
            initrd=initrd.name,
            cmdline=f"root=live:CDLABEL=PENDATA iso={iso_rel_path}",
            iso_rel_path=iso_rel_path,
        )
```

The tests use synthetic ISOs of a few KB (`tests/isofactory.py`) — what matters
is testing path detection, not the payload.

## Windows: why it does not exist yet

Windows is not "another adapter". The ISO has `/sources/boot.wim`,
`/sources/install.wim`, `/boot/bcd`, `/boot/boot.sdi` — there is no
kernel/initrd to extract, and booting goes through **wimboot** loading WinPE,
which only then runs `setup.exe`. It is a third `method`, with its own
extraction and config generation. It comes after the current abstraction has
been validated on real hardware with the Linux distributions.
