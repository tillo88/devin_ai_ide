param(
    [string]$FrontdoorUrl
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($FrontdoorUrl)) {
    $FrontdoorUrl = Read-Host "URL del frontdoor DEVIN (es. http://rig:5000)"
}

$parsed = $null
if (-not [Uri]::TryCreate($FrontdoorUrl, [UriKind]::Absolute, [ref]$parsed)) {
    throw "FrontdoorUrl non e' un URL assoluto valido."
}
if ($parsed.Scheme -notin @("http", "https") -or [string]::IsNullOrWhiteSpace($parsed.Host)) {
    throw "FrontdoorUrl deve usare http/https e contenere un host."
}
if (-not [string]::IsNullOrEmpty($parsed.UserInfo)) {
    throw "FrontdoorUrl non puo' contenere credenziali."
}
if (-not [string]::IsNullOrEmpty($parsed.Query) -or -not [string]::IsNullOrEmpty($parsed.Fragment)) {
    throw "FrontdoorUrl non puo' contenere query o frammenti."
}
if ($parsed.AbsolutePath -ne "/") {
    throw "FrontdoorUrl deve indicare la radice del frontdoor."
}

$configDir = Join-Path $env:APPDATA "DEVIN"
$configPath = Join-Path $configDir "desktop.json"
New-Item -ItemType Directory -Force -Path $configDir | Out-Null

$identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
$userGrant = "*$($identity.Value):(OI)(CI)F"
$systemGrant = "*S-1-5-18:(OI)(CI)F"
& "$env:SystemRoot\System32\icacls.exe" $configDir "/inheritance:r" "/grant:r" $userGrant $systemGrant | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Impossibile proteggere la directory di configurazione (icacls exit $LASTEXITCODE)."
}

$document = [ordered]@{
    schema = "devin_desktop_frontdoor_v1"
    frontdoor_url = $parsed.GetLeftPart([UriPartial]::Authority)
}
$json = $document | ConvertTo-Json
$temporary = Join-Path $configDir (".desktop.{0}.{1}.tmp" -f $PID, [Guid]::NewGuid().ToString("N"))
try {
    [IO.File]::WriteAllText(
        $temporary,
        $json + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporary -Destination $configPath -Force
} finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Force
    }
}
Write-Host "[ok] configurazione DEVIN salvata in $configPath"
