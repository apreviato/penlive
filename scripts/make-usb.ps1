<#
.SYNOPSIS
    Builds and writes a PenLive USB stick on Windows, end to end.

.DESCRIPTION
    Run it with no arguments and it does everything: installs what is missing,
    builds the live system, and writes the stick.

    live-build only runs on Debian, so the Linux half happens inside WSL. Two
    routes, tried in this order:

      1. Disk passthrough (preferred). `wsl --mount --bare` hands the raw USB
         to WSL, and the same make-usb.sh that Linux users run partitions and
         writes it. The whole stick is used, and there is exactly one tested
         code path rather than a Windows reimplementation.

      2. Image fallback. Where passthrough is unavailable, WSL builds a fixed
         size .img and this script writes it from Windows. Simpler, but the
         data partition is capped at the image size instead of filling the
         stick.

    The build happens inside the WSL filesystem, never on /mnt/c or /mnt/d:
    debootstrap needs device nodes and real ownership, which DrvFs cannot
    provide, so a chroot built there fails partway through.

.PARAMETER Image
    Flash this existing .img/.img.zst instead of building anything.

.PARAMETER DiskNumber
    Target disk number (from Get-Disk). Prompts interactively when omitted.

.PARAMETER Distro
    WSL distribution to build in. Defaults to the first Debian-family one.

.PARAMETER ForceImageMode
    Skip disk passthrough and use the image route.

.PARAMETER SkipBuild
    Reuse the live system already built inside WSL.

.PARAMETER ImageSizeMib
    Image size for the fallback route. Defaults to the full size of the target
    stick, so the data partition uses the whole device. Must exceed the fixed
    partitions (12800 MiB) plus a 4096 MiB minimum data partition.

.PARAMETER FullWrite
    Write every byte, including all-zero regions. Slower (a 57 GiB image takes
    half an hour rather than a couple of minutes) but overwrites any data
    previously on the stick.

.PARAMETER NoVerify
    Skip the read-back verification after an image write.

.PARAMETER VerifyOnly
    Compare a stick that was already written against an image, without
    erasing or rewriting anything. Requires -Image.

.EXAMPLE
    .\scripts\make-usb.ps1

.EXAMPLE
    .\scripts\make-usb.ps1 -Image .\dist\penlive-amd64.img.zst

.NOTES
    Must be run from an elevated PowerShell ("Run as Administrator").
