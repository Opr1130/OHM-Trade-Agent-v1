#Requires -Version 5.1
<#
.SYNOPSIS
    HF-05: two-phase V2 readiness (PreBootstrap | PostConfiguration).
.DESCRIPTION
    PreBootstrap  - identity, installed certified source release, certified image,
                    storage, backups, container state. Does NOT require /opt
                    checkout, .env or deployed Compose overlays.
    PostConfiguration - installed release manifest (no Git), .env, effective Compose
                    settings, resource limits, volumes and release modes.
    Privileged reads use `sudo -n` as opipadmin; SSH uses strict known-host
    verification. A probe is never PASS from locally supplied metadata.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('PreBootstrap', 'PostConfiguration')][string]$Phase,
    [switch]$Execute,
    [switch]$Authorized,
    [string]$SshUser = 'opipadmin',
    [string]$ToolkitRoot = '/opt/opip-m07-toolkit',
    [switch]$NoReceipt
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\OpipR1.Common.ps1')

$receipt = New-R1Receipt -Name 'Invoke-V2Readiness.ps1' -Mode $Phase
$exit = 0
$sha = $script:OpipR1CertifiedSha
$app = $script:OpipR1V2.AppRoot
$releases = '/srv/opip-v2/releases'
$script:probes = @()

function Invoke-PhaseProbe {
    param([string]$Id, [string]$Command, [scriptblock]$Evaluate)
    $probe = Invoke-R1SshProbe -Command $Command -User $SshUser
    $script:probes += $probe
    if ($probe.exit_code -ne 0 -or -not $probe.evidence) {
        Add-R1Check $receipt $Id 'INSUFFICIENT' ("rc={0} {1}" -f $probe.exit_code, $probe.stderr)
        return $false
    }
    $ok = & $Evaluate $probe.evidence
    if ($ok) { Add-R1Check $receipt $Id 'PASS' ($probe.evidence -replace "`r?`n", ' ') }
    else { Add-R1Check $receipt $Id 'FAIL' ($probe.evidence -replace "`r?`n", ' ') }
    return $ok
}

