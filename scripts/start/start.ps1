# Windows 版一条命令启动迁跃：缺 uv 就装到项目里，前端改了才重新构建，然后启动后端。
# 双击 start.bat 即可运行；也可以在 PowerShell 里运行 .\start.ps1。
# 启动后打开 http://127.0.0.1:8000（端口可以用 QIANYUE_PORT 改）。
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$port = if ($env:QIANYUE_PORT) { $env:QIANYUE_PORT } else { '8000' }

# 所有下载默认走国内镜像，镜像失败再回官方源。国内连官方源常常不是断开而是极慢，
# 慢不会报错，等官方源失败再换就会一直卡着。设置 QIANYUE_OFFICIAL_SOURCE=1 可以全部改走官方源。
$UseMirror = -not $env:QIANYUE_OFFICIAL_SOURCE
$PythonMirror = 'https://registry.npmmirror.com/-/binary/python-build-standalone'
$PypiMirror = 'https://mirrors.aliyun.com/pypi/simple'
$HfMirror = 'https://hf-mirror.com'
$NpmMirror = 'https://registry.npmmirror.com'

function Fail($message) {
  Write-Host $message -ForegroundColor Red
  exit 1
}

# 从 PyPI 镜像下载 uv 的官方安装包（.whl，其实是 zip），核对 sha256 后取出里面的 uv.exe。
# 版本固定：镜像页面不按版本排序，取“最新”容易拿到旧版。升级 uv 时改这里。
$UvVersion = '0.13.9'
function Install-UvFromMirror($toolsBin) {
  $tag = @{ AMD64 = 'win_amd64'; ARM64 = 'win_arm64' }[$env:PROCESSOR_ARCHITECTURE]
  if (-not $tag) { return $false }
  $ProgressPreference = 'SilentlyContinue'  # Windows PowerShell 的下载进度条会让下载慢几十倍
  try {
    $index = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 30 "$PypiMirror/uv/").Content
    $link = [regex]::Match($index, "href=""([^""]*/uv-$([regex]::Escape($UvVersion))-py3-none-$tag\.whl)#sha256=([0-9a-f]+)""")
    if (-not $link.Success) { return $false }
    $wheel = Join-Path $env:TEMP 'qianyue-uv.zip'
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 300 -OutFile $wheel ([Uri]::new([Uri]"$PypiMirror/uv/", $link.Groups[1].Value))
    if ((Get-FileHash -Algorithm SHA256 $wheel).Hash -ne $link.Groups[2].Value) { return $false }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    New-Item -ItemType Directory -Force $toolsBin | Out-Null
    $zip = [IO.Compression.ZipFile]::OpenRead($wheel)
    try {
      foreach ($entry in $zip.Entries) {
        if ($entry.FullName -match '\.data/scripts/uv[wx]?\.exe$') {
          [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $toolsBin $entry.Name), $true)
        }
      }
    } finally {
      $zip.Dispose()
    }
    Remove-Item $wheel
    & (Join-Path $toolsBin 'uv.exe') --version | Out-Null
    return $LASTEXITCODE -eq 0
  } catch {
    return $false
  }
}

# 1. uv：后端和 Python 3.11 都靠它。缺了装到 tools\bin，不改系统 PATH，删掉这个目录就等于卸载。
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  $toolsBin = Join-Path $root 'tools\bin'
  if (-not (Test-Path (Join-Path $toolsBin 'uv.exe'))) {
    Write-Host '没有找到 uv，正在安装到 tools\bin（只装在项目里，不改动系统配置）……'
    if (-not ($UseMirror -and (Install-UvFromMirror $toolsBin))) {
      if ($UseMirror) { Write-Host '国内镜像下载 uv 失败，改用官方安装脚本……' -ForegroundColor Yellow }
      $env:UV_INSTALL_DIR = $toolsBin
      $env:UV_NO_MODIFY_PATH = '1'
      try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
      } catch {
        Fail "uv 没有安装成功：$_`n请手动安装后重试：https://docs.astral.sh/uv/"
      }
    }
  }
  $env:PATH = "$toolsBin;$env:PATH"
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Fail "uv 装好了却找不到，请检查 $toolsBin。"
  }
}

