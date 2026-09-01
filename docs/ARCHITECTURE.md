# Arquitetura

Este documento registra **por que** cada decisão foi tomada. O código diz o que
faz; aqui está o raciocínio que não sobrevive em comentários.

## Cadeia de boot

```
UEFI
 └─ EFI/BOOT/BOOTX64.EFI          (GRUB standalone, na partição BOOTEFI)
     └─ configfile → BOOTSYS/boot/grub/grub.cfg
         ├─ menuentry "BootStack Manager"        (padrão)
         ├─ source boot/state/nextboot.cfg       (se existir e attempts < 3)
         └─ source boot/grub/recovery.cfg        (sempre)
             └─ vmlinuz + initrd → live-boot
                 ├─ BOOTSYS/live/filesystem.squashfs   (read-only)
                 └─ partição LABEL=persistence         (OverlayFS, gravável)
                     └─ systemd
                         ├─ NetworkManager
                         ├─ bootstack-daemon   (root, socket Unix)
                         ├─ bootstack-aria2    (usuário bootstack)
                         ├─ bootstack-api      (usuário bootstack, :7777)
                         └─ bootstack-kiosk    (X + Chromium fullscreen)
```

### Por que o GRUB embutido é mínimo

`grub-mkstandalone` gera um `BOOTX64.EFI` autossuficiente, mas regravá-lo é
chato. Então o config embutido faz só duas coisas: achar a partição pelo label
`BOOTSYS` e dar `configfile` no `grub.cfg` de verdade. Toda a lógica de menu
vive num arquivo comum numa partição ext4 — atualizar o menu é sobrescrever um
arquivo, não reconstruir o binário EFI.

### Watchdog de boot (o detalhe que evita "brickar")

O GRUB não tem operador de incremento nem `rm`. Isso moldou duas coisas:

1. O contador `boot_attempts` sobe por uma cadeia `if/elif` explícita em
   `grub.cfg`, não por `+= 1`. Chegando a 3, o pending boot é ignorado e o
   Manager inicia normalmente. Um `cmdline` errado gerado por um adapter não
   deixa o usuário preso num loop de boot.
2. "Cancelar pending boot" pelo GRUB **não apaga** o arquivo — o GRUB não
   consegue. Ele boota o Manager com `bootstack.clear_pending=1` e quem apaga é
   o daemon, já dentro do Linux.

`recovery.cfg` é sourced incondicionalmente **no fim** do `grub.cfg`, depois de
toda a lógica de pending boot, justamente para continuar acessível se algo
anterior falhar.

## Partições

| # | Label | FS | Tamanho | Conteúdo |
|---|---|---|---|---|
| 1 | `BOOTEFI` | FAT32 | 512 M | `EFI/BOOT/BOOTX64.EFI` |
| 2 | `BOOTSYS` | ext4 | 4 G | `live/` + `boot/state/` + `boot/extracted/` |
| 3 | `persistence` | ext4 | 8 G | OverlayFS do live-boot |
| 4 | `BOOTDATA` | exFAT | resto | `images/`, `catalog/`, `logs/` |

Três decisões que valem explicação:

**`boot/state` fica em BOOTSYS (ext4), não em BOOTDATA (exFAT).** O GRUB lê o
`nextboot.cfg` direto da partição crua, antes de existir Linux ou OverlayFS. O
suporte a ext4 no GRUB é muito mais testado que o de exfat, e não vale arriscar
a cadeia de boot inteira nisso.

**BOOTDATA é exFAT mesmo assim.** É a partição que o usuário enxerga ao plugar
o pendrive em qualquer Windows/macOS/Linux para copiar uma ISO na mão. Nada que
o GRUB precise ler no boot mora lá — exceto no método `chainload`, que é
justamente por isso o único a dar `insmod exfat`.

**ISOs ≠ persistência.** Downloads vão para `BOOTDATA`, estado do sistema vai
para `persistence`. Um factory reset (formatar `persistence`, recriar
`persistence.conf`) preserva todos os downloads. Se as ISOs morassem no overlay,
resetar o sistema custaria dezenas de GB de re-download.

## Separação de privilégios

```
Chromium kiosk  (usuário bootstack)
      │ HTTP localhost:7777
bootstack-api   (usuário bootstack)  ← parseia entrada não-confiável
      │ socket Unix, JSON por linha
bootstack-daemon (root)              ← lista fixa de comandos
```

A API é quem processa entrada não-confiável: JSON de catálogo remoto e o
conteúdo interno de ISOs baixadas. Por isso ela **não** roda como root. Tudo que
exige privilégio vai por um socket Unix para um daemon cuja superfície inteira é
a lista em `app/daemon/protocol.py`:

```
ping · mount_image · umount · write_nextboot · clear_nextboot
reboot · kexec_boot · write_usb
```

