"""联网：搜索和读取公开网页。只做普通的后台 HTTP 请求，不登录、不处理验证码。

搜索服务在设置页里选（也可以写在 backend/.env 的 QIANYUE_SEARCH_PROVIDER）。ddgs 不用 Key，
是默认；其余几家要用户自己的 Key。搜索接口没有像模型那样的统一格式（OpenAI 兼容），每家的
地址、鉴权方式、参数名和返回结构都不一样，所以下面 PROVIDERS 里一家一条，各自换算成
title、href、body 三项。需要 Key 的六家服务尚待验证成功请求的返回结果。
"""

import codecs
import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlparse

import httpx
from lxml import html

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
MAX_PAGE_CHARS = 1_000_000
MAX_PAGE_REDIRECTS = 5
MIN_READABLE_CHARS = 200  # 正文比这还短，多半是登录页、验证页或空壳页面
SEARCH_ATTEMPTS = 3


class WebError(Exception):
    """搜索或读取失败。信息是给模型和用户看的原因，不带技术细节。"""


class SearchKeyRejected(WebError):
    """搜索服务拒绝当前凭据或额度。"""


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str


class WebClient(Protocol):
    def search(self, query: str, limit: int = 5) -> list[SearchHit]: ...

    def fetch(self, url: str) -> Page: ...


def _ddgs_search(query: str, limit: int) -> list[dict]:
    from ddgs import DDGS  # 用到时才导入

    return DDGS(timeout=15).text(query, max_results=limit)


# 这些状态码表示 Key 不对或额度用完。Brave 对无效 Key 回 422
KEY_REJECTED = (401, 402, 403, 422)


@dataclass(frozen=True)
class SearchProvider:
    name: str
    url: str
    method: str  # POST 时参数放 JSON 正文，GET 时放查询串
    auth: Callable[[str], dict[str, str]]  # Key → 请求头
    params: Callable[[str, int], dict]  # (搜索词, 条数) → 参数
    results: Callable[[dict], list[dict]]  # 返回的 JSON → [{title, href, body}]

    @property
    def lead(self) -> str:
        """放在句首接中文时用：英文名后面空一格。"""
        return self.name + (" " if self.name.isascii() else "")


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _items(value) -> list[dict]:
    return [item for item in value or [] if isinstance(item, dict)]


def _hit(title, href, body) -> dict:
    return {"title": title or "", "href": href, "body": body or ""}


PROVIDERS: dict[str, SearchProvider] = {
    # https://open.bochaai.com 文档：结果在 data.webPages.value[]，summary 是长摘要
    "bocha": SearchProvider(
        name="博查",
        url="https://api.bochaai.com/v1/web-search",
        method="POST",
        auth=_bearer,
        params=lambda query, limit: {"query": query, "count": limit, "summary": True},
        results=lambda data: [
            _hit(item.get("name"), item.get("url"), item.get("summary") or item.get("snippet"))
            for item in _items(((data.get("data") or {}).get("webPages") or {}).get("value"))
        ],
    ),
    # https://docs.bigmodel.cn 网络搜索：search_std 是智谱基础版，结果在 search_result[]
    "zhipu": SearchProvider(
        name="智谱",
        url="https://open.bigmodel.cn/api/paas/v4/web_search",
        method="POST",
        auth=_bearer,
        params=lambda query, limit: {
            "search_query": query[:70],  # 文档建议不超过 70 个字
            "search_engine": "search_std",
            "search_intent": False,
            "count": limit,
        },
        results=lambda data: [
            _hit(item.get("title"), item.get("link"), item.get("content"))
            for item in _items(data.get("search_result"))
        ],
    ),
    # https://docs.tavily.com：结果在 results[]
    "tavily": SearchProvider(
        name="Tavily",
        url="https://api.tavily.com/search",
        method="POST",
        auth=_bearer,
        params=lambda query, limit: {"query": query, "max_results": limit},
        results=lambda data: [
            _hit(item.get("title"), item.get("url"), item.get("content"))
            for item in _items(data.get("results"))
        ],
    ),
    # https://api-dashboard.search.brave.com 文档：GET，Key 放在 X-Subscription-Token，
    # 结果在 web.results[]
    "brave": SearchProvider(
        name="Brave",
        url="https://api.search.brave.com/res/v1/web/search",
        method="GET",
        auth=lambda key: {"X-Subscription-Token": key, "Accept": "application/json"},
        params=lambda query, limit: {"q": query, "count": limit},
        results=lambda data: [
            _hit(item.get("title"), item.get("url"), item.get("description"))
            for item in _items((data.get("web") or {}).get("results"))
        ],
    ),
    # https://serper.dev（Google 搜索结果）：Key 放在 X-API-KEY，结果在 organic[]
    "serper": SearchProvider(
        name="Serper",
        url="https://google.serper.dev/search",
        method="POST",
        auth=lambda key: {"X-API-KEY": key},
        params=lambda query, limit: {"q": query, "num": limit},
        results=lambda data: [
            _hit(item.get("title"), item.get("link"), item.get("snippet"))
            for item in _items(data.get("organic"))
        ],
    ),
    # https://docs.exa.ai：要求返回 highlights（和搜索词相关的几段原文）当摘要
    "exa": SearchProvider(
        name="Exa",
        url="https://api.exa.ai/search",
        method="POST",
        auth=lambda key: {"x-api-key": key},
        params=lambda query, limit: {
            "query": query,
            "numResults": limit,
            "contents": {"highlights": True},
        },
        results=lambda data: [
            _hit(item.get("title"), item.get("url"), " ".join(item.get("highlights") or []))
            for item in _items(data.get("results"))
        ],
    ),
}


