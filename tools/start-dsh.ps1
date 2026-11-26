param([switch]$NoWindow)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$env:DSH_HOME = Join-Path $projectRoot 'harness-home'
$env:DSH_PROFILE = 'original'
$env:DSH_PROFILE_DIR = Join-Path $env:DSH_HOME 'profiles\original'
$profileDir = Join-Path $env:DSH_HOME 'profiles\original'
$serviceUrl = 'http://127.0.0.1:3082/'
$stateFile = Join-Path $projectRoot 'data\dsh-original-process.json'
$logDir = Join-Path $projectRoot 'data\windows-logs'
$stdout = Join-Path $logDir 'dsh-original.stdout.log'
$stderr = Join-Path $logDir 'dsh-original.stderr.log'
$entry = Join-Path $projectRoot 'runtime\node_modules\@deepseek-ai\dsh\lib\bin.js'
$profile = 'original'
$port = 3082
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$tokenUrl = $null
if (Test-Path -LiteralPath $stateFile) {
    try {
        $record = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $running = Get-Process -Id $record.pid -ErrorAction Stop
        if ($running.StartTime.ToUniversalTime().ToString('o') -eq $record.started) {
            if (Test-Path -LiteralPath $stdout) {
                $text = Get-Content -LiteralPath $stdout -Raw
                if ($text -match 'dsh web:\s+(http://\S+)') { $tokenUrl = $matches[1] }
            }
        }
    } catch { }
}
if (-not $tokenUrl) {
    if (-not (Test-Path -LiteralPath (Join-Path $profileDir 'package.json'))) {
        & node.exe $entry $profile --from-default-profile web --help | Out-Null
    }
    $nodeExe = (Get-Command node.exe -ErrorAction Stop).Source
    $arguments = @(('"' + $entry + '"'), $profile, '--no-open', '--host', '127.0.0.1', '--port', $port)
    $process = Start-Process -FilePath $nodeExe -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    @{pid=$process.Id; started=$process.StartTime.ToUniversalTime().ToString('o'); root=$projectRoot; entry=$entry; profile=$profile; port=$port} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $stateFile
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($process.HasExited) { throw ('DSH 原始版未能启动，请查看 ' + $stderr) }
        if (Test-Path -LiteralPath $stdout) {
            $text = Get-Content -LiteralPath $stdout -Raw
            if ($text -match 'dsh web:\s+(http://\S+)') { $tokenUrl = $matches[1]; break }
        }
    }
}
if (-not $tokenUrl) { throw ('未能读取 DSH 原始版访问地址，请查看 ' + $stdout) }
if (-not $NoWindow) {
    $edgeCandidates = @((Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'), (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'))
    $edgeExe = $edgeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($edgeExe) { Start-Process -FilePath $edgeExe -ArgumentList @('--app=' + $tokenUrl, '--new-window') }
    else { Start-Process $tokenUrl }
}
Write-Output $tokenUrl
