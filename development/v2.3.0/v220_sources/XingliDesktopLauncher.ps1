# coding: utf-8
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$PluginDir = $PSScriptRoot
$PluginsDir = Split-Path -Parent $PluginDir
$Mo2Dir = Split-Path -Parent $PluginsDir
$MoExe = Join-Path $Mo2Dir 'ModOrganizer.exe'
$Ini = Join-Path $Mo2Dir 'ModOrganizer.ini'
$ShortcutArg = 'moshortcut://Skyrim Special Edition:SKSE'

Write-Host '========================================'
Write-Host '        星黎整合启动器'
Write-Host '========================================'
Write-Host "MO2 目录: $Mo2Dir"

if (!(Test-Path -LiteralPath $MoExe)) {
    Write-Host '[错误] 未找到 ModOrganizer.exe。'
    Write-Host "预期位置: $MoExe"
    exit 10
}

function Get-IniGamePath {
    if (!(Test-Path -LiteralPath $Ini)) { return $null }
    $line = Get-Content -LiteralPath $Ini -Encoding UTF8 -ErrorAction SilentlyContinue | Where-Object { $_ -match '^gamePath=' } | Select-Object -First 1
    if ($line) { return (($line -replace '^gamePath=', '').Trim().Trim('"')) }
    return $null
}

$Candidates = New-Object System.Collections.Generic.List[string]
$IniGamePath = Get-IniGamePath
if ($IniGamePath) { [void]$Candidates.Add($IniGamePath) }
foreach ($rel in @('Stock Game','Game Root','Game','Skyrim Special Edition','SkyrimSE')) {
    [void]$Candidates.Add((Join-Path $Mo2Dir $rel))
}
$Parent = Split-Path -Parent $Mo2Dir
foreach ($rel in @('Stock Game','Skyrim Special Edition','SkyrimSE')) {
    [void]$Candidates.Add((Join-Path $Parent $rel))
}

$Skse = $null
$GameDir = $null
foreach ($dir in ($Candidates | Select-Object -Unique)) {
    if (!$dir) { continue }
    $candidate = Join-Path $dir 'skse64_loader.exe'
    if (Test-Path -LiteralPath $candidate) {
        $Skse = (Resolve-Path -LiteralPath $candidate).Path
        $GameDir = (Resolve-Path -LiteralPath $dir).Path
        break
    }
}

if (!$Skse) {
    Write-Host '[错误] 未找到 skse64_loader.exe。'
    Write-Host '请确认整合包包含 Stock Game 或正确的游戏根目录。'
    exit 11
}
Write-Host "SKSE: $Skse"

function Repair-SkseExecutable {
    param([string]$IniPath, [string]$SksePath, [string]$WorkDir)
    if (!(Test-Path -LiteralPath $IniPath)) { return }
    $lines = Get-Content -LiteralPath $IniPath -Encoding UTF8 -ErrorAction SilentlyContinue
    $idx = $null
    foreach ($line in $lines) {
        if ($line -match '^([0-9]+)\\title=(.+)$') {
            if ($Matches[2].Trim().ToLowerInvariant() -eq 'skse') {
                $idx = $Matches[1]
                break
            }
        }
    }
    if (!$idx) {
        Write-Host '[提示] ModOrganizer.ini 中未找到名为 SKSE 的可执行项，将直接启动 MO2。'
        return
    }

    $safeSkse = $SksePath.Replace([char]92,'/')
    $safeWorkDir = $WorkDir.Replace([char]92,'/')
    $newLines = New-Object System.Collections.Generic.List[string]
    $hasBinary = $false
    $hasWorkDir = $false
    foreach ($line in $lines) {
        if ($line -match ('^' + [regex]::Escape($idx) + '\\binary=')) {
            [void]$newLines.Add("$idx\binary=$safeSkse")
            $hasBinary = $true
        } elseif ($line -match ('^' + [regex]::Escape($idx) + '\\workingDirectory=')) {
            [void]$newLines.Add("$idx\workingDirectory=$safeWorkDir")
            $hasWorkDir = $true
        } else {
            [void]$newLines.Add($line)
        }
    }
    if (!$hasBinary) { [void]$newLines.Add("$idx\binary=$safeSkse") }
    if (!$hasWorkDir) { [void]$newLines.Add("$idx\workingDirectory=$safeWorkDir") }
    Set-Content -LiteralPath $IniPath -Value $newLines -Encoding UTF8
    Write-Host '[完成] 已校正 MO2 中 SKSE 的本机路径。'
}

Repair-SkseExecutable -IniPath $Ini -SksePath $Skse -WorkDir $GameDir
Write-Host '[启动] 正在通过 MO2 启动 SKSE...'
Start-Process -FilePath $MoExe -ArgumentList $ShortcutArg -WorkingDirectory $Mo2Dir
exit 0