def provider_search(
    provider: SearchProvider, key: str, transport: httpx.BaseTransport | None = None
) -> Callable[[str, int], list[dict]]:
    """调用一家要 Key 的搜索服务。Key 不对、额度用完时，把原因直接告诉用户。"""
    name = provider.lead

    def search(query: str, limit: int) -> list[dict]:
        params = provider.params(query, limit)
        with httpx.Client(transport=transport, timeout=20, trust_env=False) as client:
            response = client.request(
                provider.method,
                provider.url,
                headers=provider.auth(key),
                **({"params": params} if provider.method == "GET" else {"json": params}),
            )
        if response.status_code in KEY_REJECTED:
            raise SearchKeyRejected(
                f"{name}拒绝了请求（{response.status_code}）：Key 不对，或者额度用完了。"
                "请在设置页检查搜索服务的 Key"
            )
        if response.status_code == 429:
            raise WebError(f"{name}说请求太频繁了，过一会儿再试")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("返回的不是 JSON 对象")
        return provider.results(data)

    return search


def search_function(
    provider: str, key: str | None, transport: httpx.BaseTransport | None = None
) -> Callable[[str, int], list[dict]]:
    """按配置选搜索服务。选了要 Key 的服务但没填 Key 时，到搜索的时候才报错。"""
    if provider == "ddgs":
        return _ddgs_search
    chosen = PROVIDERS[provider]
    if not (key or "").strip():

        def missing(query: str, limit: int) -> list[dict]:
            raise WebError(
                f"搜索服务选的是{chosen.name}，但没有填 Key。"
                "请在设置页填上 Key，或者换回 DuckDuckGo"
            )

        return missing
    return provider_search(chosen, key.strip(), transport)


