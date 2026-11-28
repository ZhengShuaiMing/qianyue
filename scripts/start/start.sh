#!/usr/bin/env bash
# 一条命令启动迁跃：缺 uv 就装到项目里，前端改了才重新构建，然后启动后端。
# 发布包自带构建好的页面：不装 Node.js 也能直接用，只有改前端源码才需要它。
# 后端同时提供页面和接口，启动后打开 http://127.0.0.1:8000（端口可以用 QIANYUE_PORT 改）。
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
port="${QIANYUE_PORT:-8000}"

# 所有下载默认走国内镜像，镜像失败再回官方源。国内连官方源常常不是断开而是极慢，
# 慢不会报错，等官方源失败再换就会一直卡着。设置 QIANYUE_OFFICIAL_SOURCE=1 可以全部改走官方源。
if [ -z "${QIANYUE_OFFICIAL_SOURCE:-}" ]; then use_mirror=1; else use_mirror=0; fi
python_mirror="https://registry.npmmirror.com/-/binary/python-build-standalone"
pypi_mirror="https://mirrors.aliyun.com/pypi/simple"
hf_mirror="https://hf-mirror.com"
npm_mirror="https://registry.npmmirror.com"

die() {
  echo "$1" >&2
  exit 1
}

# 版本固定：镜像页面不按版本排序，取“最新”容易拿到旧版。升级 uv 时改这里。
uv_version="0.13.9"

sha256_of() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1"; else sha256sum "$1"; fi | cut -d' ' -f1
}

# 从 PyPI 镜像下载 uv 的官方安装包（.whl，其实是 zip），核对 sha256 后取出里面的 uv。
install_uv_from_mirror() {
  local tag link wheel
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) tag='macosx_[0-9_]+_arm64' ;;
    Darwin-x86_64) tag='macosx_[0-9_]+_x86_64' ;;
    Linux-x86_64) tag='manylinux_2_17_x86_64[a-z0-9_.]*' ;;
    Linux-aarch64 | Linux-arm64) tag='manylinux_2_17_aarch64[a-z0-9_.]*' ;;
    *) return 1 ;;
  esac
  command -v unzip >/dev/null 2>&1 || return 1
  # 链接是相对路径（../../packages/...），curl 会自己拼好。
  link="$(curl -fsSL --max-time 30 "$pypi_mirror/uv/" \
    | grep -oE "href=\"[^\"]*/uv-${uv_version}-py3-none-${tag}\.whl#sha256=[0-9a-f]+\"" | head -1)" || return 1
  [ -n "$link" ] || return 1
  link="${link#href=\"}"
  link="${link%\"}"
  wheel="$(mktemp)"
  curl -fsSL --max-time 300 -o "$wheel" "$pypi_mirror/uv/${link%%#*}" \
    && [ "$(sha256_of "$wheel")" = "${link##*sha256=}" ] \
    && mkdir -p "$root/tools/bin" \
    && unzip -o -j -q "$wheel" 'uv-*.data/scripts/uv' 'uv-*.data/scripts/uvx' -d "$root/tools/bin" \
    && chmod +x "$root/tools/bin/uv" "$root/tools/bin/uvx" \
    && "$root/tools/bin/uv" --version >/dev/null 2>&1
  local status=$?
  rm -f "$wheel"
  return "$status"
}

# 1. uv：后端和 Python 3.11 都靠它。缺了装到 tools/bin，不动 shell 配置，删掉这个目录就等于卸载。
if ! command -v uv >/dev/null 2>&1; then
  if [ -x "$root/tools/bin/uv" ]; then
    export PATH="$root/tools/bin:$PATH"
  else
    command -v curl >/dev/null 2>&1 || die "缺少 curl，无法自动安装 uv。请手动安装后重试：https://docs.astral.sh/uv/"
    echo "没有找到 uv，正在安装到 tools/bin（只装在项目里，不改动系统配置）……"
    if [ "$use_mirror" = 0 ] || ! install_uv_from_mirror; then
      [ "$use_mirror" = 0 ] || echo "国内镜像下载 uv 失败，改用官方安装脚本……"
      curl -LsSf https://astral.sh/uv/install.sh \
        | env UV_INSTALL_DIR="$root/tools/bin" UV_NO_MODIFY_PATH=1 sh \
        || die "uv 没有安装成功。请手动安装后重试：https://docs.astral.sh/uv/"
    fi
    export PATH="$root/tools/bin:$PATH"
    command -v uv >/dev/null 2>&1 || die "uv 装好了却找不到，请检查 $root/tools/bin。"
  fi
fi

