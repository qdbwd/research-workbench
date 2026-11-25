# 启动单独的浏览器调试窗口，避免使用日常浏览器配置。
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)][int] $Port = 9222,
    [string] $ProfileDir = (Join-Path $env:LOCALAPPDATA 'KnowledgeOps\browser-agent-profile'),
    [string] $BrowserPath
)
$ErrorActionPreference = 'Stop'
if (!$BrowserPath) {
    $BrowserPath = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (!$BrowserPath -or !(Test-Path -LiteralPath $BrowserPath -PathType Leaf)) {
    throw '未找到 Chrome 或 Edge，请通过 -BrowserPath 指定可执行文件。'
}
$profile = [IO.Path]::GetFullPath($ProfileDir)
if ($profile.Contains('"') -or $profile -eq [IO.Path]::GetPathRoot($profile)) {
    throw '请指定用于浏览器取证的独立目录。'
}
foreach ($normal in @(
    "$env:LOCALAPPDATA\Google\Chrome\User Data",
    "$env:LOCALAPPDATA\Microsoft\Edge\User Data"
)) {
    if ($profile.TrimEnd('\').Equals($normal, 'OrdinalIgnoreCase') -or
        $profile.StartsWith("$normal\", 'OrdinalIgnoreCase') -or
        $normal.StartsWith($profile.TrimEnd('\') + '\', 'OrdinalIgnoreCase')) {
        throw '必须使用独立调试 Profile，不能使用日常 Chrome 或 Edge 目录。'
    }
}
$directory = New-Item -ItemType Directory -Path $profile -Force
for ($ancestor = $directory; $ancestor; $ancestor = $ancestor.Parent) {
    if ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw '调试 Profile 路径不能经过链接或 junction。'
    }
}
Start-Process -FilePath $BrowserPath -ArgumentList @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=`"$profile`"",
    '--no-first-run',
    '--no-default-browser-check',
    'about:blank'
)
Write-Host "专用调试窗口已启动，端口 $Port。请勿在此登录网银或其他高敏账户。"
Write-Host '在项目 .env 中配置并重启服务，然后在工具设置启用本机 CDP。'
Write-Host 'BROWSER_ENABLED=true'
Write-Host 'BROWSER_LOCAL_ATTACH_ENABLED=true'
Write-Host "BROWSER_LOCAL_CDP_ENDPOINT=http://host.docker.internal:$Port"
Write-Host "BROWSER_LOCAL_CDP_HOST_HEADER=localhost:$Port"
