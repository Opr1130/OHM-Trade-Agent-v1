#Requires -Version 5.1
<#
.SYNOPSIS
    R1 data-migration orchestrator (P0-02).
.DESCRIPTION
    Modes: Inventory, Export, Verify, Stage, RestoreCanonical, Promote, Reconcile, DryRun.
    Every mutating mode requires -Authorize. RestoreCanonical is the ONLY mode that
    claims database recovery; Stage/Promote are file operations and report so.
.EXAMPLE
    pwsh -File scripts/Invoke-OpipDataMigration.ps1 -Mode DryRun -SourceRoot <fixture>
#>
[CmdletBinding()]
param(
    [ValidateSet('Inventory', 'Export', 'Verify', 'Stage', 'RestoreCanonical', 'Promote', 'Reconcile', 'Provenance', 'DryRun')]
    [string]$Mode = 'DryRun',
    [string]$SourceRoot,
    [string]$BundleDir,
    [string]$CanonicalDir,
    [string]$StagingDir,
    [string]$TargetRoot,
    [string]$AppRoot = '/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1',
    [string]$LiveDb,
    [string]$RecoveryReceipt,
    [string]$ProvenanceEvidence,
    [string]$MaxAgeHours = '24',
    [switch]$AllowFixture,
    [int]$ChunkMB = 0,
    [switch]$AllowSyntheticCanonical,
    [switch]$Authorize,
    [switch]$Force,
    [switch]$NoReceipt
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\OpipR1.Common.ps1')

$receipt = New-R1Receipt -Name 'Invoke-OpipDataMigration.ps1' -Mode $Mode
$spec = Join-Path $script:OpipR1Root 'drafts\v1-dataset-spec.json'
$bundleCli = Join-Path $script:OpipR1Scripts 'opip_bundle.py'
$recoveryCli = Join-Path $script:OpipR1Scripts 'opip_recovery.py'
$exit = 0
$steps = @()

function Invoke-R1Step {
    param([string]$Id, [string[]]$Arguments, [string]$EvidenceName)
    $evidence = Get-R1ReceiptPath $EvidenceName
    $result = Invoke-R1Python -Arguments $Arguments -EvidenceJson $evidence
    $status = 'PASS'
    if ($result.code -eq 2) { $status = 'INSUFFICIENT' }
    elseif ($result.code -eq 3) { $status = 'REFUSED' }
    elseif ($result.code -eq 77) { $status = 'SKIP' }
    elseif ($result.code -ne 0) { $status = 'FAIL' }
    Add-R1Check $receipt $Id $status ("exit={0} evidence={1}" -f $result.code, $evidence)
    $script:steps += [ordered]@{ id = $Id; exit = $result.code }
    return $result.code
}

try {
    switch ($Mode) {
        'Inventory' {
            if (-not $SourceRoot) { throw 'Inventory requires -SourceRoot' }
            $exit = Invoke-R1Step 'INVENTORY' @($bundleCli, 'inventory', '--source-root', $SourceRoot, '--spec', $spec) 'inventory.json'
        }
        'Export' {
            if (-not $SourceRoot) { throw 'Export requires -SourceRoot' }
            $args = @($bundleCli, 'export', '--source-root', $SourceRoot, '--spec', $spec,
                '--out-dir', (Join-Path $script:OpipR1Evidence 'bundles'),
                '--bundle-name', ("r1-" + (Get-Date -Format 'yyyyMMddTHHmmssZ')),
                '--chunk-mb', "$ChunkMB")
            if ($CanonicalDir) { $args += @('--canonical-dir', $CanonicalDir) }
            if ($AllowSyntheticCanonical) { $args += '--allow-synthetic-canonical' }
            if ($Force) { $args += '--force' }
            if ($Authorize) { $args += '--authorize' }
            $exit = Invoke-R1Step 'EXPORT' $args 'export.json'
        }
        'Verify' {
            if (-not $BundleDir) { throw 'Verify requires -BundleDir' }
            $exit = Invoke-R1Step 'VERIFY' @($bundleCli, 'verify', '--bundle', $BundleDir) 'verify.json'
        }
        'Stage' {
            if (-not $BundleDir -or -not $StagingDir) { throw 'Stage requires -BundleDir and -StagingDir' }
            $exit = Invoke-R1Step 'STAGE' @($bundleCli, 'stage', '--bundle', $BundleDir, '--staging', $StagingDir) 'stage.json'
        }
        'RestoreCanonical' {
            if (-not $BundleDir -or -not $LiveDb) { throw 'RestoreCanonical requires -BundleDir and -LiveDb' }
            $receiptPath = if ($RecoveryReceipt) { $RecoveryReceipt } else { Join-Path $script:OpipR1Evidence 'canonical-recovery-receipt.json' }
            $args = @($recoveryCli, 'restore-canonical', '--bundle', $BundleDir, '--app-root', $AppRoot,
                '--live-db', $LiveDb, '--receipt-out', $receiptPath)
            if ($Authorize) { $args += '--authorize' } else { $args += '--dry-run' }
            $exit = Invoke-R1Step 'RESTORE_CANONICAL' $args 'canonical-restore.json'
            $receipt.notes += "RECOVERY_RECEIPT: $receiptPath"
        }
        'Promote' {
            if (-not $StagingDir -or -not $TargetRoot) { throw 'Promote requires -StagingDir and -TargetRoot' }
            if (-not $Authorize) {
                Add-R1Check $receipt 'PROMOTE_AUTHORIZATION' 'REFUSED' '-Authorize is required to modify live destination state'
                $exit = 3
            }
            else {
                $args = @($bundleCli, 'promote', '--staging', $StagingDir, '--target', $TargetRoot, '--authorize')
                if ($RecoveryReceipt) { $args += @('--recovery-receipt', $RecoveryReceipt) }
                if ($Force) { $args += '--force' }
                $exit = Invoke-R1Step 'PROMOTE' $args 'promote.json'
            }
        }
        'Provenance' {
            if (-not $BundleDir -or -not $ProvenanceEvidence) { throw 'Provenance requires -BundleDir and -ProvenanceEvidence' }
            $args = @((Join-Path $script:OpipR1Scripts 'opip_provenance.py'), '--bundle', $BundleDir,
                '--evidence', $ProvenanceEvidence, '--max-age-hours', $MaxAgeHours)
            if ($AllowFixture) { $args += '--allow-fixture' }
            $exit = Invoke-R1Step 'PROVENANCE' $args 'provenance.json'
        }
        'Reconcile' {
            if (-not $BundleDir -or -not $TargetRoot) { throw 'Reconcile requires -BundleDir and -TargetRoot' }
            $exit = Invoke-R1Step 'RECONCILE' @($bundleCli, 'reconcile', '--bundle', $BundleDir, '--target', $TargetRoot) 'reconcile.json'
        }
        'DryRun' {
            if ($SourceRoot) {
                $null = Invoke-R1Step 'INVENTORY' @($bundleCli, 'inventory', '--source-root', $SourceRoot, '--spec', $spec) 'inventory.json'
                $null = Invoke-R1Step 'EXPORT_PLAN' @($bundleCli, 'export', '--source-root', $SourceRoot, '--spec', $spec,
                    '--out-dir', (Join-Path $script:OpipR1Evidence 'bundles'), '--bundle-name', 'dry-run') 'export-plan.json'
                $exit = 77
            }
            else {
                Add-R1Check $receipt 'DRYRUN' 'INSUFFICIENT' 'supply -SourceRoot to plan an export'
                $exit = 2
            }
        }
    }
    $receipt.notes += ('STEPS: ' + (($steps | ForEach-Object { "$($_.id)=$($_.exit)" }) -join ' '))
}
catch {
    Add-R1Check $receipt 'UNHANDLED' 'FAIL' $_.Exception.Message
    $exit = 1
}

if ($NoReceipt) { $null = Complete-R1Receipt $receipt $exit }
else { $null = Complete-R1Receipt $receipt $exit (Get-R1ReceiptPath 'data-migration.receipt.json') }
exit $exit