#>
[CmdletBinding()]
param(
    [string] $Image,
    [int]    $DiskNumber = -1,
    [string] $Distro,
    [switch] $ForceImageMode,
    [switch] $SkipBuild,
    [int]    $ImageSizeMib = 0,
    [switch] $FullWrite,
    [switch] $NoVerify,
    [switch] $VerifyOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogFile  = Join-Path $RepoRoot 'make-usb.log'
$WslWork  = '/root/penlive-build'

$script:Step = 0
# Recomputed once the route is known: disk passthrough does the build and the
# write in one step, whereas the image fallback adds a separate build, write
# and verify. Guessing a single number up front produced "[6/5]".
$script:TotalSteps = 4

# ---------------------------------------------------------------- output ----

function Write-Log { param([string] $m) $m | Out-File -FilePath $LogFile -Append -Encoding utf8 }
function Say  { param([string] $m) Write-Host $m;                            Write-Log $m }
function Info { param([string] $m) Write-Host "==> $m" -Foreground Cyan;     Write-Log "==> $m" }
function Good { param([string] $m) Write-Host "  ok $m" -Foreground Green;   Write-Log "  ok $m" }
function Warn { param([string] $m) Write-Host "  !  $m" -Foreground Yellow;  Write-Log "  !  $m" }
function Note { param([string] $m) Write-Host "     $m" -Foreground DarkGray;Write-Log "     $m" }
function Fail {
    param([string] $m)
    Write-Host "error: $m" -Foreground Red
    Write-Log "error: $m"
    Write-Host ""
    Write-Host "Full log: $LogFile" -Foreground DarkGray
    Invoke-Cleanup
    exit 1
}
function Step {
    param([string] $m)
    $script:Step++
    Say ""
    Write-Host "[$script:Step/$script:TotalSteps] $m" -Foreground White
    Write-Host ("-" * 66) -Foreground DarkGray
}
function Confirm-YesNo {
    param([string] $Question, [bool] $DefaultYes = $true)
    if ($DefaultYes) { $hint = "[Y/n]" } else { $hint = "[y/N]" }
    $reply = Read-Host "     $Question $hint"
    if ([string]::IsNullOrWhiteSpace($reply)) { return $DefaultYes }
    return ($reply.ToLower() -in @('y', 'yes'))
}

# Native tools emit UTF-16 with embedded NULs when captured. [string][char]0
# picks String.Replace(string,string); the (char,char) overload rejects an
# empty replacement and throws.
function Clean-Output { param($Raw) return ($Raw -join "`n").Replace([string][char]0, '') }

$script:MountedDisk = $null
function Invoke-Cleanup {
    if ($script:MountedDisk) {
        Write-Host "     releasing $($script:MountedDisk) from WSL" -Foreground DarkGray
        & wsl.exe --unmount $script:MountedDisk 2>$null | Out-Null
        $script:MountedDisk = $null
    }
}
trap { Invoke-Cleanup }

# -------------------------------------------------------------- preflight ----

"" | Out-File -FilePath $LogFile -Encoding utf8
Say ""
Write-Host "PenLive USB builder" -Foreground White
Write-Host "repository: $RepoRoot" -Foreground DarkGray

$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "must run elevated. Right-click PowerShell and choose 'Run as Administrator'."
}

if ($VerifyOnly -and -not $Image) {
    Fail "-VerifyOnly needs -Image, since there is nothing to compare the stick against."
}

$FlashOnly = [bool] $Image
if ($FlashOnly)  { $script:TotalSteps = 3 }
if ($VerifyOnly) { $script:TotalSteps = 2 }

# --------------------------------------------------------- 1. WSL + distro ----

function Get-WslDistros {
    $raw = & wsl.exe -l -q
    if ($LASTEXITCODE -ne 0) { return @() }
    return (Clean-Output $raw) -split "`r?`n" |
           ForEach-Object { $_.Trim() } |
           Where-Object { $_ -ne '' }
}

function Get-BuildDistro {
    # docker-desktop is a WSL distro with no apt and no systemd. Excluded here
    # rather than discovered 30 minutes into a build that cannot work.
    return (Get-WslDistros) | Where-Object { $_ -notmatch '^docker-desktop' } | Select-Object -First 1
}

if (-not $FlashOnly) {
    Step "Preparing the Linux build environment (WSL)"

    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        Fail @"
WSL is not installed. Install it, reboot, then run this script again:

    wsl --install -d Debian
"@
    }

    if (-not $Distro) { $Distro = Get-BuildDistro }

    if (-not $Distro) {
        Warn "no Debian-family WSL distribution found"
        Note "PenLive's live system can only be built on Debian or a derivative."
        Note "Debian will be installed into WSL (a few hundred MB download)."
        if (-not (Confirm-YesNo "Install Debian into WSL now?")) {
            Fail "cannot build without a Debian WSL distribution"
        }

        Info "installing Debian into WSL (this takes a few minutes)"
        # --no-launch avoids the interactive first-run account setup; the build
        # runs as root, so no user account is needed at all.
        & wsl.exe --install -d Debian --no-launch
        if ($LASTEXITCODE -ne 0) {
            Fail @"
Installing Debian failed (exit $LASTEXITCODE).

If WSL itself was just enabled, Windows needs a reboot before distributions
can be installed. Reboot, then run this script again.
"@
        }
        $Distro = Get-BuildDistro
        if (-not $Distro) {
            Fail "Debian was installed but is not listed yet. Reboot and run this script again."
        }
        Good "installed $Distro"
    } else {
        Good "using WSL distribution '$Distro'"
    }

    # A distro can be registered but fail to start (WSL not fully enabled yet).
    & wsl.exe -d $Distro -u root -- true 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Fail @"
