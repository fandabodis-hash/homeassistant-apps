$ErrorActionPreference = "Stop"

$vyrobniAdresar = Split-Path -Parent $MyInvocation.MyCommand.Path
$registrSoubor = Join-Path $vyrobniAdresar "registr_serialu.json"
$dnes = Get-Date -Format "yyyyMMdd"

if (Test-Path $registrSoubor) {
    $registr = Get-Content $registrSoubor -Raw | ConvertFrom-Json
}
else {
    $registr = [PSCustomObject]@{
        datum = $dnes
        posledni_poradi = 0
    }
}

if ($registr.datum -ne $dnes) {
    $registr.datum = $dnes
    $registr.posledni_poradi = 0
}

$registr.posledni_poradi = [int]$registr.posledni_poradi + 1

$serioveCislo = "IQF-{0}-{1:D4}" -f $dnes, $registr.posledni_poradi

$registr | ConvertTo-Json |
    Set-Content $registrSoubor -Encoding UTF8

Write-Host ""
Write-Host "Vygenerovane seriove cislo:"
Write-Host $serioveCislo
Write-Host ""