# ARM 版 Windows（如 Mac 上的 Parallels 虚拟机）：部分依赖没有 ARM 版安装包，改用 x64 版 Python，
# 由 Windows 自带的转译运行。普通 x64 电脑不受影响。
if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -and -not $env:UV_PYTHON) {
  $env:UV_PYTHON = 'cpython-3.11-windows-x86_64-none'
}

# 2. 前端：发布包里已经带着构建好的页面。只有源码和页面不一致时才重新构建，那一步要 npm。
# 指纹格式和 start.sh 一样：每行「哈希  相对路径」。两边的排序规则不同（Mac 的 sort 跟着系统语言走），
# 所以比较前都按同一规则重新排一遍，只看内容。
$frontend = Join-Path $root 'frontend'
function Get-SourceHash {
  $files = @(Get-ChildItem -Path (Join-Path $frontend 'src'), (Join-Path $frontend 'public') -Recurse -File)
  $rootFiles = @('index.html', 'package.json', 'package-lock.json', 'vite.config.ts', 'scripts/copy-font-licenses.mjs', 'scripts/write-build-hash.mjs') +
    @(Get-ChildItem -Path $frontend -Filter 'tsconfig*.json' -File | ForEach-Object { $_.Name })
  foreach ($name in $rootFiles) {
    $file = Join-Path $frontend $name
    if (Test-Path -LiteralPath $file -PathType Leaf) { $files += Get-Item -LiteralPath $file }
  }
  $byPath = @{}
  foreach ($file in $files) {
    $relative = $file.FullName.Substring($frontend.Length + 1).Replace('\', '/')
    $byPath[$relative] = $file
  }
  [string[]]$paths = @($byPath.Keys)
  [Array]::Sort($paths, [StringComparer]::Ordinal)
  $lines = foreach ($relative in $paths) {
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $byPath[$relative].FullName).Hash.ToLower()
    "$hash  $relative"
  }
  ($lines -join "`n") + "`n"
}
$distIndex = Join-Path $frontend 'dist\index.html'
$hashFile = Join-Path $frontend 'dist\.build-hash'
$needBuild = $true
if (Test-Path $distIndex) {
  $saved = if (Test-Path $hashFile) { [IO.File]::ReadAllText($hashFile) } else { '' }
  $needBuild = (Get-SourceHash) -ne $saved
}

if ($needBuild) {
  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    if (Test-Path $distIndex) {
      Write-Host '提示：前端源码比页面新，但没有找到 Node.js，继续用现有的页面。'
      Write-Host '      只有改了前端源码才需要安装 Node.js LTS：https://nodejs.org/'
    } else {
      Fail '缺少构建好的页面（frontend\dist），也没有 Node.js。请下载包含页面的发布包，或安装 Node.js LTS 后重试：https://nodejs.org/'
    }
  } else {
    Push-Location $frontend
    try {
      $lockHash = (Get-FileHash -Algorithm SHA256 -LiteralPath 'package-lock.json').Hash.ToLower()
      $installedHashFile = Join-Path 'node_modules' '.qianyue-lock-hash'
      $installedHash = if (Test-Path $installedHashFile) { [IO.File]::ReadAllText($installedHashFile).Trim() } else { '' }
      if (-not (Test-Path 'node_modules') -or $installedHash -ne $lockHash) {
        Write-Host '安装锁文件指定的前端依赖……'
        $npmInstalled = $false
        if ($UseMirror) { npm ci --no-audit --no-fund --registry=$NpmMirror; $npmInstalled = $LASTEXITCODE -eq 0 }
        if (-not $npmInstalled) {
          if ($UseMirror) { Write-Host '国内镜像（npmmirror）下载失败，改用官方 npm 源重试……' -ForegroundColor Yellow }
          npm ci --no-audit --no-fund
          if ($LASTEXITCODE -ne 0) { Fail '前端依赖没有装成功，见上面的报错。' }
        }
        [IO.File]::WriteAllText($installedHashFile, $lockHash, (New-Object Text.UTF8Encoding $false))
      }
      Write-Host '构建前端……'
      npm run build
      if ($LASTEXITCODE -ne 0) { Fail '前端构建失败，见上面的报错。' }
      [IO.File]::WriteAllText($hashFile, (Get-SourceHash), (New-Object Text.UTF8Encoding $false))
    } finally {
      Pop-Location
    }
  }
}

