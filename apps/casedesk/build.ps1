# Build the CaseDesk demo app: a self-contained folder and a zip under apps/casedesk/publish/.
# Needs the .NET 6 SDK. Nothing is downloaded: the project has no NuGet packages, and the
# self-contained runtime comes from the SDK's runtime packs when they are installed
# (pass -FrameworkDependent to build against the installed .NET 6 Desktop Runtime instead).
param([switch]$FrameworkDependent)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$out = Join-Path $here "publish\casedesk"
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
$selfContained = if ($FrameworkDependent) { "false" } else { "true" }
dotnet publish (Join-Path $here "src\CaseDeskDemo.csproj") -c Release -r win-x64 --self-contained $selfContained -o $out -nologo
if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed" }
$zip = Join-Path $here "publish\casedesk-win-x64.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Compress-Archive -Path (Join-Path $out "*") -DestinationPath $zip
Write-Output "built $out\CaseDesk.exe and $zip"
