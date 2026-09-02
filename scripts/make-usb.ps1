<#
.SYNOPSIS
    Writes a PenLive USB stick on Windows.

.DESCRIPTION
    The live system itself must be built on Debian/Ubuntu - live-build has no
    Windows equivalent, and pretending otherwise would just fail late. So this
    script covers the two things Windows can genuinely do:

      1. Flash an existing image (.img or .img.zst) onto a USB stick. This is
         the normal path: someone builds the image once on Linux or in CI, and
         everyone else flashes it.

      2. Drive the Linux build for you through WSL, when a Debian-family WSL
         distribution is installed (-Build).

    Same guarantees as the Linux script: the system disk and non-removable
    drives are refused, and the erase always needs the disk number typed back.

.PARAMETER Image
    Path to a .img or .img.zst produced by `make image` on Linux.

.PARAMETER DiskNumber
    Target disk number (from Get-Disk). Prompts interactively when omitted.

.PARAMETER Build
    Build the image first inside WSL, then flash it.

.PARAMETER NoVerify
    Skip the read-back verification pass (faster, less certain).

.EXAMPLE
    .\scripts\make-usb.ps1 -Image .\dist\penlive-amd64.img.zst

.EXAMPLE
    .\scripts\make-usb.ps1 -Build

.NOTES
    Must be run from an elevated PowerShell ("Run as Administrator").
#>
[CmdletBinding()]
param(
    [string] $Image,
    [int]    $DiskNumber = -1,
    [switch] $Build,
    [switch] $NoVerify
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogFile  = Join-Path $RepoRoot 'make-usb.log'

# ---------------------------------------------------------------- output ----

$script:Step = 0
$script:TotalSteps = 4

function Write-Log {
    param([string] $Message)
    $Message | Out-File -FilePath $LogFile -Append -Encoding utf8
}
function Say  { param([string] $m) Write-Host $m;                     Write-Log $m }
function Info { param([string] $m) Write-Host "==> $m" -Foreground Cyan;   Write-Log "==> $m" }
function Good { param([string] $m) Write-Host "  ok $m" -Foreground Green; Write-Log "  ok $m" }
function Warn { param([string] $m) Write-Host "  !  $m" -Foreground Yellow;Write-Log "  !  $m" }
function Fail {
    param([string] $m)
    Write-Host "error: $m" -Foreground Red
    Write-Log "error: $m"
    Write-Host ""
    Write-Host "Full log: $LogFile" -Foreground DarkGray
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

# -------------------------------------------------------------- preflight ----

"" | Out-File -FilePath $LogFile -Encoding utf8

Say ""
Write-Host "PenLive USB writer" -Foreground White
Write-Host "repository: $RepoRoot" -Foreground DarkGray

$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "must run elevated. Right-click PowerShell and choose 'Run as Administrator'."
}

# ------------------------------------------------------- 1. get an image ----

Step "Locating the image"

function Find-WslDistro {
    # `wsl -l -q` emits UTF-16 with embedded NULs when captured, so strip them
    # before matching or every comparison silently fails.
    $raw = & wsl.exe -l -q
    if ($LASTEXITCODE -ne 0) { return $null }
    # [string][char]0 selects String.Replace(string, string). The
    # (char, char) overload rejects an empty replacement, throws, and
    # leaves the list empty - silently reporting "no distro" even on a
    # machine that does have Debian installed.
    $names = ($raw -join "`n").Replace([string][char]0, '') -split "`r?`n" |
             ForEach-Object { $_.Trim() } |
             Where-Object { $_ -ne '' }

    # docker-desktop is a WSL distro but has no apt and no systemd; it cannot
    # run live-build, so treat it as absent rather than failing 30 minutes in.
    return $names | Where-Object { $_ -notmatch '^docker-desktop' } | Select-Object -First 1
}

if ($Build) {
    $distro = Find-WslDistro
    if (-not $distro) {
        Fail @"
-Build needs a Debian-family WSL distribution, and none was found.

Install one, then re-run:
    wsl --install -d Debian

Or build the image on a Linux machine and flash it here:
    make image                                  (on Linux)
    .\scripts\make-usb.ps1 -Image .\dist\penlive-amd64.img.zst
"@
    }
    Info "building inside WSL distribution '$distro' (20-40 minutes)"
    Warn "WSL cannot write to the USB stick directly, so this only produces the image."

    $wslRepo = & wsl.exe -d $distro -- wslpath -a "$RepoRoot"
    if ($LASTEXITCODE -ne 0) { Fail "could not translate $RepoRoot into a WSL path" }
    $wslRepo = $wslRepo.Trim()

    & wsl.exe -d $distro -u root -- bash -lc "cd '$wslRepo' && ./live/build.sh && python3 -m penlive.cli image dist/penlive-amd64.img --live-dir live/build/out --grub-cfg grub/grub.cfg --recovery-cfg grub/recovery.cfg --catalog catalog/catalog.json"
    if ($LASTEXITCODE -ne 0) { Fail "the WSL build failed - see the output above" }

    $Image = Join-Path $RepoRoot 'dist\penlive-amd64.img'
    Good "built $Image"
}

if (-not $Image) {
    $candidates = @()
    $distDir = Join-Path $RepoRoot 'dist'
    if (Test-Path $distDir) {
        $candidates = Get-ChildItem $distDir -File |
                      Where-Object { $_.Name -match '\.img(\.zst)?$' } |
                      Sort-Object LastWriteTime -Descending
    }
    if ($candidates.Count -eq 0) {
        Fail @"
No image given and none found in dist\.

Build one on a Debian/Ubuntu machine (or WSL):
    make image

then flash it here:
    .\scripts\make-usb.ps1 -Image .\dist\penlive-amd64.img.zst

Or let this script drive the WSL build for you:
    .\scripts\make-usb.ps1 -Build
"@
    }
    $Image = $candidates[0].FullName
    Info "using most recent image: $Image"
}

if (-not (Test-Path -LiteralPath $Image)) { Fail "no such file: $Image" }
$imageItem = Get-Item -LiteralPath $Image

# zstd images are decompressed to a temp file first: writing a raw disk from a
# streaming decompressor makes progress reporting and verification unreliable,
# and the temp copy is deleted afterwards.
$tempImage = $null
if ($imageItem.Extension -eq '.zst') {
    if (-not (Get-Command zstd.exe -ErrorAction SilentlyContinue)) {
        Fail @"
$($imageItem.Name) is zstd-compressed but zstd.exe is not on PATH.

Install it (winget install Facebook.Zstandard), or decompress manually and
pass the resulting .img.
"@
    }
    $tempImage = Join-Path $env:TEMP ("penlive-" + [Guid]::NewGuid().ToString('N') + ".img")
    Info "decompressing to $tempImage"
    & zstd.exe -d -f -o $tempImage $imageItem.FullName | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "zstd decompression failed" }
    $imageItem = Get-Item -LiteralPath $tempImage
}