# 3. Chrome：只有从招聘平台采集岗位时用得到，没有也照常启动。
$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
if (-not ($chromeCandidates | Where-Object { Test-Path $_ })) {
  Write-Host '提示：没有找到 Chrome。从招聘平台采集岗位需要它，也可以直接在岗位页粘贴 JD。'
  Write-Host '      下载：https://www.google.com/chrome/'
}

# Windows 默认不允许普通程序建符号链接，下载检索模型时 huggingface_hub 会打一段英文警告。
# 不影响使用（模型照样缓存在 data\models），关掉免得误以为出错。
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'

# 4. 后端依赖：默认从国内镜像装（Python 用 npmmirror，依赖包用阿里云），失败了再回官方源。
# 镜像这一步先导出依赖清单再装，装的是锁文件里同样的版本（带哈希校验），锁文件本身不改；
# 之后再启动时环境已经齐了，不会再去联网。
Set-Location (Join-Path $root 'backend')
$installed = $false
if ($UseMirror) {
  $setPythonMirror = -not $env:UV_PYTHON_INSTALL_MIRROR  # 用户自己设过的不动
  if ($setPythonMirror) { $env:UV_PYTHON_INSTALL_MIRROR = $PythonMirror }
  $requirements = Join-Path $env:TEMP 'qianyue-requirements.txt'
  # 环境已经有 Python 就不再建：重建会覆盖 python.exe，它正被占用时 Windows 不让覆盖。
  $installed = $true
  if (-not (Test-Path '.venv\Scripts\python.exe')) { uv venv --allow-existing --quiet; $installed = $LASTEXITCODE -eq 0 }
  if ($installed) { uv export --frozen --no-emit-project --quiet -o $requirements; $installed = $LASTEXITCODE -eq 0 }
  if ($installed) { uv pip sync --index-url $PypiMirror $requirements; $installed = $LASTEXITCODE -eq 0 }
  if (-not $installed) {
    Write-Host '国内镜像下载失败，改用官方源重试……' -ForegroundColor Yellow
    if ($setPythonMirror) { Remove-Item Env:UV_PYTHON_INSTALL_MIRROR }
  }
}
if (-not $installed) {
  uv sync --frozen
  if ($LASTEXITCODE -ne 0) { Fail '国内镜像和官方源都没有下载成功。请检查网络后重新运行；已经下载的部分会保留，不用从头再来。' }
}

# 5. 检索模型由后端启动后在后台下载。默认用 hf-mirror.com，它连不上才走 huggingface.co。
# 自己设过 HF_ENDPOINT 的不动。
if ($UseMirror -and -not $env:HF_ENDPOINT) {
  try {
    Invoke-WebRequest -UseBasicParsing -Method Head $HfMirror -TimeoutSec 3 | Out-Null
    $env:HF_ENDPOINT = $HfMirror
  } catch {
    if ($_.Exception.Response) { $env:HF_ENDPOINT = $HfMirror }  # 有回应（哪怕是报错页）就说明能连上
  }
}

# 6. 配置和启动。模型 Key 可以先不填，在页面「设置」里填完立即生效。
if (-not (Test-Path '.env')) {
  Copy-Item '.env.example' '.env'
  Write-Host '已经从 .env.example 复制出 backend\.env。模型 Key 可以在页面的「设置」里填。'
}

Write-Host "启动迁跃：打开 http://127.0.0.1:${port}（Ctrl+C 停止）"
uv run --frozen --no-sync uvicorn app.main:app --host 127.0.0.1 --port $port
exit $LASTEXITCODE