O daemon nunca recebe um comando de shell; recebe um nome dessa lista e
argumentos nomeados. Não existe caminho "executa esta string". A validação
acontece nos dois lados — o cliente recusa um comando fora da lista antes mesmo
de abrir o socket, e o servidor recusa de novo ao receber. Um teste garante que
`ALLOWED_COMMANDS` e os handlers implementados não divirjam.

`write_usb` reaproveita as guardas de `builder/bootstack/safety.py` em vez de
reimplementá-las: recusa nome de partição, recusa o disco do sistema em
execução.

## Adapters de boot

O erro que quebra a maioria das tentativas de "GRUB que boota qualquer ISO" é
supor que existe um método universal. Não existe: cada distro põe kernel e
initrd em lugares diferentes e espera parâmetros diferentes para achar o próprio
root filesystem.

A abstração aqui:

```python
detect(iso)  -> int   # confiança 0-100
prepare(iso, extract_dir, iso_rel_path) -> BootConfig
```

`detect()` devolve **confiança, não booleano**. Uma ISO do Ubuntu tem
`/casper/` e também `/EFI/BOOT/BOOTX64.EFI`; o adapter genérico casaria também.
Com pontuação, o registry escolhe o melhor (Ubuntu 95 > genérico 10) em vez do
primeiro que responder "talvez". Há teste cobrindo exatamente esse conflito.

Dois métodos de saída:

- **`linux`** — extrai kernel/initrd da ISO para `BOOTSYS/boot/extracted/<id>/`
  e monta um `menuentry` que passa a ISO original como root via `findiso=`,
  `iso-scan/filename=`, `inst.stage2=`, etc. Muito mais previsível que
  chainload, porque não depende do bootloader interno da ISO.
- **`chainload`** — para ISOs híbridas sem adapter dedicado: `loopback` +
  `chainloader` no `BOOTX64.EFI` de dentro da própria ISO. Não extrai nada.

A inspeção usa **pycdlib**, não loop mount: ler ISO9660 em espaço de usuário
não exige root. Root só entra bem depois, no botão "Montar".

> Os `cmdline` de Arch e Proxmox mudam entre versões. Estão marcados no código
> como ponto de partida a verificar contra a ISO específica, não como garantia.

## Downloads

`aria2c` roda como **serviço systemd próprio**, não como filho da API. Um
download de 3 GB precisa sobreviver a um restart da API; no startup,
`downloader.resume_watchers()` reconecta aos GIDs ainda ativos. O arquivo
parcial e o controle `.aria2` ficam em `BOOTDATA`, que é persistido — então
retomar depois de desligar a máquina funciona de verdade.

Ordem que garante que "existe em `images/`" signifique "confiável":

```
aria2 → images/.downloads/x.iso.part → SHA-256 → rename atômico → images/x.iso
```

Verificação falhou → arquivo apagado, status `corrupted`. O `rename()` só
acontece depois do hash conferir, então nada meio-baixado ou adulterado aparece
como pronto para bootar.

## Catálogo

`catalog.json` aponta direto para os servidores oficiais das distros — o projeto
não hospeda ISO nenhuma. Ordem de fallback: remoto → cache em `BOOTDATA` →
cópia embutida na squashfs. Offline, a UI ainda mostra o catálogo.

Manter hash na mão é como esse tipo de catálogo apodrece: a distro lança um
point release, a URL passa a servir outro arquivo, e todo download falha na
verificação. `tools/update_catalog.py` busca o arquivo de checksum oficial de
cada fornecedor e reescreve as entradas. Ele entende os três formatos que os
fornecedores realmente usam (`hash  arquivo`, com `*` binário, e o estilo BSD
`SHA256 (arquivo) = hash` do Fedora dentro do bloco assinado por PGP).

## Modo de desenvolvimento

`app/paths.py` decide, em tempo de import, se está no sistema live. Fora dele
(ou com `BOOTSTACK_DEV=1`), tudo aponta para `./devdata/` e as operações
privilegiadas retornam `503` em vez de estourar. É o que permite desenvolver a
UI inteira em Windows/macOS sem pendrive e sem root.

Em produção `ensure_dirs()` deliberadamente **não** cria `/data` e `/boot`: se a
partição não montou, criar o diretório esconderia a falha escrevendo no overlay.
Melhor deixar faltar e reportar.

## O que não foi feito, e por quê

Na ordem em que faz sentido atacar:

1. **Windows / wimboot** — não é "mais um adapter": é um método de boot
   completamente diferente (WIM/WinPE, BCD, `boot.sdi`). Entra depois que a
   abstração de adapter estiver validada em hardware com as distros Linux.
2. **Update A/B do Manager** — `system-a.squashfs` / `system-b.squashfs` com
   rollback pelo GRUB. O layout de partição já reserva espaço pensando nisso.
3. **Secure Boot** — exige shim assinado pela Microsoft e cadeia de assinatura
   no builder; multiplica a complexidade de build e update.
4. **BIOS legacy** — só se aparecer necessidade real. Suportar apenas UEFI
   x86-64 elimina uma quantidade enorme de casos especiais.
