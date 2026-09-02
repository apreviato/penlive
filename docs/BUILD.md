# Construir, gravar e testar

## Requisitos

Host **Debian 13 / Ubuntu 24.04+**, com root. `live-build` só roda em Debian ou
derivado — em outro SO, use uma VM ou container Debian.

```bash
sudo apt install live-build grub-efi-amd64-bin grub-common gdisk dosfstools exfatprogs e2fsprogs zstd qemu-system-x86 ovmf nodejs npm python3
```

## Caminho rápido: script guiado

Faz todos os passos deste documento numa sequência só, perguntando antes de
cada etapa irreversível:

```bash
sudo ./scripts/make-usb.sh
```

O que ele resolve além de encadear comandos:

- **Dependências** — detecta o que falta e oferece instalar via apt.
- **Etapas já prontas** — reaproveita frontend e sistema live existentes em vez
  de reconstruir (o live-build leva 20-40 min).
- **npm como usuário** — roda `npm ci` com `SUDO_USER`, para não deixar
  `node_modules` pertencente ao root no seu repositório.
- **Pendrive pequeno** — um pendrive de "16 GB" tem ~14,9 GiB e não cabe no
  layout padrão (precisa de 16,5 GiB). O script reduz a partição de
  persistência automaticamente, em vez de falhar com um erro cru no meio do
  processo. Abaixo de ~13 GiB ele recusa com uma explicação.
- **Escolha do alvo** — lista os discos marcando o disco do sistema e os não
  removíveis, e desmonta partições montadas antes de gravar.
- **Confirmação** — exige digitar o caminho do dispositivo. Nem `--yes` pula.

Opções úteis:

```bash
sudo ./scripts/make-usb.sh --device /dev/sdb --skip-build
```

```bash
sudo ./scripts/make-usb.sh --data-fs ext4 --persist-mib 16384
```

Log completo em `make-usb.log`.

## No Windows

Um comando faz tudo — instala o que falta, constrói e grava:

```powershell
.\scripts\make-usb.ps1
```

Precisa de PowerShell **como administrador**. Se não houver WSL com uma
distribuição Debian, ele oferece instalar (`wsl --install -d Debian
--no-launch`); depois instala as dependências de build, constrói a interface e
o sistema live, e grava o pendrive.

`live-build` só roda em Debian, então a metade Linux acontece dentro do WSL.
Há dois caminhos, tentados nesta ordem:

**1. Passagem do disco (preferido).** `wsl --mount --bare` entrega o pendrive
cru ao WSL, e o mesmo `make-usb.sh` que os usuários Linux rodam particiona e
grava. Usa o pendrive **inteiro**, e existe um único caminho de código testado
em vez de uma reimplementação para Windows.

**2. Imagem (reserva).** Onde a passagem não funciona, o WSL gera uma `.img` de
tamanho fixo e o PowerShell grava a partir do Windows. Mais simples, mas a
partição de dados fica limitada ao tamanho da imagem em vez de ocupar o
pendrive todo.

O build acontece dentro do sistema de arquivos do WSL, nunca em `/mnt/d`: o
`debootstrap` precisa de nós de dispositivo e permissões reais, que o DrvFs não
representa, então um chroot construído ali quebra no meio.

Gravar uma imagem já pronta, sem construir nada:

```powershell
.\scripts\make-usb.ps1 -Image .\dist\penlive-amd64.img.zst
```

As mesmas garantias do script Linux valem: o disco do sistema e unidades não
removíveis são recusados, e a gravação exige digitar o número do disco. No
caminho de imagem, o disco é lido de volta e comparado byte a byte
(`-NoVerify` pula).

Arquivos `.img.zst` precisam de `zstd.exe` no PATH
(`winget install Facebook.Zstandard`).

> O `docker-desktop` que o Docker Desktop cria **não** serve como distribuição
> de build — não tem apt nem systemd — e o script o ignora explicitamente em vez
> de falhar 30 minutos depois.

> **Interop do WSL.** O WSL acrescenta todo o PATH do Windows ao do Linux, então
> um `command -v npm` dentro do Debian pode encontrar
> `C:\Program Files\nodejs\npm`. A verificação de dependências passaria, o Node
> nunca seria instalado no Debian, e o build quebraria bem mais tarde dentro do
> `CMD.EXE` reclamando que caminhos UNC não são suportados. O script remove as
> entradas `/mnt/` do PATH e trata qualquer binário que resolva em `/mnt/` como
> ausente.

> Se o WSL acabou de ser habilitado, o Windows precisa reiniciar antes de
> instalar distribuições. O script detecta isso e avisa em vez de falhar de
> forma obscura.

