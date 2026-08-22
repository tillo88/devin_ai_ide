param(
    [string]$SourceRepo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).ProviderPath
)

$ErrorActionPreference = "Stop"
$template = Join-Path $SourceRepo "devin\ui\templates\codex_app.html"
$staticSource = Join-Path $SourceRepo "devin\ui\static"
$output = Join-Path $SourceRepo "src-tauri\frontend"
$stamp = Get-Date -Format "yyyyMMddHHmmss"

$html = [IO.File]::ReadAllText($template, [Text.Encoding]::UTF8)
if (-not $html.Contains("{{ shell_version }}")) {
    Write-Host "[warn] '{{ shell_version }}' non trovato nel template" -ForegroundColor Yellow
}
$html = $html.Replace("{{ shell_version }}", $stamp)
$mainScript = '<script type="module" src="/static/js/codex_app.js?v=' + $stamp + '"></script>'
$bootScript = '<script type="module" src="/static/js/desktop_bootstrap.js?v=' + $stamp + '"></script>'
if (-not $html.Contains($mainScript)) {
    throw "Tag codex_app.js non trovato: bundle desktop non generato."
}
$html = $html.Replace($mainScript, $bootScript)

$expectedOutput = [IO.Path]::GetFullPath((Join-Path $SourceRepo "src-tauri\frontend"))
if (Test-Path -LiteralPath $output) {
    $resolvedOutput = (Resolve-Path -LiteralPath $output).ProviderPath
    if ($resolvedOutput -ine $expectedOutput) {
        throw "Output frontend risolto fuori destinazione: $resolvedOutput"
    }
    Remove-Item -LiteralPath $resolvedOutput -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $output | Out-Null
[IO.File]::WriteAllText(
    (Join-Path $output "index.html"),
    $html,
    [Text.UTF8Encoding]::new($false)
)
Copy-Item -LiteralPath $staticSource -Destination (Join-Path $output "static") -Recurse -Force

$count = (Get-ChildItem -LiteralPath $output -File -Recurse | Measure-Object).Count
Write-Host "[ok] bundle desktop: $output ($count file, versione $stamp)"
