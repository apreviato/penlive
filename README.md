# PenLive

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
| `penlive` CLI (particiona GPT, formata, instala GRUB, provisiona) | pronto, com `--dry-run` |
| GRUB: boot manager, pending boot, watchdog de 3 tentativas, recovery | pronto |
| Debian `live-build` (pacotes, hooks, systemd, kiosk) | pronto |
| Backend FastAPI + SQLite | pronto |
| Daemon privilegiado (socket Unix, comandos restritos) | pronto |
| Download resumível via aria2 + verificação SHA-256 | pronto |
| Adapters: Debian, Ubuntu, Fedora, Arch, Proxmox, EFI genérico | pronto |
| Frontend React em modo kiosk (inglês) | pronto |
| Boot nativo, Run VM (QEMU/KVM), Mount, Write-to-USB | pronto |
| Barra de status estilo SO: IP, teclado, armazenamento, relógio | pronto |
| Assistente de primeira execução (teclado + rede) | pronto |
| Trava de kiosk em três camadas (VT, WM, navegador) | pronto |
| 8 ferramentas: backup, restore, SMART, reparos, recuperação, provisionamento | pronto |
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
cd manager/backend && PENLIVE_DEV=1 python -m uvicorn app.main:app --port 7777
```

Abra <http://127.0.0.1:7777>. O catálogo carrega da cópia local em
`catalog/catalog.json`; Wi-Fi, boot, mount e as ferramentas retornam `503`
porque dependem do sistema live — a interface trata isso e continua utilizável,
mostrando o motivo em cada caso.

## Interface

```
┌──────────────────────────────────────────────────────────────┐
│ ▣ PenLive   ● HomeWifi 192.168.1.42  ⌨ BR  ▤ 127 GB  09:41 ⏻│
├──────────────────────────────────────────────────────────────┤
│  Systems  │  Tools  │  Settings                              │
└──────────────────────────────────────────────────────────────┘
```

Barra de status sempre visível com estado da conexão, IP, layout de teclado,
espaço livre, relógio e desligar. Três abas: **Systems** (catálogo, download,
boot, VM, mount), **Tools** (as 8 ferramentas) e **Settings** (rede, teclado,
informações do sistema).

Na primeira execução aparece um assistente de duas etapas — teclado, depois
rede. Depois disso o boot vai direto para o catálogo, e o assistente só volta
se a máquina estiver sem conexão.

## Ferramentas

Backup · Restore · SMART · Filesystem Check & Repair · Linux Boot Repair ·
Windows Repair · Deleted File Recovery · Provision Machine

Todas gerenciadas pela interface, com console de saída ao vivo e cancelamento.
Ações destrutivas exigem confirmação explícita, e as partições do próprio
pendrive aparecem marcadas com `⚠ PenLive`.

Um plugin **não** consegue ampliar o que o sistema faz: ele produz o *nome* de
uma operação mais argumentos estruturados, e quem monta a linha de comando é o
daemon. Ver [docs/PLUGINS.md](docs/PLUGINS.md).

## Testes

```bash
make test
```

174 testes: 20 no builder (particionamento, guardas de segurança, plano de
provisionamento) e 154 no backend (adapters contra ISOs sintéticas, geração de
menuentry do GRUB, verificação SHA-256, protocolo do daemon, validação
adversarial de argumentos privilegiados, runner de jobs, inventário de discos,
API HTTP e schema do catálogo).

## Construir o pendrive de verdade

Precisa de um host **Debian/Ubuntu** e root. Um único comando guiado cuida de
tudo — dependências, interface, sistema live, escolha do dispositivo e gravação:

```bash
sudo ./scripts/make-usb.sh
```

Ele pergunta antes de cada passo irreversível, pula o que já está pronto (o
build do sistema live leva 20-40 min e não precisa ser repetido para regravar
um pendrive), e ajusta as partições sozinho em pendrives pequenos. No fim,
valida o resultado e explica como bootar.

Regravar um segundo pendrive reaproveitando o build:

```bash
sudo ./scripts/make-usb.sh --device /dev/sdb --skip-build
```

> A gravação apaga o disco inteiro. O script sempre exige que você digite o
> caminho do dispositivo para confirmar — nem `--yes` pula essa etapa — e se
> recusa a escrever no disco do sistema em execução.

Os passos manuais, e como testar em QEMU antes de usar hardware real, estão em
[docs/BUILD.md](docs/BUILD.md).

## Layout do pendrive

```
GPT
├── p1  PENEFI      FAT32   512M   EFI/BOOT/BOOTX64.EFI
├── p2  PENSYS      ext4    4G     live/ (kernel, initrd, squashfs) + boot/state + boot/extracted
├── p3  persistence  ext4    8G     OverlayFS (live-boot) — configurações, Wi-Fi
└── p4  PENDATA     exFAT   resto  images/ (ISOs), catalog/, logs/
```

ISOs ficam em `PENDATA`, **separadas** da persistência: resetar o sistema
(factory reset) não apaga nenhum download. `PENDATA` é exFAT para que o
pendrive possa ser usado em Windows/macOS para copiar ISOs manualmente.

## Documentação

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — decisões de design e o porquê de cada uma
- [docs/BUILD.md](docs/BUILD.md) — construir, gravar e testar em QEMU
- [docs/ADAPTERS.md](docs/ADAPTERS.md) — como funciona e como escrever um adapter
- [docs/PLUGINS.md](docs/PLUGINS.md) — as ferramentas, o modelo de segurança e como adicionar uma

## Escopo e limites

**Verificado nesta implementação:** testes automatizados, build do frontend, API
rodando ponta a ponta com a interface clicada no navegador (assistente,
catálogo, ferramentas, formulário de backup e console de job com streaming),
plano de comandos do builder em dry-run, e os checksums do catálogo conferidos
contra os arquivos oficiais de cada distribuição.

**Não verificado:** nada foi testado em hardware real nem em UEFI. O boot
nativo, a persistência OverlayFS, o kiosk Chromium, a trava de terminais
virtuais e os `cmdline` dos adapters precisam de validação em QEMU/hardware
antes de qualquer uso sério — comece por [docs/BUILD.md](docs/BUILD.md), seção
"Testar em QEMU".

As ferramentas privilegiadas foram exercitadas contra um daemon simulado (a
validação de argumentos e o runner de jobs têm testes próprios), mas nenhuma
rodou contra discos reais. Antes de confiar em Backup/Restore/Provisioning,
teste em discos descartáveis dentro de uma VM.

**Fora do escopo por enquanto** (decisão consciente, na ordem sugerida):
Windows via wimboot, updates A/B do próprio Manager, Secure Boot e BIOS legacy.
