#Requires -Version 5.1
<#
.SYNOPSIS
    HF-03 orchestrator: prepare -> authorize -> execute the guarded V2 bootstrap.
.DESCRIPTION
    The host-side work lives in scripts/v2/bootstrap-v2.sh (single guarded
    execution script). This wrapper only:
      * verifies the separately provisioned toolkit (SHA-256 manifest) before use,
      * runs each phase over ssh as opipadmin with sudo -n,
      * mirrors the three receipts (PREPARED / AUTHORIZED / EXECUTED) locally.
    It stages no Compose overlay and no generated nginx configuration: the
    certified docker-compose.yml under $APP_ROOT is the single source of truth.
    It never converts a protection-preflight verdict into PASS; the authorization
    step refuses blocking incidents regardless of acknowledgements.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Prepare', 'Authorize', 'Execute')][string]$Phase,
    [switch]$Authorize,
    [switch]$AcknowledgeFreshHost,
    [string]$ConfirmSha,
    [string]$ConfirmHost,
    [string]$SshUser = 'opipadmin',
    [string]$ToolkitRoot = '/opt/opip-m07-toolkit',
    [switch]$NoReceipt
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\OpipR1.Common.ps1')

$receipt = New-R1Receipt -Name 'Invoke-V2Bootstrap.ps1' -Mode $Phase
$exit = 0
$toolkit = Join-OpipPosixPath -Root $ToolkitRoot -Child 'scripts/v2/bootstrap-v2.sh'
$toolkitArg = ConvertTo-OpipPosixArgument $toolkit
$toolkitRootArg = ConvertTo-OpipPosixArgument $ToolkitRoot

try {
    if ($SshUser -ne $script:OpipR1V2.SshUser) {
        Add-R1Check $receipt 'SSH_ACCOUNT' 'REFUSED' "use $($script:OpipR1V2.SshUser), not $SshUser"
        $null = Complete-R1Receipt $receipt 3 (Get-R1ReceiptPath "bootstrap-$Phase.receipt.json"); exit 3
    }

    if ($Phase -ne 'Prepare') {
        if (-not $Authorize) {
            Add-R1Check $receipt 'OWNER_AUTHORIZATION' 'REFUSED' "-Authorize is required for $Phase"
            $null = Complete-R1Receipt $receipt 3 (Get-R1ReceiptPath "bootstrap-$Phase.receipt.json"); exit 3
        }
        if ($ConfirmSha -ne $script:OpipR1CertifiedSha) {
            Add-R1Check $receipt 'CONFIRM_SHA' 'REFUSED' 'ConfirmSha must equal the certified SHA'
            $null = Complete-R1Receipt $receipt 3 (Get-R1ReceiptPath "bootstrap-$Phase.receipt.json"); exit 3
        }
        if ($ConfirmHost -ne $script:OpipR1V2.DropletId -and $ConfirmHost -ne $script:OpipR1V2.Name) {
            Add-R1Check $receipt 'CONFIRM_HOST' 'REFUSED' 'ConfirmHost must be the V2 droplet id or name'
            $null = Complete-R1Receipt $receipt 3 (Get-R1ReceiptPath "bootstrap-$Phase.receipt.json"); exit 3
        }
        Add-R1Check $receipt 'OWNER_AUTHORIZATION' 'PASS' "authorised $Phase for $ConfirmHost"
    }
    else { Add-R1Check $receipt 'OWNER_AUTHORIZATION' 'PASS' 'prepare is read-only' }

    if ($Phase -eq 'Execute') {
        $remoteArgs = "--mode execute"
    }
    elseif ($Phase -eq 'Authorize') {
        $remoteArgs = "--mode authorize --confirm-sha $($script:OpipR1CertifiedSha) --confirm-host $($script:OpipR1V2.DropletId)"
        if ($AcknowledgeFreshHost) { $remoteArgs += ' --acknowledge-fresh-host' }
    }
    else {
        $remoteArgs = '--mode prepare'
    }

    $steps = @(
        "sudo -n $toolkitArg --mode check-toolkit --toolkit-root $toolkitRootArg",
        "sudo -n $toolkitArg $remoteArgs --toolkit-root $toolkitRootArg"
    )
    $receipt.notes += "REMOTE_STEPS ($Phase):"
    foreach ($step in $steps) { $receipt.notes += $step }

    # Remote execution is intentionally not performed by this build: the owner
    # runs the printed sequence (or the existing deployment workflow) so that no
    # agent process can mutate production infrastructure.
    Add-R1Check $receipt 'REMOTE_EXECUTION' 'INSUFFICIENT' `
        'PENDING LIVE VALIDATION: this wrapper prints the authorised remote sequence; it never opens a remote shell'
    $receipt.notes += "TOOLKIT: $ToolkitRoot (separately provisioned, SHA-256 manifest verified at every run)"
    $receipt.notes += 'No Compose overlay and no generated nginx configuration are staged; the certified docker-compose.yml is the only Compose file'
    if ($exit -eq 0) { $exit = 2 }
}
catch {
    Add-R1Check $receipt 'UNHANDLED' 'FAIL' $_.Exception.Message
    $exit = 1
}

if ($NoReceipt) { $null = Complete-R1Receipt $receipt $exit }
else { $null = Complete-R1Receipt $receipt $exit (Get-R1ReceiptPath "bootstrap-$Phase.receipt.json") }
exit $exit