# 2. 前端：发布包里已经带着构建好的页面。只有源码和页面不一致时才重新构建，那一步要 npm。
cd "$root/frontend"
# 用内容指纹比较新旧。zip 解压后源码和页面的时间戳几乎相同，比时间会误判。
# 排序固定按字节序（LC_ALL=C），不跟着系统语言走，Mac、Linux 和 Windows 的 PowerShell 脚本算出来一样。
source_hash() {
  local file
  {
    find src public -type f -print0
    for file in index.html package.json package-lock.json vite.config.ts scripts/copy-font-licenses.mjs scripts/write-build-hash.mjs; do
      [ ! -f "$file" ] || printf '%s\0' "$file"
    done
    for file in tsconfig*.json; do
      [ ! -f "$file" ] || printf '%s\0' "$file"
    done
  } | LC_ALL=C sort -z | if command -v shasum >/dev/null 2>&1; then xargs -0 shasum -a 256; else xargs -0 sha256sum; fi
}

need_build=0
if [ ! -f dist/index.html ]; then
  need_build=1
elif [ "$(source_hash)" != "$(cat dist/.build-hash 2>/dev/null || true)" ]; then
  need_build=1
fi

if [ "$need_build" = 1 ]; then
  if ! command -v npm >/dev/null 2>&1; then
    if [ -f dist/index.html ]; then
      echo "提示：前端源码比页面新，但没有找到 Node.js，继续用现有的页面。"
      echo "      只有改了前端源码才需要安装 Node.js LTS：https://nodejs.org/"
    else
      die "缺少构建好的页面（frontend/dist），也没有 Node.js。请下载包含页面的发布包，或安装 Node.js LTS 后重试：https://nodejs.org/"
    fi
  else
    lock_hash="$(sha256_of package-lock.json)"
    if [ ! -d node_modules ] || [ "$(cat node_modules/.qianyue-lock-hash 2>/dev/null || true)" != "$lock_hash" ]; then
      echo "安装锁文件指定的前端依赖……"
      if [ "$use_mirror" = 0 ] || ! npm ci --no-audit --no-fund --registry="$npm_mirror"; then
        [ "$use_mirror" = 0 ] || echo "国内镜像（npmmirror）下载失败，改用官方 npm 源重试……"
        npm ci --no-audit --no-fund || die "前端依赖没有装成功，见上面的报错。"
      fi
      printf '%s\n' "$lock_hash" > node_modules/.qianyue-lock-hash
    fi
    echo "构建前端……"
    npm run build || die "前端构建失败，见上面的报错。"
    source_hash > dist/.build-hash
  fi
fi

# 3. Chrome：只有从招聘平台采集岗位时用得到，没有也照常启动。
if [ ! -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ] \
  && ! command -v google-chrome >/dev/null 2>&1 \
  && ! command -v chromium >/dev/null 2>&1; then
  echo "提示：没有找到 Chrome。从招聘平台采集岗位需要它，也可以直接在岗位页粘贴 JD。"
  echo "      下载：https://www.google.com/chrome/"
fi

# 4. 后端依赖：默认从国内镜像装（Python 用 npmmirror，依赖包用阿里云），失败了再回官方源。
# 镜像这一步先导出依赖清单再装，装的是锁文件里同样的版本（带哈希校验），锁文件本身不改；
# 之后再启动时环境已经齐了，不会再去联网。
cd "$root/backend"
installed=0
if [ "$use_mirror" = 1 ]; then
  requirements="$(mktemp)"
  # 环境已经有 Python 就不再建，免得每次启动都重写它。
  if { [ -x .venv/bin/python ] \
      || UV_PYTHON_INSTALL_MIRROR="${UV_PYTHON_INSTALL_MIRROR:-$python_mirror}" uv venv --allow-existing --quiet; } \
    && uv export --frozen --no-emit-project --quiet -o "$requirements" \
    && uv pip sync --index-url "$pypi_mirror" "$requirements"; then
    installed=1
  else
    echo "国内镜像下载失败，改用官方源重试……"
  fi
  rm -f "$requirements"
fi
if [ "$installed" = 0 ]; then
  uv sync --frozen \
    || die "国内镜像和官方源都没有下载成功。请检查网络后重新运行；已经下载的部分会保留，不用从头再来。"
fi

# 5. 检索模型由后端启动后在后台下载。默认用 hf-mirror.com，它连不上才走 huggingface.co。
# 自己设过 HF_ENDPOINT 的不动。
if [ "$use_mirror" = 1 ] && [ -z "${HF_ENDPOINT:-}" ] && curl -s -o /dev/null -m 3 --head "$hf_mirror"; then
  export HF_ENDPOINT="$hf_mirror"
fi

# 6. 配置和启动。模型 Key 可以先不填，在页面「设置」里填完立即生效。
if [ ! -f .env ]; then
  cp .env.example .env
  echo "已经从 .env.example 复制出 backend/.env。模型 Key 可以在页面的「设置」里填。"
fi

echo "启动迁跃：打开 http://127.0.0.1:${port}（Ctrl+C 停止）"
exec uv run --frozen --no-sync uvicorn app.main:app --host 127.0.0.1 --port "$port"