class LiveWebClient:
    """真实联网。"""

    def __init__(
        self,
        search_fn: Callable[[str, int], list[dict]] = _ddgs_search,
        transport: httpx.BaseTransport | None = None,
        resolve: Callable[[str], list[str]] | None = None,
    ) -> None:
        self._search_fn = search_fn
        self._transport = transport
        self._resolve = resolve or _resolve_host

    def use_search(self, search_fn: Callable[[str, int], list[dict]]) -> None:
        """换一个搜索服务（设置页改了以后）。"""
        self._search_fn = search_fn

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        for _ in range(SEARCH_ATTEMPTS):
            try:
                results = self._search_fn(query, limit)
            except WebError:  # Key 不对、没填 Key：再试也一样，直接把原因交出去
                raise
            except Exception:  # noqa: BLE001  ddgs 对「没结果」「被限流」「超时」抛的异常各不相同
                continue
            hits = [
                SearchHit(item.get("title") or "", item["href"], item.get("body") or "")
                for item in results
                if item.get("href")
            ]
            if hits:
                return hits
        raise WebError("没有搜到结果，或搜索服务暂时不可用")

    def fetch(self, url: str) -> Page:
        def validate_public_url(candidate: str) -> tuple[httpx.URL, str, str]:
            parsed = urlparse(candidate)
            try:
                port = parsed.port
                original = httpx.URL(candidate)
            except ValueError:
                raise WebError("不是有效的网页链接") from None
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise WebError("不是有效的网页链接")
            # 每一跳都重新解析和检查，并把连接地址固定为本次检查过的 IP，避免 DNS 重绑定。
            addresses = self._resolve(parsed.hostname)
            if not addresses or any(_is_internal(address) for address in addresses):
                raise WebError("这个链接不是公网上的网页")
            target = original.copy_with(host=addresses[0])
            host_header = original.raw_host.decode("ascii")
            if ":" in parsed.hostname:
                host_header = f"[{host_header}]"
            if port and port != (443 if parsed.scheme == "https" else 80):
                host_header += f":{port}"
            return target, host_header, parsed.hostname, original.raw_host.decode("ascii")

        current_url = url
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=15,
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT},
                default_encoding=_declared_encoding,  # 响应头没写编码时，看网页自己的声明
            ) as client:
                for redirect_count in range(MAX_PAGE_REDIRECTS + 1):
                    target_url, host_header, tls_name, tls_name_ascii = validate_public_url(
                        current_url
                    )
                    request = client.build_request(
                        "GET",
                        target_url,
                        headers={"Host": host_header},
                        extensions={"sni_hostname": tls_name_ascii.encode("ascii")}
                        if urlparse(current_url).scheme == "https" and not _is_ip_address(tls_name)
                        else {},
                    )
                    response = client.send(request)
                    if response.status_code not in (301, 302, 303, 307, 308):
                        break
                    location = response.headers.get("location")
                    if not location:
                        break
                    if redirect_count == MAX_PAGE_REDIRECTS:
                        raise WebError("网页重定向次数过多")
                    current_url = urljoin(current_url, location)
        except httpx.HTTPError:
            raise WebError("网页打不开或超时") from None
        if response.status_code >= 400:
            raise WebError(f"网页返回 {response.status_code}，可能需要登录或拒绝程序访问")
        if "html" not in response.headers.get("content-type", ""):
            raise WebError("不是网页（可能是 PDF 或文件），无法读取")

        title, text = extract_text(response.text[:MAX_PAGE_CHARS])
        if len(text) < MIN_READABLE_CHARS:
            raise WebError("页面没有可读的正文，可能需要登录或有访问验证")
        return Page(url=current_url, title=title, text=text)


_INTERNAL_NETWORKS = [
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
]


def _is_ip_address(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


def _is_internal(address: str) -> bool:
    """是不是本机或内网地址。

    不用 ipaddress 的 is_global 判断：开着代理软件时，公网域名常被解析成 198.18.x.x
    这类保留地址，那样会把正常网页也拦掉。
    """
    ip = ipaddress.ip_address(address)
    # ::ffff:192.168.1.1 这种写法其实是 IPv4 地址，按 IPv4 判断，否则内网地址会漏过去
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
        or any(ip in network for network in _INTERNAL_NETWORKS)
    )


def _resolve_host(hostname: str) -> list[str]:
    try:
        return [info[4][0] for info in socket.getaddrinfo(hostname, None)]
    except OSError:
        return []


def _declared_encoding(content: bytes) -> str:
    """网页在 <meta> 里声明的编码。不少中文网站用 GBK 而且响应头不写，默认按 UTF-8。"""
    match = re.search(rb"charset=[\"']?([\w-]+)", content[:2048], re.IGNORECASE)
    try:
        return codecs.lookup(match.group(1).decode("ascii")).name if match else "utf-8"
    except LookupError:
        return "utf-8"


def extract_text(content: str) -> tuple[str, str]:
    """从 HTML 里取出标题和正文文字，去掉脚本、样式和导航。"""
    try:
        tree = html.fromstring(content)
    except Exception:  # noqa: BLE001  内容不是合法 HTML
        return "", ""
    for element in tree.xpath("//script|//style|//noscript|//nav|//header|//footer|//form"):
        element.drop_tree()
    title = " ".join(tree.xpath("string(//title)").split())
    body = tree.find(".//body")
    content_root = body if body is not None else tree
    lines = [" ".join(line.split()) for line in content_root.text_content().splitlines()]
    return title, "\n".join(line for line in lines if line)
