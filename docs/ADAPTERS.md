# Boot adapters

Um adapter ensina o PenLive a bootar uma família de ISOs. É o ponto de
extensão principal do projeto.

## Interface

```python
class BootAdapter(ABC):
    family: str

    def detect(self, iso: IsoImage) -> int:
        """Confiança 0-100 de que este adapter sabe bootar esta ISO."""

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        """Extrai o que precisar e devolve como bootar."""
```

`detect()` devolve **confiança, não booleano**. Uma ISO do Ubuntu casa com
`/casper/` e também com `/EFI/BOOT/BOOTX64.EFI` do adapter genérico; a pontuação
faz o registry escolher o mais específico em vez do primeiro que responder
"talvez".

Escala em uso:

| Faixa | Uso |
|---|---|
| 85–95 | assinatura específica da distro (`/casper/vmlinuz`) |
| 40–60 | família mais ampla, sem certeza de variante |
| 10 | fallback genérico (só existe `BOOTX64.EFI`) |
| 0 | não casa |

## `BootConfig`

```python
BootConfig(
    method="linux",              # ou "chainload"
    label="Ubuntu",
    kernel="vmlinuz",            # nome do arquivo dentro de extract_dir
    initrd="initrd",
    cmdline="boot=casper iso-scan/filename=/images/x.iso quiet ---",
    iso_rel_path="images/x.iso", # caminho relativo à raiz de PENDATA
)
```

`kernel`/`initrd` são **só nomes de arquivo**. Quem monta o caminho final
(`boot/extracted/<image_id>/...`) é o `bootmanager`; o adapter não precisa saber
onde o arquivo vai parar.

`iso_rel_path` é o caminho da ISO **no pendrive**, e é o que entra no `cmdline`.
Usar o caminho temporário de inspeção aqui é o erro clássico: boota e dá kernel
panic porque o sistema-alvo não acha o próprio root. Existe teste para isso.

## Métodos

**`linux`** — extrai kernel e initrd, boota direto pelo GRUB e aponta o initrd
do sistema-alvo para a ISO original. Preferível: não depende do bootloader
interno da ISO.

**`chainload`** — GRUB dá `loopback` na ISO e `chainloader` no `BOOTX64.EFI`
dela. Não extrai nada. Fallback para ISOs híbridas sem adapter dedicado.

## Adapters existentes

| Família | Assinatura | Score | cmdline |
|---|---|---|---|
| `ubuntu` | `/casper/vmlinuz` | 95 | `boot=casper iso-scan/filename=` |
| `debian` | `/live/vmlinuz` | 90 | `boot=live findiso=` |
| `proxmox` | `/boot/linux26` | 90 | `ro ramdisk_size=... findiso=` |
| `fedora` | `/images/pxeboot/vmlinuz` | 85 | `inst.stage2=hd:LABEL=PENDATA:` |
| `arch` | `/arch/boot/x86_64/vmlinuz-linux` | 85 | `img_dev=... img_loop=` |
| `generic` | `/EFI/BOOT/BOOTX64.EFI` | 10 | chainload |

> Os `cmdline` de Arch e Proxmox variam entre versões. Trate como ponto de
> partida: confira `/loader/entries/*.conf` ou `/boot/grub/grub.cfg` dentro da
> ISO específica antes de confiar em produção.

## Escrever um novo adapter

1. Crie `manager/backend/app/adapters/minhadistro.py`.
2. Descubra a assinatura: monte a ISO (ou use `pycdlib`) e ache kernel/initrd.
3. Descubra o `cmdline`: leia o `isolinux.cfg`, `grub.cfg` ou
   `loader/entries/*.conf` **de dentro da ISO** — é a fonte autoritativa.
4. Registre em `registry.py` (`REGISTRY`).
5. Adicione uma fixture em `tests/isofactory.py` e um caso no
   `@pytest.mark.parametrize` de `tests/test_adapters.py`.

```python
class MinhaDistroAdapter(BootAdapter):
    family = "minhadistro"

    def detect(self, iso: IsoImage) -> int:
        return 85 if iso.exists("/minhadistro/vmlinuz") else 0

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        kernel = iso.extract_file("/minhadistro/vmlinuz", extract_dir / "vmlinuz")
        initrd = iso.extract_file("/minhadistro/initrd", extract_dir / "initrd")
        return BootConfig(
            method="linux",
            label="Minha Distro",
            kernel=kernel.name,
            initrd=initrd.name,
            cmdline=f"root=live:CDLABEL=PENDATA iso={iso_rel_path}",
            iso_rel_path=iso_rel_path,
        )
```

Os testes usam ISOs sintéticas de poucos KB (`tests/isofactory.py`) — o que
importa testar é a detecção de caminho, não o payload.

## Windows: por que ainda não existe

Windows não é "mais um adapter". A ISO tem `/sources/boot.wim`,
`/sources/install.wim`, `/boot/bcd`, `/boot/boot.sdi` — não há kernel/initrd
para extrair, e o boot passa por **wimboot** carregando WinPE, que só então roda
o `setup.exe`. É um terceiro `method`, com extração e geração de config
próprias. Entra depois que a abstração atual estiver validada em hardware real
com as distros Linux.
