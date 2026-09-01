# BootStack

Um pendrive que **é** um gerenciador de sistemas operacionais: liga o computador,
conecta no Wi-Fi, escolhe um sistema de um catálogo, baixa, verifica e boota —
sem precisar de um segundo computador, sem regravar o pendrive a cada ISO.

```
UEFI → GRUB → Debian Live (SquashFS + OverlayFS) → FastAPI + Chromium kiosk
                                                        ↓
                                          catálogo · download · boot · VM
```

## O que já está implementado

| Camada | Estado |
|---|---|
| `bootstack` CLI (particiona GPT, formata, instala GRUB, provisiona) | pronto, com `--dry-run` |
| GRUB: boot manager, pending boot, watchdog de 3 tentativas, recovery | pronto |
| Debian `live-build` (pacotes, hooks, systemd, kiosk) | pronto |
| Backend FastAPI + SQLite | pronto |
| Daemon privilegiado (socket Unix, comandos restritos) | pronto |
| Download resumível via aria2 + verificação SHA-256 | pronto |
| Adapters: Debian, Ubuntu, Fedora, Arch, Proxmox, EFI genérico | pronto |
| Frontend React em modo kiosk (pt-BR) | pronto |
| Boot nativo, Run VM (QEMU/KVM), Mount, Write-to-USB | pronto |
| Windows / wimboot, update A/B, Secure Boot, BIOS legacy | **não implementado** — ver [Escopo](#escopo-e-limites) |

## Início rápido (desenvolvimento)

Roda em qualquer SO — sem pendrive, sem root. O backend detecta que não está no
sistema live e usa `./devdata/` no lugar das partições reais.

```bash
make backend-deps
```

```bash
cd manager/frontend && npm ci && npm run build
```

```bash
cd manager/backend && BOOTSTACK_DEV=1 python -m uvicorn app.main:app --port 7777
```

Abra <http://127.0.0.1:7777>. O catálogo carrega da cópia local em
`catalog/catalog.json`; Wi-Fi, boot e mount retornam `503` porque dependem do
sistema live — a interface trata isso e continua utilizável.

## Testes

```bash
make test
```

71 testes: 21 no builder (particionamento, guardas de segurança, plano de
provisionamento) e 50 no backend (adapters contra ISOs sintéticas, geração de
menuentry do GRUB, protocolo do daemon, repositório, API HTTP, schema do
catálogo).

## Construir o pendrive de verdade

Precisa de um host **Debian/Ubuntu** com `live-build`, e root. Ver
[docs/BUILD.md](docs/BUILD.md) para o passo a passo completo.

```bash
sudo apt install live-build grub-efi-amd64-bin gdisk dosfstools exfatprogs zstd
```

```bash
make live
```

```bash
sudo PYTHONPATH=builder python -m bootstack.cli devices
```

```bash
sudo PYTHONPATH=builder python -m bootstack.cli install /dev/sdb --live-dir live/build/out
```

> `install` apaga o disco inteiro. Ele exige que você digite o caminho do
> dispositivo para confirmar e se recusa a escrever no disco do sistema em
> execução. Use `--dry-run` primeiro para ver o plano exato de comandos.

## Layout do pendrive

```
GPT
├── p1  BOOTEFI      FAT32   512M   EFI/BOOT/BOOTX64.EFI
├── p2  BOOTSYS      ext4    4G     live/ (kernel, initrd, squashfs) + boot/state + boot/extracted
├── p3  persistence  ext4    8G     OverlayFS (live-boot) — configurações, Wi-Fi
└── p4  BOOTDATA     exFAT   resto  images/ (ISOs), catalog/, logs/
```

ISOs ficam em `BOOTDATA`, **separadas** da persistência: resetar o sistema
(factory reset) não apaga nenhum download. `BOOTDATA` é exFAT para que o
pendrive possa ser usado em Windows/macOS para copiar ISOs manualmente.

## Documentação

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — decisões de design e o porquê de cada uma
- [docs/BUILD.md](docs/BUILD.md) — construir, gravar e testar em QEMU
- [docs/ADAPTERS.md](docs/ADAPTERS.md) — como funciona e como escrever um adapter

## Escopo e limites

**Verificado nesta implementação:** testes automatizados, build do frontend,
API rodando ponta a ponta, plano de comandos do builder em dry-run, e os
checksums do catálogo conferidos contra os arquivos oficiais de cada
distribuição.

**Não verificado:** nada foi testado em hardware real nem em UEFI. O boot
nativo, a persistência OverlayFS, o kiosk Chromium e os `cmdline` dos adapters
precisam de validação em QEMU/hardware antes de qualquer uso sério — comece por
[docs/BUILD.md](docs/BUILD.md), seção "Testar em QEMU".

**Fora do escopo por enquanto** (decisão consciente, na ordem sugerida):
Windows via wimboot, updates A/B do próprio Manager, Secure Boot e BIOS legacy.