O restante deste documento descreve os mesmos passos manualmente, útil para
depurar uma etapa específica.

## 1. Frontend

Obrigatório antes do live: a API serve o `dist/` como arquivos estáticos, então
sem ele o sistema live sobe sem interface.

```bash
cd manager/frontend && npm ci && npm run build
```

## 2. Sistema live

```bash
sudo ./live/build.sh
```

Demora bastante (baixa um Debian inteiro) e produz:

```
live/build/out/vmlinuz
live/build/out/initrd.img
live/build/out/filesystem.squashfs
```

O script copia backend, builder, catálogo, units systemd e o `dist/` do frontend
para dentro do chroot, e o hook `0200-install-manager` cria a venv com as
dependências já resolvidas — o sistema live precisa funcionar sem rede.

## 3a. Gravar direto num pendrive

```bash
sudo PYTHONPATH=builder python -m penlive.cli devices
```

**Sempre rode o dry-run primeiro.** Ele imprime a sequência exata de comandos
sem executar nada:

```bash
sudo PYTHONPATH=builder python -m penlive.cli install /dev/sdb --dry-run --live-dir live/build/out
```

Conferido o plano:

```bash
sudo PYTHONPATH=builder python -m penlive.cli install /dev/sdb --live-dir live/build/out
```

Ele exige que você digite `/dev/sdb` para confirmar. As guardas recusam nome de
partição (`/dev/sdb1`) e o disco do sistema em execução.

Opções úteis: `--data-fs ext4` (se o pendrive nunca vai ver Windows),
`--persist-mib`, `--system-mib`, `--log auditoria.txt`.

Verificar depois:

```bash
sudo PYTHONPATH=builder python -m penlive.cli validate /dev/sdb
```

## 3b. Gerar uma imagem distribuível

Não toca em disco físico nenhum — monta tudo num loop device:

```bash
sudo PYTHONPATH=builder python -m penlive.cli image dist/penlive-amd64.img \
    --size-mib 16384 --live-dir live/build/out --compress
```

Resultado: `dist/penlive-amd64.img.zst`, que o usuário final grava com
Rufus, balenaEtcher ou `dd`.

## 4. Testar em QEMU (faça isso antes do hardware)

```bash
sudo qemu-system-x86_64 -enable-kvm -m 4096 -smp 2 \
    -bios /usr/share/ovmf/OVMF.fd \
    -drive file=/dev/sdb,format=raw,if=virtio \
    -netdev user,id=n0 -device virtio-net-pci,netdev=n0
```

Ou contra a imagem, sem pendrive:

```bash
sudo qemu-system-x86_64 -enable-kvm -m 4096 -smp 2 \
    -bios /usr/share/ovmf/OVMF.fd \
    -drive file=dist/penlive-amd64.img,format=raw,if=virtio
```

Roteiro mínimo de validação:

1. GRUB aparece e "PenLive Manager" boota.
2. Chromium sobe fullscreen na tela de rede (sem desktop visível).
3. Conectar no Wi-Fi (ou usar a rede do QEMU) e chegar no catálogo.
4. Baixar uma ISO; matar a VM em ~50% e reiniciar — o download deve retomar.
5. Verificação SHA-256 passa e o status vira `ready`.
6. "Bootar" agenda o pending boot; reiniciar deve cair no instalador.
7. Reiniciar mais 3× com um pending boot quebrado — o watchdog deve desistir e
   voltar ao Manager.
8. Persistência: reiniciar e confirmar que o Wi-Fi reconecta sozinho.

## Manutenção do catálogo

```bash
python tools/update_catalog.py
```

Sai com código 1 se algum hash ou tamanho divergir do publicado pelo
fornecedor. Bom candidato a job semanal de CI.

```bash
python tools/update_catalog.py --write
```

## Problemas comuns

**GRUB não aparece / máquina ignora o pendrive** — Secure Boot precisa estar
desativado (ainda não suportado). Confirme também que o boot é UEFI, não legacy.

**GRUB abre mas não acha o kernel** — o label `PENSYS` não bateu.
`sudo blkid /dev/sdb2` deve mostrar `LABEL="PENSYS"`.

**Sobe em modo texto, sem Chromium** — `journalctl -u penlive-kiosk` e
`journalctl -u penlive-api`. Quase sempre é `manager/frontend/dist` ausente na
hora do build.

**Sistema baixado não boota** — quase sempre `cmdline` do adapter. Use a entrada
"Ignore pending boot" no menu Recovery, e compare o `cmdline` gerado com o
`/boot/grub/grub.cfg` de dentro da ISO em questão.