$imageBytes = $imageItem.Length
Good ("image is {0:N1} GiB" -f ($imageBytes / 1GB))

# ------------------------------------------------------ 2. pick the disk ----

Step "Choosing the target USB stick"

try {
    $disks = Get-Disk | Sort-Object Number
} catch {
    Fail "could not enumerate disks: $($_.Exception.Message)"
}

if ($DiskNumber -lt 0) {
    Say ""
    "{0,-4} {1,-6} {2,10}  {3,-10} {4,-6} {5}" -f '#', 'DISK', 'SIZE', 'BUS', 'REM', 'MODEL' |
        ForEach-Object { Write-Host "     $_" }

    foreach ($d in $disks) {
        $marker = ''
        if ($d.IsSystem -or $d.IsBoot) {
            $marker = '<- SYSTEM DISK'
        } elseif ($d.BusType -ne 'USB') {
            $marker = '<- not removable'
        }
        $line = "{0,-4} {1,-6} {2,9:N1}G  {3,-10} {4,-6} {5} {6}" -f `
            "$($d.Number))", $d.Number, ($d.Size / 1GB), $d.BusType,
            $(if ($d.BusType -eq 'USB') { 'yes' } else { 'no' }),
            $d.FriendlyName, $marker
        if ($marker -ne '') {
            Write-Host "     $line" -Foreground Yellow
        } else {
            Write-Host "     $line"
        }
        Write-Log "     $line"
    }
    Say ""

    $answer = Read-Host "     Which disk number?"
    if (-not ($answer -match '^\d+$')) { Fail "invalid selection: $answer" }
    $DiskNumber = [int] $answer
}

$disk = $disks | Where-Object { $_.Number -eq $DiskNumber }
if (-not $disk) { Fail "no disk with number $DiskNumber" }

if ($disk.IsSystem -or $disk.IsBoot) {
    Fail "disk $DiskNumber is this machine's system disk. Refusing."
}
if ($disk.BusType -ne 'USB') {
    Warn "disk $DiskNumber is $($disk.BusType), not USB - this may be an internal drive"
    if (-not (Confirm-YesNo "Continue anyway?" $false)) { Fail "aborted" }
}
if ($disk.Size -lt $imageBytes) {
    Fail ("disk $DiskNumber holds {0:N1} GiB but the image needs {1:N1} GiB" -f ($disk.Size / 1GB), ($imageBytes / 1GB))
}

# --------------------------------------------------------------- 3. write ----

Step "Writing to disk $DiskNumber"

Say ""
Write-Host "     Everything on disk $DiskNumber will be erased." -Foreground Red
Say ""
Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue |
    Format-Table -AutoSize PartitionNumber, DriveLetter, @{n='Size(GB)';e={'{0:N1}' -f ($_.Size/1GB)}}, Type |
    Out-String | ForEach-Object { Write-Host $_ }
Say ""

Write-Host "     Type the disk number to confirm erasing it."
$typed = Read-Host "     disk $DiskNumber >"
if ($typed -ne "$DiskNumber") { Fail "confirmation did not match; nothing was written" }

Info "clearing existing partitions"
Clear-Disk -Number $DiskNumber -RemoveData -RemoveOEM -Confirm:$false
# Windows re-mounts volumes eagerly; without a beat the raw handle can still
# collide with a volume lock that was only just released.
Start-Sleep -Seconds 2

Info "writing image - do not remove the stick"

$devicePath = "\\.\PHYSICALDRIVE$DiskNumber"
$bufferSize = 4MB
$source = $null
$target = $null
try {
    $source = [System.IO.File]::OpenRead($imageItem.FullName)
    $target = New-Object System.IO.FileStream(
        $devicePath, [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)

    $buffer  = New-Object byte[] $bufferSize
    $written = 0L
    $sw = [Diagnostics.Stopwatch]::StartNew()

    while ($true) {
        $read = $source.Read($buffer, 0, $bufferSize)
        if ($read -le 0) { break }
        $target.Write($buffer, 0, $read)
        $written += $read

        $pct = [int](($written / $imageBytes) * 100)
        $mbps = 0
        if ($sw.Elapsed.TotalSeconds -gt 0) {
            $mbps = [int](($written / 1MB) / $sw.Elapsed.TotalSeconds)
        }
        Write-Progress -Activity "Writing PenLive to disk $DiskNumber" `
            -Status ("{0:N1} / {1:N1} GiB  -  {2} MB/s" -f ($written/1GB), ($imageBytes/1GB), $mbps) `
            -PercentComplete $pct
    }
    $target.Flush($true)
    Write-Progress -Activity "Writing PenLive to disk $DiskNumber" -Completed
    Good ("wrote {0:N1} GiB in {1:N0}s" -f ($written/1GB), $sw.Elapsed.TotalSeconds)
} catch {
    Fail "write failed: $($_.Exception.Message)"
} finally {
    if ($target) { $target.Dispose() }
    if ($source) { $source.Dispose() }
}

# -------------------------------------------------------------- 4. verify ----

Step "Verifying"

if ($NoVerify) {
    Warn "skipped (-NoVerify)"
} else {
    $source = $null
    $target = $null
    try {
        $source = [System.IO.File]::OpenRead($imageItem.FullName)
        $target = New-Object System.IO.FileStream(
            $devicePath, [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)

        $bufA = New-Object byte[] $bufferSize
        $bufB = New-Object byte[] $bufferSize
        $checked = 0L
        $mismatch = $false

        while ($checked -lt $imageBytes) {
            $want = [Math]::Min($bufferSize, $imageBytes - $checked)
            $a = $source.Read($bufA, 0, $want)
            if ($a -le 0) { break }

            # A raw device read can return a short count; keep asking until the
            # window is full, or the comparison would fail on alignment alone.
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
                -PercentComplete ([int](($checked / $imageBytes) * 100))
        }
        Write-Progress -Activity "Verifying disk $DiskNumber" -Completed

        if ($mismatch) {
            Fail "verification FAILED - the stick does not match the image. Do not boot it; write again."
        }
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

# ----------------------------------------------------------------- done ----

Say ""
Write-Host "PenLive is ready on disk $DiskNumber" -Foreground Green
Say ""
Say "  To boot it:"
Say "    1. Leave the stick plugged in and restart the machine"
Say "    2. Open the firmware boot menu (usually F12, F10, Esc or Del)"
Say "    3. Choose the USB device"
Say ""
Write-Host "  Secure Boot must be disabled - it is not supported yet." -Foreground Yellow
Say "  First boot asks for keyboard layout and Wi-Fi, then shows the catalog."
Say ""
Write-Host "  Windows may offer to format the stick - decline. It cannot read" -Foreground DarkGray
Write-Host "  the Linux partitions, but the PENDATA partition is exFAT and will" -Foreground DarkGray
Write-Host "  appear normally once PenLive has been booted at least once." -Foreground DarkGray
Say ""
Write-Host "  Log: $LogFile" -Foreground DarkGray
Say ""