try {
    if ($SshUser -ne $script:OpipR1V2.SshUser) {
        Add-R1Check $receipt 'SSH_ACCOUNT' 'REFUSED' "use $($script:OpipR1V2.SshUser), not $SshUser"
        $null = Complete-R1Receipt $receipt 3 (Get-R1ReceiptPath "readiness-$Phase.receipt.json"); exit 3
    }
    Add-R1Check $receipt 'SSH_ACCOUNT' 'PASS' "account=$SshUser (privileged reads via sudo -n)"

    if (-not $Execute) {
        Add-R1Check $receipt 'REMOTE_PROBES' 'INSUFFICIENT' 'PENDING LIVE VALIDATION: run with -Execute -Authorized'
        $exit = 2
    }
    elseif (-not $Authorized) {
        Add-R1Check $receipt 'REMOTE_PROBES' 'REFUSED' '-Execute requires -Authorized'
        $exit = 3
    }
    else {
        if (-not (Invoke-PhaseProbe 'REMOTE_IDENTITY' 'curl -fsS http://169.254.169.254/metadata/v1/id' {
                    param($e) $e.Trim() -eq $script:OpipR1V2.DropletId })) { $exit = 1 }

        if ($Phase -eq 'PreBootstrap') {
            # L-01: /srv/opip-v2/releases is 0700 root on the V2 host, so an
            # unprivileged `test -d` is a false negative even for a correct
            # release. The approved account performs this one privileged read
            # through the same non-interactive `sudo -n` mechanism used for the
            # image probe. An absent directory, an unreadable directory and a
            # sudo refusal are all refusals; none of them can become PASS.
            $releasePath = "$releases/$sha/OHM-Trade-Agent-v1"
            $sourceProbe = Invoke-R1SshProbe -Command (
                "sudo -n bash -c 'p=`"$releasePath`"; " +
                "if [ ! -d `"`$p`" ]; then echo MISSING; exit 4; fi; " +
                "if [ ! -r `"`$p`" ] || [ ! -x `"`$p`" ]; then echo UNREADABLE; exit 5; fi; " +
                "echo PRESENT'"
            ) -User $SshUser
            $script:probes += $sourceProbe
            $sourceText = (@($sourceProbe.evidence, $sourceProbe.stderr) | Where-Object { $_ }) -join ' '
            if ($sourceProbe.exit_code -eq 0 -and $sourceProbe.evidence -and
                $sourceProbe.evidence.Trim() -eq 'PRESENT') {
                Add-R1Check $receipt 'SOURCE_RELEASE_PRESENT' 'PASS' $sourceText
            }
            elseif ($sourceText -match 'MISSING') {
                Add-R1Check $receipt 'SOURCE_RELEASE_PRESENT' 'FAIL' "release directory absent: $releasePath"
                $exit = 1
            }
            elseif ($sourceText -match 'UNREADABLE') {
                Add-R1Check $receipt 'SOURCE_RELEASE_PRESENT' 'FAIL' `
                    "release directory is not readable by $SshUser even via sudo -n: $releasePath"
                $exit = 1
            }
            else {
                Add-R1Check $receipt 'SOURCE_RELEASE_PRESENT' 'INSUFFICIENT' `
                    ("rc={0} {1}" -f $sourceProbe.exit_code, $sourceText)
                $exit = 1
            }

            if (-not (Invoke-PhaseProbe 'CERTIFIED_IMAGE' "sudo -n docker image inspect -f '{{json .Config.Labels}}' $($script:OpipR1V2.ImageTag)" {
                        param($e) $e -match [regex]::Escape($sha) })) { $exit = 1 }
            if (-not (Invoke-PhaseProbe 'CONTAINER_STATE' "sudo -n docker ps -a --format '{{.Names}}' | grep -c '^ohm-trade-agent$' || true" {
                        param($e) $e.Trim() -match '^[0-9]+$' })) { $exit = 1 }
            if (-not (Invoke-PhaseProbe 'STORAGE' "df -Pk / | tail -1 | awk '{print `$4}'" {
                        param($e) [int]($e.Trim()) -gt 524288 })) { $exit = 1 }
            # D11: PreBootstrap runs BEFORE the final V1 generation is transferred.
            # A backup count here can only be a rehearsal seed (or zero), so it must
            # never gate bootstrap. Record it as evidence, not as a prerequisite.
            $seedEvidence = Invoke-R1SshProbe "ls -1 $releases/$sha/OHM-Trade-Agent-v1/data/opip/canonical/backups/*.manifest.json 2>/dev/null | wc -l"
            $seedObserved = if ($seedEvidence.evidence) { $seedEvidence.evidence } else { 'unavailable' }
            Add-R1Check $receipt 'BACKUP_SEED_INFO' 'SKIP' ("informational only; final V1 generation is verified after transfer (observed={0})" -f $seedObserved)
            Add-R1Check $receipt 'PHASE_SCOPE' 'PASS' 'no /opt checkout, .env, overlay or final backup generation required in PreBootstrap'
        }
        else {
            # AC-009: the runtime tree is installed without .git. Verify the
            # installer manifest, recomputed from installed contents, using the
            # same exclusions as installation; no Git command is used.
            if (-not (Invoke-PhaseProbe 'RELEASE_MANIFEST' "sudo -n $ToolkitRoot/scripts/v2/bootstrap-v2.sh --mode check-release" {
                        param($e) $e -match 'RELEASE_CHECK=PASS' })) { $exit = 1 }
            if (-not (Invoke-PhaseProbe 'ENV_PRESENT' "test -s $app/.env && stat -c '%a %U' $app/.env" {
                        param($e) $e.Trim() -match '^(600|640) root' })) { $exit = 1 }
            if (-not (Invoke-PhaseProbe 'EFFECTIVE_COMPOSE' "cd $app && sudo -n docker compose config 2>/dev/null | grep -E 'mem_limit|cpus|OPIP_PAPER_V2_MODE|OPIP_CANONICAL_WRITER_MODE' | sort -u" {
                        param($e) ($e -match '128m' -or $e -match '1280m') -and $e -match 'OPIP_PAPER_V2_MODE' -and $e -match 'off' })) { $exit = 1 }
            if (-not (Invoke-PhaseProbe 'VOLUMES' "cd $app && sudo -n docker compose config --volumes" {
                        param($e) $e -match 'data' })) { $exit = 1 }
            if (-not (Invoke-PhaseProbe 'RELEASE_MODES' "cd $app && sudo -n docker compose run --rm --no-deps -v `"$app/docker-compose.yml:/repo/docker-compose.yml:ro`" ohm-trade-agent python -m app.services.release_profiles --profile EVIDENCE_SHADOW --repo-root /repo" {
                        param($e) $e -match 'ARCHITECTURE_GATE=PASS' })) { $exit = 1 }
            Add-R1Check $receipt 'PHASE_SCOPE' 'PASS' 'installed manifest, .env, effective config, volumes and modes verified'
        }
    }
}
catch {
    Add-R1Check $receipt 'UNHANDLED' 'FAIL' $_.Exception.Message
    $exit = 1
}

if ($script:probes.Count -gt 0) {
    $evidencePath = Get-R1ReceiptPath "readiness-$Phase.probes.json"
    ($script:probes | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $evidencePath -Encoding UTF8
    $receipt.artifacts += $evidencePath
}
if ($NoReceipt) { $null = Complete-R1Receipt $receipt $exit }
else { $null = Complete-R1Receipt $receipt $exit (Get-R1ReceiptPath "readiness-$Phase.receipt.json") }
exit $exit

