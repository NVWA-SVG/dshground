param([Parameter(Mandatory=$true)][string]$Pptx,[Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$sourceFile=(Resolve-Path -LiteralPath $Pptx).Path
$renderDirectory=[IO.Path]::GetFullPath($OutputDirectory)
New-Item -Path $renderDirectory -ItemType Directory -Force | Out-Null
$previousPowerPoint=@(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue)
$application=$null;$presentation=$null
try {
    $application=New-Object -ComObject PowerPoint.Application
    $presentation=$application.Presentations.Open($sourceFile,-1,0,0)
    $presentation.Export($renderDirectory,'PNG',1600,900)
    $presentation.SaveAs((Join-Path $renderDirectory 'teaching-preview.pdf'),32)
    Write-Output ('Rendered slides: '+$presentation.Slides.Count)
} finally {
    if($presentation){$presentation.Close();[void][Runtime.InteropServices.Marshal]::ReleaseComObject($presentation)}
    if($application){if($previousPowerPoint.Count -eq 0 -and $application.Presentations.Count -eq 0){$application.Quit()};[void][Runtime.InteropServices.Marshal]::ReleaseComObject($application)}
}
