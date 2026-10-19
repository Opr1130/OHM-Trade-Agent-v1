#Requires -Version 5.1
<#
.SYNOPSIS
    HF-04 orchestrator: prepare -> execute a durable rollback on the host.
.DESCRIPTION
    Epoch and sequence are read from the DESTINATION DATABASE by
    scripts/v2/rollback-v2.sh; this wrapper never accepts owner-entered numbers.
    Rollback kinds are distinct:
      safe-baseline : release modes only (same code, same data)
      code          : previous certified IMAGE only (immutable image id required)
      data          : refused here - canonical recovery uses the native restore
                      path with a verified recovery receipt
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Prepare', 'Execute')][string]$Phase,
    [Parameter(Mandatory)][ValidateSet('safe-baseline', 'code', 'data')][string]$Kind,
    [Parameter(Mandatory)][string]$PreviousSha,
    [string]$PreviousImage,
    [switch]$Authorize,
    [string]$ConfirmSha,
    [string]$SshUser = 'opipadmin',
    [switch]$NoReceipt
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\OpipR1.Common.ps1')

$receipt = New-R1Receipt -Name 'Invoke-V2Rollback.ps1' -Mode "$Phase/$Kind"
$exit = 0

try {
    if ($SshUser -ne $script:OpipR1V2.SshUser) {
        Add-R1Check $receipt 'SSH_ACCOUNT' 'REFUSED' "use $($script:OpipR1V2.SshUser), not $SshUser"
        $null = Complete-R1Receipt $receipt 3 (Get-R1ReceiptPath 'v2-rollback.receipt.json'); exit 3
    }
    if ($PreviousSha -notmatch '^[0-9a-f]{40}$') {
        Add-R1Check $receipt 'PREVIOUS_SHA' 'REFUSED' 'PreviousSha must be a 40-character hex commit'
        $null = Complete-R1Receipt $receipt 3 (Get-R1ReceiptPath 'v2-rollback.receipt.json'); exit 3
    }
    if ($Kind -eq 'data') {
        Add-R1Check $receipt 'KIND_SEPARATION' 'REFUSED' `
            'data rollback is not performed by this tool: use Invoke-OpipDataMigration -Mode RestoreCanonical with a verified receipt'
        $null = Complete-R1Receipt $receipt 3 (Get-R1ReceiptPath 'v2-rollback.receipt.json'); exit 3
    }
    if ($Kind -eq 'code' -and -not $PreviousImage) {
        Add-R1Check $receipt 'IMMUTABLE_IMAGE' 'REFUSED' 'code rollback requires -PreviousImage (immutable image id)'
        $null = Complete-R1Receipt $receipt 3 (Get-R1ReceiptPath 'v2-rollback.receipt.json'); exit 3
    }
    Add-R1Check $receipt 'KIND_SEPARATION' 'PASS' "kind=$Kind (code, modes and data recovery are distinct)"

    if ($Phase -eq 'Execute' -and -not $Authorize) {
        Add-R1Check $receipt 'OWNER_AUTHORIZATION' 'REFUSED' '-Authorize is required to execute a rollback'
        $null = Complete-R1Receipt $receipt 3 (Get-R1ReceiptPath 'v2-rollback.receipt.json'); exit 3
    }
    if ($Phase -eq 'Execute' -and $ConfirmSha -ne $script:OpipR1CertifiedSha) {
        Add-R1Check $receipt 'CONFIRM_SHA' 'REFUSED' 'ConfirmSha must equal the certified SHA'
        $null = Complete-R1Receipt $receipt 3 (Get-R1ReceiptPath 'v2-rollback.receipt.json'); exit 3
    }

    $remoteArgs = "--mode $($Phase.ToLower()) --kind $Kind --previous-sha $PreviousSha"
    if ($PreviousImage) { $remoteArgs += " --previous-image $PreviousImage" }
    $steps = @(
        'sudo -n install -d -m 0700 /tmp/opip-v2',
        'sudo -n install -m 0700 /dev/stdin /tmp/opip-v2/rollback-v2.sh   # uploaded by this wrapper',
        "sudo -n bash /tmp/opip-v2/rollback-v2.sh $remoteArgs"
    )
    $receipt.notes += "REMOTE_STEPS ($Phase/$Kind):"
    foreach ($step in $steps) { $receipt.notes += $step }
    $receipt.notes += 'EPOCH_SOURCE: destination database read on the host (never owner-entered)'

    Add-R1Check $receipt 'REMOTE_EXECUTION' 'INSUFFICIENT' `
        'PENDING LIVE VALIDATION: this wrapper prints the authorised remote sequence; it never opens a remote shell'
    if ($exit -eq 0) { $exit = 2 }
}
catch {
    Add-R1Check $receipt 'UNHANDLED' 'FAIL' $_.Exception.Message
    $exit = 1
}

if ($NoReceipt) { $null = Complete-R1Receipt $receipt $exit }
else { $null = Complete-R1Receipt $receipt $exit (Get-R1ReceiptPath 'v2-rollback.receipt.json') }
exit $exit