WSL distribution '$Distro' is registered but will not start.

This usually means Windows still needs a reboot after enabling WSL. Reboot,
then run this script again.
"@
    }
    Good "'$Distro' starts and runs as root"
}

# -------------------------------------------------- 2. sync repo into WSL ----

function Invoke-Wsl {
    param([string] $Command, [switch] $Stream)
    if ($Stream) {
        & wsl.exe -d $Distro -u root -- bash -lc $Command
    } else {
        $out = & wsl.exe -d $Distro -u root -- bash -lc $Command
        return (Clean-Output $out).Trim()
    }
}

if (-not $FlashOnly) {
    Step "Copying the project into WSL"

    $wslRepo = Invoke-Wsl "wslpath -a '$RepoRoot'"
    if ($LASTEXITCODE -ne 0 -or -not $wslRepo) { Fail "could not translate $RepoRoot to a WSL path" }
    Note "source: $wslRepo"
    Note "build : $WslWork"

    # Copied into the distro's own ext4 filesystem on purpose: live-build runs
    # debootstrap, which creates device nodes and sets ownership that DrvFs
    # (/mnt/d) cannot represent, so building in place would fail partway.
    Info "syncing (excluding node_modules, build output and .git)"
    $sync = @"
set -e
mkdir -p '$WslWork'
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude 'node_modules' --exclude 'dist' --exclude '.git' \
    --exclude 'live/build' --exclude 'devdata' \
    '$wslRepo'/ '$WslWork'/
else
  tar -C '$wslRepo' \
      --exclude=node_modules --exclude=dist --exclude=.git \
      --exclude=live/build --exclude=devdata \
      -cf - . | tar -C '$WslWork' -xf -
fi
chmod +x '$WslWork'/scripts/*.sh '$WslWork'/live/build.sh '$WslWork'/live/auto/config 2>/dev/null || true
chmod +x '$WslWork'/live/config/hooks/live/*.hook.chroot 2>/dev/null || true
"@
    Invoke-Wsl $sync -Stream
    if ($LASTEXITCODE -ne 0) { Fail "could not copy the project into WSL" }
    Good "synced"
}

# ------------------------------------------------------ 3. pick the disk ----

Step "Choosing the target USB stick"

try { $disks = Get-Disk | Sort-Object Number } catch { Fail "could not enumerate disks: $($_.Exception.Message)" }

if ($DiskNumber -lt 0) {
    Say ""
    Write-Host ("     {0,-4} {1,10}  {2,-8} {3,-5} {4}" -f '#', 'SIZE', 'BUS', 'REM', 'MODEL')
    foreach ($d in $disks) {
        $marker = ''
        if ($d.IsSystem -or $d.IsBoot) { $marker = '<- SYSTEM DISK' }
        elseif ($d.BusType -ne 'USB')  { $marker = '<- not removable' }
        $rem = 'no'; if ($d.BusType -eq 'USB') { $rem = 'yes' }
        $line = "     {0,-4} {1,9:N1}G  {2,-8} {3,-5} {4} {5}" -f `
            "$($d.Number))", ($d.Size / 1GB), $d.BusType, $rem, $d.FriendlyName, $marker
        if ($marker) { Write-Host $line -Foreground Yellow } else { Write-Host $line }
        Write-Log $line
    }
    Say ""
    $answer = Read-Host "     Which disk number?"
    if (-not ($answer -match '^\d+$')) { Fail "invalid selection: $answer" }
    $DiskNumber = [int] $answer
}

$disk = $disks | Where-Object { $_.Number -eq $DiskNumber }
if (-not $disk) { Fail "no disk with number $DiskNumber" }
if ($disk.IsSystem -or $disk.IsBoot) { Fail "disk $DiskNumber is this machine's system disk. Refusing." }
if ($disk.BusType -ne 'USB') {
    Warn "disk $DiskNumber is $($disk.BusType), not USB - this may be an internal drive"
    if (-not (Confirm-YesNo "Continue anyway?" $false)) { Fail "aborted" }
}

if (-not $VerifyOnly) {
    Say ""
    Write-Host "     Everything on disk $DiskNumber ($($disk.FriendlyName)) will be erased." -Foreground Red
    Say ""
    Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue |
        Format-Table -AutoSize PartitionNumber, DriveLetter,
            @{n='Size(GB)';e={'{0:N1}' -f ($_.Size/1GB)}}, Type |
        Out-String | ForEach-Object { Write-Host $_ }

    Write-Host "     Type the disk number to confirm erasing it."
    $typed = Read-Host "     disk $DiskNumber >"
    if ($typed -ne "$DiskNumber") { Fail "confirmation did not match; nothing was written" }
} else {
    Info "verify only: the stick will be read, never written"
}

$devicePath = "\\.\PHYSICALDRIVE$DiskNumber"

# ------------------------------------------- 4. build + write via WSL ----

$usedPassthrough = $false

if (-not $FlashOnly -and -not $ForceImageMode) {
    Step "Building and writing through WSL"

    Info "handing disk $DiskNumber to WSL"
    # Volumes must be gone before WSL can claim the whole disk.
    Clear-Disk -Number $DiskNumber -RemoveData -RemoveOEM -Confirm:$false -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    $before = (Invoke-Wsl "lsblk -dno NAME | sort") -split "`r?`n" | ForEach-Object { $_.Trim() }

    & wsl.exe --mount $devicePath --bare
    if ($LASTEXITCODE -ne 0) {
        Warn "disk passthrough unavailable (exit $LASTEXITCODE); falling back to the image route"
    } else {
        $script:MountedDisk = $devicePath
        Start-Sleep -Seconds 2

        $after = (Invoke-Wsl "lsblk -dno NAME | sort") -split "`r?`n" | ForEach-Object { $_.Trim() }
        # Identify by difference rather than guessing /dev/sdb: WSL assigns the
        # next free letter, and guessing wrong here means writing to the wrong
        # disk inside the VM.
        # @(...) forces array semantics: a single match would otherwise be a
        # bare string, whose .Count behaves differently across PowerShell
        # versions, and getting this wrong means writing to the wrong disk.
        $new = @(Compare-Object -ReferenceObject $before -DifferenceObject $after |
                 Where-Object { $_.SideIndicator -eq '=>' } |
                 Select-Object -ExpandProperty InputObject)

        if ($new.Count -ne 1) {
            Warn "could not identify the disk inside WSL (found: $($new -join ', ')); falling back to the image route"
            Invoke-Cleanup
        } else {
            $wslDevice = "/dev/$($new[0])"
            Good "disk $DiskNumber is $wslDevice inside WSL"

            $skipFlag = ''
            if ($SkipBuild) { $skipFlag = '--skip-build' }

            Info "running the Linux installer inside WSL (20-40 minutes on first run)"
            Note "installs build dependencies, builds the UI and live system, then writes the stick"
            Say ""

            # --assume-confirmed is safe here precisely because this script has
            # already taken an explicit typed confirmation for this same disk.
            Invoke-Wsl "cd '$WslWork' && ./scripts/make-usb.sh --device '$wslDevice' --yes --assume-confirmed $skipFlag" -Stream
            $rc = $LASTEXITCODE

            Invoke-Cleanup
            if ($rc -ne 0) { Fail "the WSL build/write failed (exit $rc) - see the output above" }

            $usedPassthrough = $true
            Good "stick written using the full capacity of the device"
        }
    }
}

# ------------------------------------------------ 4b. image route ----

if (-not $usedPassthrough) {
    # From here the route is settled: an image build (unless one was handed to
    # us), then a write, then a verify.
    $remaining = 3
    if ($FlashOnly) { $remaining = 2 }
    $script:TotalSteps = $script:Step + $remaining

    if (-not $FlashOnly) {
        Step "Building an image inside WSL"

        # Size the image to the stick so PENDATA uses the whole device. This is
        # only affordable because the writer skips zero blocks: a 57 GiB image
        # still carries only ~1.3 GiB of real data.
        if ($ImageSizeMib -le 0) {
            $ImageSizeMib = [int][Math]::Floor($disk.Size / 1MB)
            Info ("sizing the image to the stick: {0} MiB ({1:N1} GiB)" -f $ImageSizeMib, ($disk.Size/1GB))
            if (($disk.Size % 1MB) -ne 0) {
                # The backup GPT belongs on the last sector. A stick that is not
                # a whole number of MiB leaves a sliver past the image, which
                # penlive-expand fixes on first boot.
                Note "stick is not a whole number of MiB; the last partial MiB stays unused"
            }
        }

        $skipFlag = ''
        if ($SkipBuild) { $skipFlag = '--skip-build' }

        Invoke-Wsl "cd '$WslWork' && ./scripts/make-usb.sh --build-only --yes $skipFlag --image-size-mib $ImageSizeMib --image-out '$WslWork/dist/penlive-amd64.img'" -Stream
        if ($LASTEXITCODE -ne 0) { Fail "the WSL image build failed - see the output above" }

        New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot 'dist') | Out-Null
        Info "copying the image out of WSL"
        # $wslRepo was already translated above; reusing it avoids a second
        # wslpath round-trip and any ambiguity over mixed path separators.
        Invoke-Wsl "mkdir -p '$wslRepo/dist' && cp '$WslWork/dist/penlive-amd64.img' '$wslRepo/dist/'" -Stream
        if ($LASTEXITCODE -ne 0) { Fail "could not copy the image out of WSL" }

        $Image = Join-Path $RepoRoot 'dist\penlive-amd64.img'
        Good "image ready at $Image"
    }

    if (-not (Test-Path -LiteralPath $Image)) { Fail "no such file: $Image" }
    $imageItem = Get-Item -LiteralPath $Image

    # Never write an image that is not demonstrably a PenLive image. `truncate`
    # creates the file up front, so a build that dies partway leaves a
    # plausible-looking 20 GiB of zeroes behind; flashing that silently wipes
    # the stick and produces something that cannot boot. Check for the GPT
    # signature and the PENSYS/PENDATA partition names in the header.
    if ($imageItem.Extension -ne '.zst') {
        $head = New-Object byte[] 262144
        $fs = [System.IO.File]::OpenRead($imageItem.FullName)
        try { $read = $fs.Read($head, 0, $head.Length) } finally { $fs.Dispose() }

        $ascii = -join ($head[0..([Math]::Min($read, $head.Length) - 1)] |
                        ForEach-Object { if ($_ -ge 32 -and $_ -lt 127) { [char]$_ } else { '.' } })
        $utf16 = [System.Text.Encoding]::Unicode.GetString($head, 0, [Math]::Min($read, $head.Length))

        if ($ascii -notmatch 'EFI PART') {
            Fail "$($imageItem.Name) has no GPT header - the build did not complete. Nothing was written."
        }
        # GPT stores partition names as UTF-16LE.
        if ($utf16 -notmatch 'PENSYS') {
            Fail "$($imageItem.Name) has no PENSYS partition - the build did not complete. Nothing was written."
        }
        Good "image verified as a PenLive layout"
    }

    $tempImage = $null
    if ($imageItem.Extension -eq '.zst') {
        if (-not (Get-Command zstd.exe -ErrorAction SilentlyContinue)) {
            Fail "$($imageItem.Name) is zstd-compressed but zstd.exe is not on PATH (winget install Facebook.Zstandard)"
        }
        $tempImage = Join-Path $env:TEMP ("penlive-" + [Guid]::NewGuid().ToString('N') + ".img")
        Info "decompressing to $tempImage"
        & zstd.exe -d -f -o $tempImage $imageItem.FullName | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "zstd decompression failed" }
        $imageItem = Get-Item -LiteralPath $tempImage
    }

    $imageBytes = $imageItem.Length
    if ($disk.Size -lt $imageBytes) {
        Fail ("disk $DiskNumber holds {0:N1} GiB but the image needs {1:N1} GiB" -f ($disk.Size/1GB), ($imageBytes/1GB))
    }

    if ($VerifyOnly) {
        # $bufferSize is set inside the write block, which is skipped here.
        $bufferSize = 4MB
    } else {

    Step "Writing disk $DiskNumber"
    Info "clearing existing partitions"
    Clear-Disk -Number $DiskNumber -RemoveData -RemoveOEM -Confirm:$false -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    Info "writing - do not remove the stick"
    if (-not $FullWrite) {
        Note "all-zero blocks are skipped; only the ~1-2 GiB that carries data is written"
    }
    $bufferSize = 4MB
    $source = $null; $target = $null
    try {
        $source = [System.IO.File]::OpenRead($imageItem.FullName)
        $target = New-Object System.IO.FileStream($devicePath,
            [System.IO.FileMode]::Open, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $buffer = New-Object byte[] $bufferSize
        $zero = New-Object byte[] $bufferSize
        $processed = 0L
        $written = 0L
        $sw = [Diagnostics.Stopwatch]::StartNew()
        while ($true) {
            $read = $source.Read($buffer, 0, $bufferSize)
            if ($read -le 0) { break }

            # The image is mostly empty filesystem: a 20 GiB image carries
            # about 1.3 GiB of real data. Writing the zeroes back costs ten
            # minutes on a USB stick and changes nothing - the partition
            # metadata that matters is non-zero and is written, and free space
            # is free space. Clear-Disk above has already removed the old
            # partition table, so nothing stale is left claiming the device.
            $isZero = $false
            if (-not $FullWrite -and $read -eq $bufferSize) {
                # Compare against a zero buffer in one native call rather than
                # looping in PowerShell, which would be slower than the write.
                $isZero = [System.Linq.Enumerable]::SequenceEqual([byte[]]$buffer, [byte[]]$zero)
            }

            if ($isZero) {
                $target.Seek($read, [System.IO.SeekOrigin]::Current) | Out-Null
            } else {
                $target.Write($buffer, 0, $read)
                $written += $read
            }
            $processed += $read

            $mbps = 0
            if ($sw.Elapsed.TotalSeconds -gt 0) { $mbps = [int](($written/1MB)/$sw.Elapsed.TotalSeconds) }
            Write-Progress -Activity "Writing PenLive to disk $DiskNumber" `
                -Status ("{0:N1} / {1:N1} GiB scanned - {2:N2} GiB written - {3} MB/s" -f `
                    ($processed/1GB), ($imageBytes/1GB), ($written/1GB), $mbps) `
                -PercentComplete ([int](($processed/$imageBytes)*100))
        }
        # No SetLength here: a physical drive has a fixed size and rejects it.
        # Seeking over trailing zeroes simply leaves those sectors untouched,
        # which is the intent - the GPT backup header at the very end is
        # non-zero and is therefore always written.
        $target.Flush($true)
        Write-Progress -Activity "Writing PenLive to disk $DiskNumber" -Completed
        Good ("wrote {0:N2} GiB of {1:N1} GiB in {2:N0}s ({3:N1} GiB of zeroes skipped)" -f `
            ($written/1GB), ($imageBytes/1GB), $sw.Elapsed.TotalSeconds, (($processed-$written)/1GB))
    } catch {
        Fail "write failed: $($_.Exception.Message)"
    } finally {
        if ($target) { $target.Dispose() }
        if ($source) { $source.Dispose() }
    }

    }  # end of the write block, skipped under -VerifyOnly

    Step "Verifying"
    if ($NoVerify) {
        Warn "skipped (-NoVerify)"
    } else {
        $source = $null; $target = $null
        try {
            $source = [System.IO.File]::OpenRead($imageItem.FullName)
            $target = New-Object System.IO.FileStream($devicePath,
                [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
            $bufA = New-Object byte[] $bufferSize
            $bufB = New-Object byte[] $bufferSize
            $zeroBuf = New-Object byte[] $bufferSize
            $checked = 0L; $mismatch = $false
            while ($checked -lt $imageBytes) {
                # [int64] on both arguments picks Math::Min(Int64, Int64).
                # Without it PowerShell selects the Int32 overload from
                # $bufferSize and then fails converting the remaining byte
                # count, so verification died on the first block of any image
                # larger than 2 GiB. The result always fits an Int32 because it
                # is capped at $bufferSize, which Read() requires.
                $want = [int][Math]::Min([int64]$bufferSize, $imageBytes - $checked)
                $a = $source.Read($bufA, 0, $want)
                if ($a -le 0) { break }

                # Blocks that were skipped on write were never sent to the
                # device, so reading them back proves nothing. Verifying them
                # would also mean reading the entire 57 GiB, which costs as
                # much as the write we just avoided.
                if (-not $FullWrite -and $a -eq $bufferSize -and
                    [System.Linq.Enumerable]::SequenceEqual([byte[]]$bufA, [byte[]]$zeroBuf)) {
                    $target.Seek($a, [System.IO.SeekOrigin]::Current) | Out-Null
                    $checked += $a
                    continue
                }
                # Raw device reads can come back short; refill before comparing
                # or the mismatch would be alignment, not corruption.
                $b = 0
                while ($b -lt $a) {
                    $n = $target.Read($bufB, $b, $a - $b)
                    if ($n -le 0) { break }
                    $b += $n
                }
                if ($b -ne $a) { $mismatch = $true; break }
                for ($i = 0; $i -lt $a; $i++) {
                    if ($bufA[$i] -ne $bufB[$i]) { $mismatch = $true; break }
                }
                if ($mismatch) { break }
                $checked += $a
                Write-Progress -Activity "Verifying disk $DiskNumber" `
                    -Status ("{0:N1} / {1:N1} GiB" -f ($checked/1GB), ($imageBytes/1GB)) `
                    -PercentComplete ([int](($checked/$imageBytes)*100))
            }
            Write-Progress -Activity "Verifying disk $DiskNumber" -Completed
            if ($mismatch) { Fail "verification FAILED - do not boot this stick; write it again" }
            Good ("verified {0:N1} GiB" -f ($checked/1GB))
        } catch {
            Fail "verification error: $($_.Exception.Message)"
        } finally {
            if ($target) { $target.Dispose() }
            if ($source) { $source.Dispose() }
        }
    }

    if ($tempImage -and (Test-Path -LiteralPath $tempImage)) {
        Remove-Item -LiteralPath $tempImage -Force -ErrorAction SilentlyContinue
    }
}

# ----------------------------------------------------------------- done ----

Invoke-Cleanup

Say ""
Write-Host "PenLive is ready on disk $DiskNumber" -Foreground Green
Say ""
Say "  To boot it:"
Say "    1. Leave the stick plugged in and restart the machine"
Say "    2. Open the firmware boot menu (usually F12, F10, Esc or Del)"
Say "    3. Choose the USB device"
Say ""
Write-Host "  Secure Boot must be disabled." -Foreground Yellow
Say "  PenLive builds its own GRUB, so it carries no signature Secure Boot"
Say "  accepts. With it on the firmware skips the stick silently - you pick it"
Say "  in the boot menu and the machine just moves on. Disable Secure Boot in"
Say "  firmware setup (under Security or Boot), and confirm CSM/Legacy is off."
Say "  First boot asks for keyboard layout and Wi-Fi, then shows the catalog."
Say ""
if (-not $usedPassthrough -and -not $FlashOnly) {
    Note "This used the image route, so the data partition is capped at ${ImageSizeMib} MiB."
    Note "Re-running without -ForceImageMode uses the whole stick when passthrough works."
    Say ""
}
Note "Windows may offer to format the stick - decline. It cannot read the Linux"
Note "partitions, but PENDATA is exFAT and appears normally after the first boot."
Say ""
Write-Host "  Log: $LogFile" -Foreground DarkGray
Say ""
