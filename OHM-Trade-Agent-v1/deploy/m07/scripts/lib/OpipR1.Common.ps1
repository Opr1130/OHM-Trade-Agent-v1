#Requires -Version 5.1
<#
.SYNOPSIS  R1 shared helpers: receipts, exit codes, python/ssh invocation.
.NOTES     Exit codes: 0 PASS, 1 FAIL, 2 INSUFFICIENT, 3 REFUSED, 64 USAGE, 77 DRYRUN.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:OpipR1CertifiedSha = 'c196dd674678b3b1ee91c351ee97c1c9e2b3938d'
$script:OpipR1V1 = [ordered]@{ DropletId = '589794133'; Ip = '161.35.106.207' }
$script:OpipR1V2 = [ordered]@{
    DropletId = '610307364'; Name = 'OPip-Trade-AI'; Ip = '178.128.147.125'
    Hostname = '178-128-147-125.sslip.io'; SshUser = 'opipadmin'
    AppRoot = '/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1'
    StateDir = '/var/lib/ohm-deploy'
    ImageTag = 'opip-v2:certified-c196dd67'
}
$script:OpipR1Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:OpipR1Scripts = Join-Path $script:OpipR1Root 'scripts'
$script:OpipR1Evidence = Join-Path $script:OpipR1Root 'evidence'

function New-R1Receipt {
    param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string]$Mode)
    return [ordered]@{
        receipt_schema = 'r1-1'; script = $Name; mode = $Mode; mission = 'MISSION-07-R1'
        certified_sha = $script:OpipR1CertifiedSha
        started_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        finished_at_utc = $null; status = 'RUNNING'; exit_code = $null
        checks = @(); notes = @(); artifacts = @(); mutated = $false
    }
}

function Add-R1Check {
    param($Receipt, [string]$Id, [ValidateSet('PASS', 'FAIL', 'SKIP', 'INSUFFICIENT', 'REFUSED')][string]$Status, [string]$Detail = '')
    $Receipt.checks += [ordered]@{ id = $Id; status = $Status; detail = $Detail }
}

function Complete-R1Receipt {
    param($Receipt, [int]$ExitCode, [string]$Path)
    $Receipt.exit_code = $ExitCode
    $Receipt.status = switch ($ExitCode) {
        0 { 'PASS' } 1 { 'FAIL' } 2 { 'INSUFFICIENT_EVIDENCE' } 3 { 'REFUSED' } 64 { 'USAGE' } 77 { 'DRYRUN' }
        default { 'FAIL' }
    }
    $Receipt.finished_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    $json = $Receipt | ConvertTo-Json -Depth 12
    if ($Path) {
        $dir = Split-Path -Parent $Path
        if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        Set-Content -LiteralPath $Path -Value $json -Encoding UTF8
        Write-Host "receipt: $Path"
    }
    Write-Host ("RESULT={0} EXIT={1}" -f $Receipt.status, $ExitCode)
    return $ExitCode
}

function Get-R1ReceiptPath {
    param([string]$Name)
    $dir = Join-Path $script:OpipR1Evidence 'receipts'
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    return (Join-Path $dir $Name)
}

function Invoke-R1Python {
    <# Runs an R1 python helper and returns @{ code; json; raw }. #>
    param([Parameter(Mandatory)][string[]]$Arguments, [string]$EvidenceJson)
    $python = (Get-Command python -ErrorAction Stop).Source
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONPATH = $script:OpipR1Scripts
    $raw = & $python @Arguments 2>&1
    $code = $LASTEXITCODE
    $json = $null
    if ($EvidenceJson -and (Test-Path -LiteralPath $EvidenceJson)) {
        try { $json = Get-Content -LiteralPath $EvidenceJson -Raw | ConvertFrom-Json } catch { $json = $null }
    }
    return @{ code = $code; json = $json; raw = ($raw -join "`n") }
}

function Invoke-R1SshProbe {
    <#
      Read-only remote probe as the approved account.
      Strict known-host verification: the host key must already be present in
      known_hosts. An unknown key fails the probe (never auto-accepted).
      Each command must exit 0 and produce non-empty evidence.
    #>
    param(
        [Parameter(Mandatory)][string]$Command,
        [string]$User = $script:OpipR1V2.SshUser,
        [int]$TimeoutSeconds = 25
    )
    $entry = [ordered]@{ user = $User; command = $Command; exit_code = $null; evidence = $null; stderr = $null }
    try {
        $out = & ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o "ConnectTimeout=$TimeoutSeconds" `
            "$User@$($script:OpipR1V2.Ip)" $Command 2>&1
        $entry.exit_code = $LASTEXITCODE
        $text = ($out | Where-Object { $_ -ne $null }) -join "`n"
        if ($LASTEXITCODE -eq 0 -and $text.Trim()) { $entry.evidence = $text.Trim() }
        else { $entry.stderr = $text.Trim() }
    }
    catch {
        $entry.exit_code = 255
        $entry.stderr = $_.Exception.Message
    }
    return $entry
}

function Join-OpipPosixPath {
    <#
      Join a POSIX root and child without ever producing Windows separators.
      The host contract is POSIX-only, so `Join-Path` must not be used for a
      remote path even when the operator runs this wrapper on Windows.
    #>
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Child
    )
    $cleanRoot = $Root.Replace('\', '/').TrimEnd('/')
    $cleanChild = $Child.Replace('\', '/').TrimStart('/')
    if (-not $cleanRoot.StartsWith('/')) {
        throw "remote root must be an absolute POSIX path: $Root"
    }
    # Single quotes are valid path characters; shell safety is the quoting
    # helper's job. Newlines, NULs, traversal and empty segments are refused.
    foreach ($value in @($cleanRoot, $cleanChild)) {
        if ($value -match "[`r`n]" -or $value.IndexOf([char]0) -ge 0) {
            throw 'remote path contains a newline or NUL'
        }
        $trimmed = $value.Trim('/')
        if ($trimmed -eq '') { continue }
        foreach ($segment in $trimmed.Split('/')) {
            if ($segment -eq '' -or $segment -eq '.' -or $segment -eq '..') {
                throw "remote path contains an unsafe segment: $value"
            }
        }
    }
    if ($cleanChild -eq '') { return $cleanRoot }
    return "$cleanRoot/$cleanChild"
}

function ConvertTo-OpipPosixArgument {
    <#
      Quote one POSIX shell argument. Single quotes preserve every byte of a
      path (including spaces); an embedded single quote is emitted as '\''.
      Newlines and NULs are refused rather than silently mangled.
    #>
    param([Parameter(Mandatory)][string]$Value)
    if ($Value -match "[`r`n`0]") {
        throw 'remote argument contains a newline or NUL'
    }
    return "'" + ($Value -replace "'", "'\''") + "'"
}

Write-Verbose 'OpipR1.Common loaded'

