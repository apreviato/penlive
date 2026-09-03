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
    method="linux",              # or "chainload" / "wimboot"
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

**`wimboot`** — Windows only. GRUB loads the wimboot binary as if it were a
kernel, and passes `bootmgfw.efi`, the BCD, `boot.sdi` and `boot.wim` in a
single in-memory cpio archive using GRUB's `newc:<name>:<path>` syntax. See
"Windows" below.

## Existing adapters

| Family | Signature | Score | cmdline |
|---|---|---|---|
| `ubuntu` | `/casper/vmlinuz` | 95 | `boot=casper iso-scan/filename=` |
| `debian` | `/live/vmlinuz` | 90 | `boot=live findiso=` |
| `proxmox` | `/boot/linux26` | 90 | `ro ramdisk_size=... findiso=` |
| `fedora` | `/images/pxeboot/vmlinuz` | 85 | Live: `root=live:CDLABEL=... rd.live.image iso-scan/filename=`; installer: `inst.stage2=hd:LABEL=PENDATA:` |
| `arch` | `/arch/boot/x86_64/vmlinuz-linux` | 85 | `img_dev=... img_loop=` |
| `debian-installer` | `/install.amd/vmlinuz` | 80 | `iso-scan/filename=` |
| `windows` | `/sources/boot.wim` | 90 | wimboot (see below) |
| `generic` | `/EFI/BOOT/BOOTX64.EFI` | 10 | chainload |

Several catalog entries reuse an existing adapter rather than needing a new
one: Linux Mint is Ubuntu-derived and uses the casper layout; Rocky Linux and
AlmaLinux are RHEL-family with the Anaconda `/images/pxeboot` layout; and
Clonezilla Live and GParted Live are built with `live-build`, so the Debian
adapter boots them unchanged.

Fedora uses the same kernel path for Workstation Live and installer media, but
their initrds expect different arguments. The adapter distinguishes them by
`/LiveOS/squashfs.img`; Live media receives its ISO9660 volume label plus the
ISO file path, while installer media keeps Anaconda's `inst.stage2` argument.

SystemRescue and openSUSE match none of them and fall back to
`GenericEfiAdapter`. Chainloading a loopback-mounted ISO is best-effort — the
target's own bootloader still has to find its media — so those two are the
first candidates if a new adapter is worth writing.

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

## Windows

Windows is not "another Linux adapter". There is no kernel and no initrd: the
ISO holds `/sources/boot.wim`, `/boot/boot.sdi` and a BCD store, and the
firmware is meant to run `bootmgfw.efi`. Chainloading that from a
loopback-mounted ISO does not work — Windows' boot manager cannot read GRUB's
loop device — so `WindowsAdapter` uses [wimboot](https://ipxe.org/wimboot)
(iPXE project, GPL), a small loader GRUB *can* start like a kernel.

Three pieces have to line up, and only one of them is the adapter:

1. **`wimboot` on PENSYS.** Debian does not package it, so the builder takes a
   path: `penlive image --wimboot ./wimboot`. Without it the adapter raises
   `WimbootMissing` and the image stays mount-and-VM-only rather than offering
   a Boot button that cannot work. See [BUILD.md](BUILD.md).
2. **The four boot files**, extracted into the per-image cache like any other
   adapter's kernel. The BCD comes from `/efi/microsoft/boot/bcd`; the one at
   `/boot/bcd` next to it is the BIOS variant and boots UEFI into recovery.
3. **The ISO contents unpacked onto PENDATA**, by `services/winmedia.py`.
   wimboot only carries WinPE; Windows Setup then looks for
   `\sources\install.wim`, and WinPE cannot mount an ISO by itself. PENDATA
   is exFAT, so an `install.wim` over 4 GiB fits. The inspector does this right
   after the download, so by boot time it is already there.

### UDF

A Windows ISO keeps a single readme in its ISO9660 tree and everything else in
UDF, so `IsoImage` reads UDF as well — without it an adapter sees an empty
image. UDF names are case-sensitive and Microsoft writes them lowercase, which
`_path_variants` handles so callers need not care.

One consequence is worth knowing before changing detection scores: with UDF
readable, `GenericEfiAdapter` also matches Windows media through
`/efi/boot/bootx64.efi`. It scores 10 against `WindowsAdapter`'s 90, and that
gap is what keeps Windows off a chainload that cannot boot.
