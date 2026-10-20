#Requires -Version 5.1
<#
.SYNOPSIS
    R1 Telegram acceptance: tracked delivery evidence (P1-03).
.DESCRIPTION
    Executes the CERTIFIED tracked path with a mocked transport (no network, no
    real credential) and requires:
      * a delivery event with a message id (tracked receipt),
      * a stable canonical message id (dedupe),
      * a detectable duplicate probe.
    A transport-only probe is reported separately and never satisfies the gate.
    Live delivery remains PENDING until the owner supplies credentials on V2.
#>
[CmdletBinding()]
param([switch]$NoReceipt)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\OpipR1.Common.ps1')

$receipt = New-R1Receipt -Name 'Invoke-V2TelegramValidation.ps1' -Mode 'Mock'
$exit = 0

try {
    $out = Get-R1ReceiptPath 'telegram-tracked.json'
    $env:OPIP_CERTIFIED_SRC = 'C:\Users\ohmpr\opip-mission-07\src'
    $result = Invoke-R1Python -Arguments @(
        (Join-Path $script:OpipR1Scripts 'telegram_tracked_acceptance.py'), '--out', $out) -EvidenceJson $out
    if ($result.code -eq 0 -and $result.json.verdict -eq 'PASS') {
        Add-R1Check $receipt 'TRACKED_DELIVERY' 'PASS' ("message_id={0} events={1}" -f $result.json.canonical_message_id, $result.json.events_recorded)
        Add-R1Check $receipt 'DEDUPE' 'PASS' $result.json.dedupe.reason
        Add-R1Check $receipt 'TRANSPORT_ONLY_SEPARATED' 'PASS' 'transport-only probe is reported independently'
        $receipt.artifacts += $out
    }
    else {
        Add-R1Check $receipt 'TRACKED_DELIVERY' 'FAIL' $result.raw
        $exit = 1
    }

    Add-R1Check $receipt 'LIVE_DELIVERY' 'INSUFFICIENT' `
        'PENDING LIVE VALIDATION: real credentials + a real cycle are required; a mock proves the contract only'
    if ($exit -eq 0) { $exit = 2 }

    $receipt.notes += 'LIVE_TRACKED_PROBE (owner, on V2):'
    $receipt.notes += 'docker compose exec -T ohm-trade-agent python -c "from app.core.config import get_settings as g; from app.services.telegram_delivery import send_tracked_telegram as s; c=g(); print(s(bot_token=c.telegram_bot_token, chat_id=c.telegram_chat_id, message=\"R1 tracked probe\", identity=\"R1PROBE\", alert_family=\"ACTIONABLE\", event_type=\"NEW_ENTRY\", fingerprint=\"r1-live\"))"'
    $receipt.notes += 'docker compose exec -T ohm-trade-agent python -c "from app.services.telegram_delivery import build_delivery_summary; print(build_delivery_summary(scope=\"today\"))"'
}
catch {
    Add-R1Check $receipt 'UNHANDLED' 'FAIL' $_.Exception.Message
    $exit = 1
}

if ($NoReceipt) { $null = Complete-R1Receipt $receipt $exit }
else { $null = Complete-R1Receipt $receipt $exit (Get-R1ReceiptPath 'v2-telegram-validation.receipt.json') }
exit $exit
