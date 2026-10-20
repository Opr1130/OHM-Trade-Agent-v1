#Requires -Version 5.1
<#
.SYNOPSIS
    R1 dashboard + HTTPS readiness (P1-02).
.DESCRIPTION
    Adds the missing prerequisites: certificate challenge requirements, firewall
    restriction, hostname verification, renewal, and a fail-closed rule so a
    missing certificate can never produce an unsafe partial deployment. Route,
    authorization and historical-data checks are executed separately (pytest +
    dashboard_data_acceptance.py) rather than inferred from HTTP 200.
#>
[CmdletBinding()]
param(
    [string]$TlsFactsPath,
    [string]$SrcRoot = 'C:\Users\ohmpr\opip-mission-07\src',
    [switch]$NoReceipt
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\OpipR1.Common.ps1')

$receipt = New-R1Receipt -Name 'Invoke-V2DashboardValidation.ps1' -Mode 'Validate'
$exit = 0

try {
    $routes = Join-Path $SrcRoot 'OHM-Trade-Agent-v1\app\api\dashboard.py'
    if (-not (Test-Path -LiteralPath $routes)) { throw "certified dashboard routes not found: $routes" }
    $text = Get-Content -LiteralPath $routes -Raw
    $required = @('/dashboard', '/api/analytics/summary', '/api/analytics/intelligence', '/api/opip/zero-trade-explanation')
    $missing = @($required | Where-Object { $text -notmatch [regex]::Escape($_) })
    if ($missing.Count -eq 0) { Add-R1Check $receipt 'ROUTES_PRESENT' 'PASS' "routes=$($required.Count)" }
    else { Add-R1Check $receipt 'ROUTES_PRESENT' 'FAIL' ($missing -join ','); $exit = 1 }
    if ($text -match '_require_secret\(x_webhook_secret\)') { Add-R1Check $receipt 'AUTH_REQUIRED' 'PASS' 'analytics routes require x-webhook-secret' }
    else { Add-R1Check $receipt 'AUTH_REQUIRED' 'FAIL' 'no secret check found'; $exit = 1 }

    # R3: the tracked config from the certified release is authoritative; no V2
    # nginx configuration is generated, staged or overlaid.
    $nginx = Get-Content -LiteralPath (Join-Path $script:OpipR1Root '..\nginx\dashboard-sidecar.conf') -Raw
    if ($nginx -match 'ssl_certificate\s+/etc/letsencrypt/live/161-35-106-207\.sslip\.io/fullchain\.pem') {
        Add-R1Check $receipt 'NGINX_CERT_PATH' 'PASS' 'tracked certificate path'
    }
    else { Add-R1Check $receipt 'NGINX_CERT_PATH' 'FAIL' 'tracked certificate path missing'; $exit = 1 }
    if ($nginx -match 'location / \{\s*return 404;') { Add-R1Check $receipt 'FAIL_CLOSED_DEFAULT' 'PASS' 'unknown paths return 404' }
    else { Add-R1Check $receipt 'FAIL_CLOSED_DEFAULT' 'FAIL' 'default 404 missing'; $exit = 1 }

    if ($TlsFactsPath -and (Test-Path -LiteralPath $TlsFactsPath)) {
        $eval = @'
import json, sys
sys.path.insert(0, sys.argv[2])
import opip_recovery as r
print(json.dumps(r.verify_tls_prerequisites(json.load(open(sys.argv[1], encoding="utf-8")))))
'@
        $probe = Join-Path $env:TEMP 'r1_tls_eval.py'
        Set-Content -LiteralPath $probe -Value $eval -Encoding UTF8
        $result = Invoke-R1Python -Arguments @($probe, $TlsFactsPath, $script:OpipR1Scripts)
        if ($result.code -eq 0 -and $result.raw -match '"verdict": "PASS"') {
            Add-R1Check $receipt 'TLS_PREREQUISITES' 'PASS' 'challenge, firewall, permissions, renewal all satisfied'
        }
        else {
            Add-R1Check $receipt 'TLS_PREREQUISITES' 'REFUSED' ($result.raw -replace "`n", ' ')
            $exit = 3
        }
    }
    else {
        Add-R1Check $receipt 'TLS_PREREQUISITES' 'INSUFFICIENT' 'PENDING LIVE VALIDATION: supply -TlsFactsPath from the host'
        if ($exit -eq 0) { $exit = 2 }
    }

    $receipt.notes += 'LIVE_PROOF (owner, after issuance):'
    $receipt.notes += "curl -sS -o /dev/null -w '%{http_code}' --resolve 178-128-147-125.sslip.io:8443:178.128.147.125 https://178-128-147-125.sslip.io:8443/dashboard           # expect 200"
    $receipt.notes += "curl -sS -o /dev/null -w '%{http_code}' --resolve 178-128-147-125.sslip.io:8443:178.128.147.125 https://178-128-147-125.sslip.io:8443/api/analytics/summary # expect 401"
    $receipt.notes += "openssl s_client -connect 178.128.147.125:8443 -servername 178-128-147-125.sslip.io </dev/null 2>/dev/null | openssl x509 -noout -dates"
    $receipt.notes += 'RENEWAL: systemctl list-timers | grep -i certbot; certbot renew --dry-run'
    $receipt.notes += 'FIREWALL: allow 8443 only from the owner address range; keep 8000 loopback-only.'
}
catch {
    Add-R1Check $receipt 'UNHANDLED' 'FAIL' $_.Exception.Message
    $exit = 1
}

if ($NoReceipt) { $null = Complete-R1Receipt $receipt $exit }
else { $null = Complete-R1Receipt $receipt $exit (Get-R1ReceiptPath 'v2-dashboard-validation.receipt.json') }
exit $exit
