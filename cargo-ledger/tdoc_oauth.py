"""腾讯文档开放平台 OAuth2 客户端（云托管用）。

与 tdoc_client.py 的分工必须说清楚：
  - tdoc_client.py ：本机开发用。票据由 WorkBuddy 宿主注入，脚本不持有任何凭证。
                     缺点：票据是临时的、只在本机有效，服务部署到云端就拿不到。
  - tdoc_oauth.py  ：云端用。自己持有 client_id/client_secret，用 refresh_token
                     长期续期（1 年有效，可续），不依赖宿主。

线上唯一可持续的身份来源是 OAuth 的 refresh_token，这是云托管的硬前提。

官方文档依据（2026-10 核对）：
  授权   GET https://docs.qq.com/oauth/v2/authorize
  换token GET https://docs.qq.com/oauth/v2/token
  业务   POST https://docs.qq.com/openapi/smartbook/v2/files/{fileID}/sheets/{sheetID}
         请求头必须是 Access-Token / Client-Id / Open-Id 三个，缺一报 103xx。
         Open-Id 就是换 token 时返回的 user_id。

用法：
  python tdoc_oauth.py url               打印授权链接（浏览器打开扫码）
  python tdoc_oauth.py exchange <code>   用回调里的 code 换 token 并保存
  python tdoc_oauth.py status            查看 token 状态与剩余天数
  python tdoc_oauth.py refresh           手动刷新
  python tdoc_oauth.py test              用 OAuth 身份真实读一次表格

凭证来源优先级：环境变量 > config.json 的 oauth 段。
  环境变量：TDOC_CLIENT_ID / TDOC_CLIENT_SECRET / TDOC_REDIRECT_URI
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
TOKEN_PATH = HERE / ".tdoc_tokens.json"

# 发布时是整目录上传，会覆盖代码目录；但运行时写进 /workspace 的文件不随发布
# 消失。所以 token 双写，读的时候 /workspace 优先，这样运营在网页上重贴一次
# token 就能跨版本保留，不用我每次重新部署。
WORKSPACE_DIR = Path("/workspace")


def _token_paths() -> list:
    """读取顺序：凭证目录 → 历史遗留位置 → 项目目录（出厂凭证，只读兜底）。

    TOKEN_PATH 永远排第一。早期版本把 /workspace/.tdoc_tokens.json 放在前面，
    结果项目里那个"顺手写的副本"会盖住真正的凭证目录：新贴的 token 存进了
    .cargo-ledger/，读的时候却还去根目录捞旧的那份，表现就是"已保存，但读表失败"。

    第三项是给"网页上不提供凭证录入"用的：把 token 文件随代码一起发布，
    线上启动就能读到，运营侧完全无感。它只参与读取，不参与写入——
    否则每次运行都会把 token 写回将要被上传的目录。
    """
    out = [TOKEN_PATH]
    for extra in (WORKSPACE_DIR / ".tdoc_tokens.json", HERE / ".tdoc_tokens.json"):
        try:
            if extra not in out and extra.exists():
                out.append(extra)
        except OSError:
            pass
    return out


def _token_write_paths() -> list:
    """只写主路径一份：写多了会让"该读哪份"重新变得不可预测。"""
    return [TOKEN_PATH]

AUTH_URL = "https://docs.qq.com/oauth/v2/authorize"
TOKEN_URL = "https://docs.qq.com/oauth/v2/token"

# 两套并存的接口形态。腾讯文档把智能表格的 API 从「动作打在 body 里」改成了
# RESTful（动作进 URL，如 /fields:list），旧的 smartbook 路径没有下线公告，
# 但新应用可能只认新形态。两种的**请求体完全相同**，只有 method 和路径不同，
# 所以切换成本极低——做成自动回退，不用赌哪一套。
API_BASE = "https://docs.qq.com/openapi/smartbook/v2"       # 智能表格业务接口
SMARTSHEET_BASE = "https://docs.qq.com/openapi/smartsheet/v2"  # 另一套形态（本环境实测 404）
DRIVE_BASE = "https://docs.qq.com/openapi/drive/v2"           # 文件管理接口

# ★ 踩过的坑（2026-10-29 实测）：同一个文档有两套 ID，用途不同，混用必报 200063。
#
#   短 ID        XabC123DeFg              只被 drive 接口认（能查 metadata）
#   URL 里的 ID  DabC123DeFgURL           文档链接 /smartsheet/DabC123...，smartbook 当成 folder → 10202
#   内部 ID      300000000$XabC123DeFg    ★ smartsheet/smartbook 业务接口只认这个
#
# 所以配置里填人类友好的短 ID 就行，发请求前用 drive metadata 换成内部 ID。
# 200063 就是这么来的：认证、权限都没问题（metadata 一直 ret=0，isOwner=true），
# 纯粹是 ID 形态不对——这也是为什么当时 msg 是空的，网关根本没进到业务层。
#
# 也就是说：200063 的真实语义是「fileID 形态不适用于该接口」，
# 至少比"没权限"更常见，别再往权限方向查了。
_resolved_fid: dict = {}


def resolve_file_id(file_id: str) -> str:
    """短 ID → 业务接口认的内部 ID（形如 300000000$xxxx）。已含 $ 的原样返回。"""
    fid = (file_id or "").strip()
    if not fid or "$" in fid:
        return fid
    if fid in _resolved_fid:
        return _resolved_fid[fid]
    try:
        r = _raw("GET", f"{DRIVE_BASE}/files/{urllib.parse.quote(fid, safe='')}/metadata")
    except OAuthError:
        return fid
    if str(r.get("ret") or 0) != "0":
        return fid
    inner = _data_of(r).get("ID") or ""
    if inner:
        _resolved_fid[fid] = inner
        return inner
    return fid

_REST_ROUTES = {
    "getFields":     ("POST", "/fields:list"),
    "getRecords":    ("POST", "/records:list"),
    "addRecords":    ("POST", "/records"),
    "updateRecords": ("POST", "/records"),
    "deleteRecords": ("POST", "/records"),
}

# 已经探明的形态。进程内记住，避免每次调用都多打一个必失败的请求。
_API_STYLE = os.environ.get("TDOC_API_STYLE", "").strip()  # ""=未知 → 先旧后新
_DEAD_STYLES: set = set()   # 实测 404 的形态，本进程内不再尝试

# 这些业务码代表「这个请求根本没被当成一次有效操作」，换另一种形态重试是安全的。
# 参数错（400001）只对读操作回退，写操作不回退——宁可报错也不冒重复写入的风险。
_RETRY_RETS_READ = {"200063", "400001", "400003", "400005", "400008"}
_RETRY_RETS_WRITE = {"200063", "400003"}
_WRITE_ACTIONS = {"addRecords", "updateRecords", "deleteRecords"}

# access_token 官方有效期 30 天（expires_in=2592000）。提前 1 小时刷新，
# 避免"刚好在两次请求之间过期"这种偶发失败。
REFRESH_AHEAD_SEC = 3600


class OAuthError(RuntimeError):
    """ret 单独留一个属性：调用方要按业务码决定"换端点重试"还是"直接报错"。"""

    def __init__(self, msg: str, ret=None):
        super().__init__(msg)
        self.ret = None if ret is None else str(ret)


# ---------------------------------------------------------------- 配置与凭证

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def oauth_conf() -> dict:
    """client_id / client_secret / redirect_uri 的最终取值。"""
    cfg = (load_config().get("oauth") or {})
    return {
        "client_id": os.environ.get("TDOC_CLIENT_ID") or cfg.get("client_id", ""),
        "client_secret": os.environ.get("TDOC_CLIENT_SECRET") or cfg.get("client_secret", ""),
        "redirect_uri": os.environ.get("TDOC_REDIRECT_URI") or cfg.get("redirect_uri", ""),
    }


def _require_client() -> dict:
    """业务调用只需要 client_id（它和 Access-Token、Open-Id 一起构成请求头）。"""
    c = oauth_conf()
    if not c["client_id"]:
        raise OAuthError(
            "缺少 client_id。去 https://docs.qq.com/open/developers/ 建应用后拿到，"
            "填进 config.json 的 oauth 段，或设环境变量 TDOC_CLIENT_ID。"
        )
    return c


def _require_app() -> dict:
    """换 code / 刷新 token 才必须 client_secret —— 官方文档写明这两步 secret 必填。"""
    c = _require_client()
    if not c["client_secret"]:
        raise OAuthError(
            "缺少 client_secret，无法换 token 或续期。"
            "手动贴 access_token 可以绕过这一步（python tdoc_oauth.py adopt），"
            "但 token 到期后仍需新值。"
        )
    return c


def load_token() -> dict:
    """多份候选里取"最新的那份"，而不是"第一个存在的"。

    为什么要比时间：网页端不再提供 token 录入，续期只能靠"把新的 token 文件
    随代码发布上去"。如果死守路径顺序，包内那份永远会被 /workspace 里的旧文件
    盖住，发新包也不生效。按 obtained_at 取最新，两个方向都对：
      - 回填了新 token 的发布包 → 时间更新 → 生效
      - 运行时 OAuth 刷新写入主路径 → 时间更新 → 生效
    """
    best, best_ts = {}, -1
    for p in _token_paths():
        try:
            with open(p, encoding="utf-8") as f:
                tok = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if not tok.get("access_token"):
            continue
        ts = int(tok.get("obtained_at") or 0)
        if ts >= best_ts:
            best, best_ts = tok, ts
    return best


def save_token(tok: dict) -> None:
    """令牌落盘。权限收窄到 600，避免同机其他账号读到 client_secret 换来的凭证。

    写多份：任一份能读就算有凭证，避免某次发布把唯一那份覆盖掉。
    """
    wrote = []
    for p in _token_write_paths():
        tmp = p.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(tok, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass  # Windows 上 chmod 语义有限，忽略
            tmp.replace(p)
            wrote.append(p)
        except OSError:
            continue
    if not wrote:
        raise OAuthError("token 写入失败：所有候选路径都不可写")


# ---------------------------------------------------------------- OAuth 流程

def build_auth_url(state: str = "") -> str:
    c = _require_app()
    if not c["redirect_uri"]:
        raise OAuthError(
            "缺少 redirect_uri。它必须是 HTTPS，且要和开放平台后台白名单里的地址"
            "完全一致（含路径、末尾不要多斜杠），否则授权后回调会被拒。"
        )
    q = {
        "client_id": c["client_id"],
        "redirect_uri": c["redirect_uri"],
        "new_login": "1",          # 官方要求固定填 1，用新版授权组件
        "response_type": "code",
        "scope": "all",            # 官方要求固定 all，实际权限靠应用后台勾选的 scope
    }
    if state:
        q["state"] = state
    return f"{AUTH_URL}?{urllib.parse.urlencode(q)}"


def _http_get_json(url: str, headers: dict | None = None, data: bytes | None = None,
                   method: str = "GET") -> dict:
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise OAuthError(f"HTTP {e.code}: {body[:400]}")
    except urllib.error.URLError as e:
        raise OAuthError(f"网络错误: {e.reason}")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise OAuthError(f"返回不是 JSON: {body[:400]}")


def exchange_code(code: str) -> dict:
    """用授权码换 token。code 10 分钟内有效、只能用一次，超时只能重新授权。"""
    c = _require_app()
    if not c["redirect_uri"]:
        raise OAuthError("缺少 redirect_uri，必须与发起授权时完全一致")
    q = urllib.parse.urlencode({
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "redirect_uri": c["redirect_uri"],
        "grant_type": "authorization_code",
        "code": code.strip(),
    })
    d = _http_get_json(f"{TOKEN_URL}?{q}")
    if "access_token" not in d:
        raise OAuthError(f"换 token 失败: {json.dumps(d, ensure_ascii=False)[:400]}")
    tok = {
        "access_token": d["access_token"],
        "refresh_token": d.get("refresh_token", ""),
        # user_id 就是 Open-Id，调用业务接口时必须带上，官方文档明确写了这一点
        "open_id": d.get("user_id", ""),
        "expires_at": int(time.time()) + int(d.get("expires_in") or 0),
        "expires_in": int(d.get("expires_in") or 0),
        "scope": d.get("scope", ""),
        "obtained_at": int(time.time()),
    }
    save_token(tok)
    return tok


def refresh_token(force: bool = False) -> dict:
    """用 refresh_token 续期。

    注意：腾讯文档刷新时可能下发新的 refresh_token。如果沿用旧的，下一次刷新
    就会失败——而且失败时距上次刷新可能已过 29 天，等于静默失效。
    所以这里只要新值存在就覆盖保存，不管是否相同。
    """
    c = _require_app()
    tok = load_token()
    rt = tok.get("refresh_token")
    if not rt:
        raise OAuthError("没有 refresh_token，需要重新走一次授权（python tdoc_oauth.py url）")
    q = urllib.parse.urlencode({
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "grant_type": "refresh_token",
        "refresh_token": rt,
    })
    d = _http_get_json(f"{TOKEN_URL}?{q}")
    if "access_token" not in d:
        raise OAuthError(
            "刷新失败（refresh_token 可能已过期或已被吊销，有效期 1 年）: "
            + json.dumps(d, ensure_ascii=False)[:400]
        )
    new = {
        "access_token": d["access_token"],
        "refresh_token": d.get("refresh_token") or rt,  # 没下发新值就沿用旧的
        "open_id": d.get("user_id") or tok.get("open_id", ""),
        "expires_at": int(time.time()) + int(d.get("expires_in") or 0),
        "expires_in": int(d.get("expires_in") or 0),
        "scope": d.get("scope") or tok.get("scope", ""),
        "obtained_at": int(time.time()),
        "refreshed_at": int(time.time()),
    }
    save_token(new)
    return new


def ensure_token() -> dict:
    """业务调用前统一走这里。

    手填的 token（没有 refresh_token）只要没过期就照用，过期了给出明确的重贴指引，
    而不是抛一个"刷新失败"让人误以为是网络问题。
    """
    tok = load_token()
    if not tok.get("access_token"):
        raise OAuthError(
            "未授权。两条路：python tdoc_oauth.py adopt 贴现成 token，"
            "或 python tdoc_oauth.py url 走完整授权"
        )
    remain = tok.get("expires_at", 0) - time.time()
    if not tok.get("refresh_token"):
        # 手填的 token 不做时间预判。后台没有明确到期时间，我按官方示例假定的
        # 3 天可能远比实际短——提前判死会让人白重贴一次，而过期时接口一定会
        # 返回 10301，那时再提示重贴才是准确信号。
        return tok
    if remain > REFRESH_AHEAD_SEC:
        return tok
    return refresh_token()


# ---------------------------------------------------------------- 业务接口

def _raw(method: str, url: str, body=None) -> dict:
    """原样发一次请求，连 HTTP 码、traceID、响应体都留下来。

    诊断必须看到原文：`ret=200063 msg=（空）` 这种回包本身信息量是零，
    真正能拿去查客服或对比的是 X-Trace-Id 和完整 body。
    """
    c = _require_client()
    tok = ensure_token()
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Access-Token", tok["access_token"])
    req.add_header("Client-Id", c["client_id"])
    req.add_header("Open-Id", tok.get("open_id", ""))
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")

    out = {"http": 0, "ret": None, "msg": None, "trace": "", "body": ""}
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out["http"] = r.status
            hdrs = dict(r.headers or {})
            out["body"] = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        out["http"] = e.code
        hdrs = dict(e.headers or {})
        out["body"] = e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        out["error"] = f"网络错误: {e.reason}"
        return out
    for k, v in hdrs.items():
        if k.lower() in ("x-trace-id", "x-traceid", "traceid"):
            out["trace"] = v
    try:
        js = json.loads(out["body"])
    except (json.JSONDecodeError, TypeError):
        js = {}
    if isinstance(js, dict):
        out["ret"] = js.get("ret")
        out["msg"] = js.get("msg")
        if js.get("code") is not None and out["ret"] is None:
            # v3 接口用的是 code/message 而不是 ret/msg
            out["ret"] = js.get("code")
            out["msg"] = js.get("message")
    return out


def _call_style(file_id: str, sheet_id: str, action: str, params: dict,
                style: str) -> dict:
    body = {action: params}
    fid = resolve_file_id(file_id)
    if style == "smartsheet":
        method, suffix = _REST_ROUTES.get(action, ("POST", ""))
        url = f"{SMARTSHEET_BASE}/files/{fid}/sheets/{sheet_id}{suffix}"
    else:
        method, url = "POST", f"{API_BASE}/files/{fid}/sheets/{sheet_id}"
    raw = _raw(method, url, body)
    if raw.get("error"):
        raise OAuthError(raw["error"])
    ret = raw["ret"]
    if raw["http"] >= 400 or (ret not in (0, "0", None)):
        msg = raw["msg"] or ""
        if raw["http"] == 401 or (ret is not None and str(ret).startswith("103")):
            detail = (
                "鉴权被拒。依次检查：\n"
                "  1) access_token 是否已过期 → 后台复制新的，再保存一次\n"
                "  2) open_id 是否属于这个 client_id → 两者必须来自同一应用\n"
                "  3) 应用是否勾了「智能表格」权限（scope.smartsheet）\n"
                f"  原始返回：http={raw['http']} ret={ret} msg={msg}"
            )
        else:
            detail = _explain_ret(ret if ret is not None else raw["http"], msg, action)
        if raw["trace"]:
            detail += f"\n  traceID={raw['trace']}"
        detail += f"\n  endpoint={style} {method} {url.split('docs.qq.com', 1)[-1]}"
        raise OAuthError(detail, ret=ret if ret is not None else raw["http"])
    return _data_of(raw)


def _data_of(raw: dict) -> dict:
    try:
        js = json.loads(raw["body"])
    except (json.JSONDecodeError, TypeError, KeyError):
        return {}
    return (js or {}).get("data") or {}


def _call(file_id: str, sheet_id: str, action: str, params: dict,
          style: str = "") -> dict:
    """统一调用。action 就是文档里的 getRecords / getFields / updateRecords 这类键名。

    形态未知时先按旧的 smartbook 打一次，失败再换 RESTful 重试，成功那次记住。
    读操作可以放心重试；写操作只在"请求明显没生效"的错误码上重试。
    """
    global _API_STYLE
    if style:
        return _call_style(file_id, sheet_id, action, params, style)

    retry: set = (_RETRY_RETS_WRITE if action in _WRITE_ACTIONS
                  else _RETRY_RETS_READ)
    order = [_API_STYLE] if _API_STYLE in ("smartbook", "smartsheet") else \
            ["smartbook", "smartsheet"]
    order = [s for s in order if s not in _DEAD_STYLES] or order[:1]
    last = None
    for i, st in enumerate(order):
        try:
            d = _call_style(file_id, sheet_id, action, params, st)
        except OAuthError as e:
            last = e
            # 404/路径不存在 = 这套接口在本环境根本没挂，别再来回试
            if e.ret in ("404", 404) or "404" in str(e).splitlines()[0]:
                _DEAD_STYLES.add(st)
            can_retry = e.ret in retry and i < len(order) - 1
            if can_retry:
                continue
            raise
        _API_STYLE = st      # 这一套能跑通，后面都走它
        return d
    raise last  # type: ignore[misc]


def probe(file_id: str, sheet_id: str) -> dict:
    """二分定位：先确认 token 有效且拿到了正确的 fileID，再看能不能读子表。

    ret 相同不代表原因相同，所以每一步都留 http / ret / msg / traceID，
    让人一眼看出是哪一层断的，而不是笼统地说"读表失败"。
    """
    c = _require_client()
    steps: list = []

    def run(label: str, method: str, url: str, body=None, expect: str = ""):
        try:
            r = _raw(method, url, body)
        except OAuthError as e:
            r = {"http": 0, "ret": None, "msg": str(e), "trace": "", "body": "",
                 "error": str(e)}
        ok = (r.get("http") == 200 and str(r.get("ret") or 0) == "0")
        steps.append({
            "label": label,
            "endpoint": f"{method} {url.split('docs.qq.com', 1)[-1]}",
            "ok": ok,
            "http": r.get("http"),
            "ret": r.get("ret"),
            "msg": r.get("msg") or "",
            "trace": r.get("trace") or "",
            "expect": expect,
            "body": (r.get("body") or "")[:600],
            "error": r.get("error") or "",
        })
        return r if ok else None

    # ⓪ 用文件管理接口验 token：这一步不依赖 sheetID，能干净地分开
    #    "token 不行" / "fileID 形态不对" / "表格接口没权限" 三件事。
    meta = run("⓪ 文档元信息（drive 接口）", "GET",
               f"{DRIVE_BASE}/files/{urllib.parse.quote(file_id.strip(), safe='')}/metadata",
               expect="验 token 有效；顺便拿到业务接口要的内部 fileID")
    inner_id = ""
    doc = {}
    if meta:
        doc = _data_of(meta) or {}
        inner_id = doc.get("ID") or ""

    # 业务接口只认内部 ID（300000000$xxx），短 ID 会得到 200063
    fid = inner_id or resolve_file_id(file_id)

    run("① 列子表（业务接口 + 内部 ID）", "GET",
        f"{API_BASE}/files/{urllib.parse.quote(fid, safe='')}/sheets",
        expect="验内部 fileID 能否进入智能表格业务层")
    run("② 查字段（商品/订单子表）", "POST",
        f"{API_BASE}/files/{urllib.parse.quote(fid, safe='')}/sheets/{sheet_id}",
        {"getFields": {"offset": 0, "limit": 10}}, expect="验 sheetID + 读字段权限")
    run("③ 查记录", "POST",
        f"{API_BASE}/files/{urllib.parse.quote(fid, safe='')}/sheets/{sheet_id}",
        {"getRecords": {"offset": 0, "limit": 5}}, expect="验读记录权限（真正干活那一步）")

    good = [s["label"] for s in steps if s["ok"]]
    working_style = "smartbook" if any(s["ok"] for s in steps[1:]) else ""
    advise = ""
    if not steps[0]["ok"]:
        advise = ("文档元信息都读不到 → 先解决 token：它可能已过期，或 open_id 与 "
                  "client_id 不是同一次授权得到的。")
    elif not working_style:
        advise = ("token 有效（元信息读到了），但业务接口全部失败 → 大概率是这个应用"
                  "还没有「智能表格」权限（审核未通过时 scope 不生效），把 traceID 发给"
                  "开放平台客服问。")
    return {
        "file_id": file_id,
        "resolved_file_id": fid,
        "id_changed": bool(inner_id and inner_id != file_id.strip()),
        "title": doc.get("title", ""),
        "doc_type": doc.get("type", ""),
        "is_owner": doc.get("isOwner"),
        "sheet_id": sheet_id,
        "client_id": (c["client_id"][:6] + "…") if c["client_id"] else "",
        "open_id": (ensure_token().get("open_id") or ""),
        "working_style": working_style,
        "passed": good,
        "advise": advise,
        "steps": steps,
    }


# 这几个是实测/文档里能确认语义的。认不出的照原样透出，不猜。
_RET_HINTS = {
    "10301": (
        "access_token 无效或已过期（Token check error）。\n"
        "  去开放平台后台复制当前的 access_token，执行：python tdoc_oauth.py adopt --token <新值>"
    ),
    "10303": (
        "Open-Id 缺失或不属于这个 client_id。\n"
        "  两个值必须来自同一次授权：python tdoc_oauth.py adopt --token <token> --open-id <open_id>"
    ),
    # 200063 是 2026-10-29 实测确认的：认证和权限都没问题（同一 token 查
    # drive metadata 一路 ret=0、isOwner=true），纯粹是 fileID 形态不对——
    # 智能表格业务接口只认内部 ID（300000000$xxx），给短 ID 就是 200063，
    # 给文档链接里的 Dxxx 会变成 10202 Folder not exists。msg 为空是因为
    # 请求压根没进业务层。
    "200063": (
        "fileID 形态不对（不是权限问题）。\n"
        "  智能表格接口要内部 ID，形如 300000000$XabC123DeFg；\n"
        "  给短 ID（XabC123DeFg）会得到 200063，给 D 开头的链接 ID 会得到 10202。\n"
        "  代码里已自动用 drive metadata 换 ID，若仍报此错，检查文档是否被删除重建过。"
    ),
    "10202": (
        "智能表格接口把传入的 fileID 当成文件夹了：多半填了文档链接里 D 开头的 ID。\n"
        "  应使用短 ID 或内部 ID（300000000$xxx），代码会自动转换。"
    ),
    "22003": "记录不存在：recordID 写错，或这行已被删除。",
    "400001": "请求参数不合法。多为 fileID/sheetID 形态不对，或应用没开通智能表格权限。",
    "400003": "访问的资源不存在：fileID 写错、文档被删、或当前账号看不到它。",
    "400005": "不允许访问该资源：账号没有这份文档的读权限。",
}


def _explain_ret(ret, msg, action: str) -> str:
    key = str(ret)
    hint = _RET_HINTS.get(key)
    if hint:
        return f"{action} 被拒 ret={key}（{msg}）：{hint}"
    return f"{action} 失败 ret={key} msg={msg}"


# OpenAPI 的 fieldType 是数字枚举，MCP 那边是字符串。用这张表对齐，认不出的
# 保留 unknown_<n> 而不是猜一个，猜错会导致下游按错误类型解析。
FIELD_TYPE_BY_ID = {
    1: "text", 2: "number", 3: "checkbox", 4: "dateTime", 5: "image",
    8: "url", 9: "singleSelect", 10: "progress", 16: "email",
    # 17 = 单选（propertySingleSelect）。以前没登记，落到 unknown_17 的兜底
    # 分支按"文本"写。写一个已存在的选项**碰巧能成功**（服务端按文本匹配），
    # 所以这个漏洞藏了很久；等到要清空时才露出来——传空字符串报
    # ret=22013 invalid select value。单选清空必须传 null，不是 ""。
    17: "singleSelect",
    # 26 = 货币（propertyCurrency）。写入时和 number 一样给数值，只是展示带 ¥
    # 和千分位。以前没登记这个枚举，导致「行情价/推荐下单价」写进去是 None ——
    # 表格里的金额列一直是空的，而接口返回 ret=0，看着像成功。
    26: "currency",
}


def list_fields(file_id: str, sheet_id: str) -> list:
    """返回结构与 MCP 版对齐：field_id / field_title / field_type。"""
    out, offset = [], 0
    while True:
        d = _call(file_id, sheet_id, "getFields", {"offset": offset, "limit": 100})
        g = d.get("getFields") or {}
        for f in g.get("fields") or []:
            ft = f.get("fieldType")
            out.append({
                "field_id": f.get("fieldID"),
                "field_title": f.get("fieldTitle"),
                "field_type": FIELD_TYPE_BY_ID.get(ft, f"unknown_{ft}"),
                "_raw": f,
            })
        if not g.get("hasMore"):
            break
        offset += 100
    return out


def _oconv_value(v):
    """把 OpenAPI 的 values 单项还原成 python 值（与 tdoc_client.read_value 同语义）。"""
    if v is None:
        return None
    if isinstance(v, (int, float, bool)):
        return v
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        texts = []
        for it in v:
            if not isinstance(it, dict):
                texts.append(it)
                continue
            t = it.get("text") or it.get("title") or it.get("url") or it.get("id")
            if t is not None:
                texts.append(t)
        if not texts:
            return None
        return texts[0] if len(texts) == 1 else texts
    if isinstance(v, dict):
        return v.get("text") or v.get("title") or v.get("id")
    return v


def list_records(file_id: str, sheet_id: str, limit: int = 200) -> list:
    """返回结构对齐 MCP：records[{record_id, field_values:[{field, ...}]}]。

    上游 logistics_agent.py 完全按 MCP 格式写，这里做一次归一，
    下游一行都不用改——这是"两种后端一套代码"的关键点。
    """
    out, offset = [], 0
    while True:
        d = _call(file_id, sheet_id, "getRecords", {"offset": offset, "limit": limit})
        g = d.get("getRecords") or {}
        for r in g.get("records") or []:
            fvs = []
            for title, raw in (r.get("values") or {}).items():
                fv = {"field": title}
                val = _oconv_value(raw)
                # 空值直接跳过，**不要 str(None) 变成字符串 "None"**。
                # 否则下游 `if o["product"]:` 会把一个空单元格当成有内容，
                # 空行被算进"在途单数"，地址栏显示 "None"，一路都是假数据。
                if val is None:
                    continue
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    fv["number_value"] = val
                elif isinstance(val, bool):
                    fv["bool_value"] = val
                elif isinstance(val, list):
                    fv["text_value"] = {"items": [{"text": str(x), "type": "text"} for x in val]}
                else:
                    fv["text_value"] = {"items": [{"text": str(val), "type": "text"}]}
                fvs.append(fv)
            out.append({"record_id": r.get("recordID"), "field_values": fvs})
        if not g.get("hasMore"):
            break
        offset += limit
    return out


def _to_openapi_value(fv: dict, field_type: str):
    """把 MCP 风格的 field_values 单项转成 OpenAPI 的 values 值。"""
    # currency（fieldType 26）也是数值，不能落到最后的 string_value 分支
    if field_type in ("number", "currency"):
        return fv.get("number_value")
    if field_type == "checkbox":
        return bool(fv.get("bool_value"))
    if field_type == "dateTime":
        # MCP 侧传的是毫秒时间戳字符串；OpenAPI 也收字符串毫秒
        return fv.get("string_value") or fv.get("number_value")
    for key in ("text_value", "option_value", "reference_value"):
        if key in fv and isinstance(fv[key], dict):
            items = fv[key].get("items") or []
            if field_type == "singleSelect":
                # 单选要传选项对象；只给 text 时服务端会按文本匹配已有选项
                return [{"text": i.get("text")} for i in items if isinstance(i, dict)]
            return [{"type": "text", "text": i.get("text", "")}
                    for i in items if isinstance(i, dict)]
    raw = fv.get("string_value")
    # 走到这里说明类型不在已知表里（公式列 19、物流状态 17 这类）。
    # 不能只看 string_value：调用方可能给的是 number_value，那样会静默变 None，
    # 写入被丢弃且校验也会误判"两边都是空"。
    if raw is None and fv.get("number_value") is not None:
        return fv["number_value"]
    if raw is None and fv.get("bool_value") is not None:
        return bool(fv["bool_value"])
    if raw is None:
        return None
    # ⚠️ 文本列必须传 [{"type":"text","text":...}]，不能给裸字符串。
    #
    # 踩过的坑（2026-11-01，代价是运营以为填了、表里一直是空）：
    # 直接传 `"1111111111"` 时接口返回 ret=0，还把 values 原样回显：
    #   {"records":[{"recordID":"rkrpMb","values":{"快递单号":"1111111111"}}]}
    # 看着像成功，实际一个字都没落库——那个 values 只是请求回显，不是结果。
    # 静默失败比报错危险得多：报错人会重试，静默会让人以为做完了。
    if field_type == "singleSelect":
        # 单选清空必须传 null。传 "" 会被服务端判成"这个选项不存在"：
        #   ret=22013 Smartsheet invalid select value
        # 注意它和文本列不同——文本列传 "" 就是清空，单选传 "" 是报错。
        # 同一个"空"在不同字段类型下语义不一样，别想当然。
        s = str(raw).strip()
        return [{"text": s}] if s else None
    return [{"type": "text", "text": str(raw)}]


def _norm_for_cmp(v):
    """把「我发出去的值」归一成可与读回值比较的形态。"""
    if isinstance(v, list):
        out = []
        for it in v:
            if isinstance(it, dict):
                out.append("" if it.get("text") is None else str(it.get("text")))
            else:
                out.append(str(it))
        return "".join(out)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    return "" if v is None else str(v)


def _read_back_value(fv: dict):
    """把 list_records 里的 field_value 归一成同一个形态。"""
    if fv.get("number_value") is not None:
        return fv["number_value"]
    if fv.get("bool_value") is not None:
        return fv["bool_value"]
    tv = fv.get("text_value")
    if isinstance(tv, dict):
        items = tv.get("items") or []
        return "".join(str(i.get("text") or "") for i in items if isinstance(i, dict))
    if fv.get("string_value") is not None:
        return fv["string_value"]
    return ""


def _same_value(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-9
    sa, sb = str(a if a is not None else "").strip(), str(b if b is not None else "").strip()
    if not sa and not sb:
        return True
    try:
        return abs(float(sa) - float(sb)) < 1e-9
    except (TypeError, ValueError):
        return sa == sb


def _verify_write(file_id: str, sheet_id: str, expect: dict) -> None:
    """写后读回，逐字段比对。对不上就抛错。

    为什么值得多读一次表：腾讯的 updateRecords 在值格式不对时**返回 ret=0**，
    还把 values 原样回显，看起来完全成功，实际一个字都没落库。这类失败没有
    任何信号，只能靠读回来证伪。一次写入多读一次表，比"运营以为填了、表里
    两周都是空"便宜太多。
    """
    got = {r.get("record_id"): r for r in list_records(file_id, sheet_id)}
    bad = []
    for rid, fields in expect.items():
        row = got.get(rid)
        if row is None:
            bad.append(f"{rid}: 表里找不到这条记录")
            continue
        cur = {fv.get("field"): fv for fv in (row.get("field_values") or [])}
        for title, want in fields.items():
            have = _read_back_value(cur.get(title) or {})
            if not _same_value(_norm_for_cmp(want), have):
                bad.append(f"{title}: 想写 {want!r}，读回 {have!r}")
    if bad:
        raise OAuthError("写入没有生效（接口返回成功，但表里没变）：\n  "
                         + "\n  ".join(bad[:5])
                         + "\n  这通常是值格式不对或该列不可写（公式列、关联列）。")


def update_records(file_id: str, sheet_id: str, records: list,
                   verify: bool = False) -> dict:
    """records 用 MCP 风格（record_id + field_values），这里转成 OpenAPI 的
    recordID + values（values 是「字段名 → 值」的字典，不是数组）。

    verify=True 时写后读回校验，不一致直接抛错——文本列曾因传裸字符串
    静默失败过，所有对外写入口都该带这个开关。
    """
    if not records:
        return {"updated": 0}
    types = {f["field_title"]: f["field_type"] for f in list_fields(file_id, sheet_id)}
    payload = []
    expect: dict = {}
    for r in records:
        values = {}
        for fv in r.get("field_values") or []:
            title = fv.get("field")
            if not title:
                continue
            values[title] = _to_openapi_value(fv, types.get(title, "text"))
            expect.setdefault(r.get("record_id"), {})[title] = values[title]
        payload.append({"recordID": r.get("record_id"), "values": values})
    res = _call(file_id, sheet_id, "updateRecords", {"records": payload})
    if verify:
        _verify_write(file_id, sheet_id, expect)
    return res


def delete_records(file_id: str, sheet_id: str, record_ids: list) -> dict:
    """批量删记录。参数名是 recordIDs（官方 delete_records 文档），不是 records。

    删是不可逆的，所以调用方必须先落盘备份；这里只负责发请求，不做任何兜底猜测。
    """
    ids = [i for i in (record_ids or []) if i]
    if not ids:
        return {"deleted": 0}
    _call(file_id, sheet_id, "deleteRecords", {"recordIDs": ids})
    return {"deleted": len(ids)}


def add_records(file_id: str, sheet_id: str, records: list) -> dict:
    if not records:
        return {"added": 0}
    types = {f["field_title"]: f["field_type"] for f in list_fields(file_id, sheet_id)}
    payload = []
    for r in records:
        values = {}
        for fv in r.get("field_values") or []:
            title = fv.get("field")
            if not title:
                continue
            values[title] = _to_openapi_value(fv, types.get(title, "text"))
        payload.append({"values": values})
    return _call(file_id, sheet_id, "addRecords", {"records": payload})


# ---------------------------------------------------------------- CLI

def _fmt_remain(sec: int) -> str:
    if sec <= 0:
        return "已过期"
    d, rem = divmod(int(sec), 86400)
    h = rem // 3600
    return f"{d} 天 {h} 小时"


def main(argv: list) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"

    if cmd == "setup":
        import argparse
        p = argparse.ArgumentParser(prog="tdoc_oauth.py setup")
        p.add_argument("--client-id")
        p.add_argument("--secret")
        p.add_argument("--redirect")
        ns = p.parse_args(argv[2:])
        cfg = load_config()
        o = cfg.setdefault("oauth", {})
        if ns.client_id:
            o["client_id"] = ns.client_id.strip()
        if ns.secret:
            o["client_secret"] = ns.secret.strip()
        if ns.redirect:
            r = ns.redirect.strip()
            if not r.startswith("https://"):
                print("[拒绝] 回调地址必须 https://，腾讯文档不接受 http 或本地地址", file=sys.stderr)
                return 2
            if "?" in r or "#" in r:
                print("[拒绝] 回调地址不能带查询串或锚点", file=sys.stderr)
                return 2
            o["redirect_uri"] = r
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        c = oauth_conf()
        print("已写入 config.json：")
        for k in ("client_id", "client_secret", "redirect_uri"):
            v = c[k] or ""
            shown = (v[:10] + "…") if len(v) > 10 else v
            print(f"  {k:13}= {shown or '（空）'}")
        if c["client_id"] and c["client_secret"] and c["redirect_uri"]:
            print("\n三项齐全，下一步：python tdoc_oauth.py url")
        else:
            print("\n还缺项，补齐后才能授权")
        return 0

    if cmd == "adopt":
        import argparse
        p = argparse.ArgumentParser(prog="tdoc_oauth.py adopt")
        p.add_argument("--token", required=True, help="开放平台后台里的 access_token")
        p.add_argument("--open-id", dest="open_id", help="后台里的 open_id（也可沿用上次保存的）")
        p.add_argument("--client-id", dest="client_id", help="如与 config.json 不同则一并更新")
        p.add_argument("--expires-in", dest="expires_in", type=int, default=0,
                       help="已知有效期秒数；不知道就留空（默认不猜，失效由接口 10301 反馈）")
        p.add_argument("--no-verify", action="store_true", help="跳过真实读表验证")
        ns = p.parse_args(argv[2:])

        tok = ns.token.strip()
        if len(tok) < 20:
            print(f"[拒绝] token 只有 {len(tok)} 个字符，像是没复制全", file=sys.stderr)
            return 2

        if ns.client_id:
            cfg = load_config()
            cfg.setdefault("oauth", {})["client_id"] = ns.client_id.strip()
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
                f.write("\n")

        old = load_token()
        open_id = (ns.open_id or old.get("open_id") or "").strip()
        save_token({
            "access_token": tok,
            "refresh_token": "",           # 手填的没有续期能力
            "open_id": open_id,
            "expires_at": (int(time.time()) + ns.expires_in) if ns.expires_in else 0,
            "expires_in": max(ns.expires_in, 0),
            "scope": "",
            "obtained_at": int(time.time()),
            "source": "manual-adopt",
        })
        print(f"已保存。Open-Id={open_id or '（空，调用业务接口会报 10303）'}")
        print("有效期：未知（后台没给到期时间就不猜；失效时接口返回 10301，重贴即可）"
              if not ns.expires_in else
              f"有效期：按你给的 {_fmt_remain(ns.expires_in)} 计")

        if ns.no_verify:
            return 0
        cfg = load_config().get("tdoc") or {}
        tgt = (cfg.get("product") or {})
        if not (tgt.get("file_id") and tgt.get("sheet_id")):
            tgt = (cfg.get("order") or {})
        if not (tgt.get("file_id") and tgt.get("sheet_id")):
            print("config.json 里没有可测的表，跳过验证")
            return 0
        print(f"\n真实读表验证 file={tgt['file_id']} sheet={tgt['sheet_id']} …")
        try:
            fs = list_fields(tgt["file_id"], tgt["sheet_id"])
            rs = list_records(tgt["file_id"], tgt["sheet_id"])
        except OAuthError as e:
            print(f"[失败] {e}", file=sys.stderr)
            return 3
        print(f"[通过] 字段 {len(fs)} 个、记录 {len(rs)} 条 —— 三个值都对得上")
        return 0

    if cmd == "url":
        link = build_auth_url()  # 先建链接再打印，避免报错时先输出一段空引导
        print("在浏览器打开下面这个链接，用有表格权限的那个账号扫码授权：\n")
        print(link)
        print("\n授权后浏览器会跳到回调地址，地址栏里带 code=xxxx。")
        print("把 code 复制出来执行：python tdoc_oauth.py exchange <code>")
        print("（code 10 分钟内有效，且只能用一次）")
        return 0

    if cmd == "exchange":
        if len(argv) < 3:
            print("用法：python tdoc_oauth.py exchange <code>")
            return 2
        tok = exchange_code(argv[2])
        print(f"授权成功。Open-Id={tok['open_id']}")
        print(f"access_token 有效期 {_fmt_remain(tok['expires_in'])}")
        print(f"refresh_token 有效期 1 年（可续），已保存到 {TOKEN_PATH.name}")
        return 0

    if cmd == "refresh":
        tok = refresh_token()
        print(f"刷新成功，新 access_token 剩余 {_fmt_remain(tok['expires_at'] - time.time())}")
        return 0

    if cmd == "status":
        tok = load_token()
        if not tok.get("access_token"):
            print("尚未授权。两条路：")
            print("  A 手填（最快）：python tdoc_oauth.py adopt --token <access_token> --open-id <open_id>")
            print("  B 标准 OAuth：python tdoc_oauth.py url  （需要 client_secret）")
            return 1
        c = oauth_conf()
        src = tok.get("source") or "oauth"
        manual = src == "manual-adopt"
        print(f"凭证来源     : {'手填 token' if manual else 'OAuth 授权'}")
        print(f"Open-Id      : {tok.get('open_id') or '（缺失，调用业务接口会报 10303）'}")
        print(f"Client-Id    : {(c['client_id'][:6] + '…') if c['client_id'] else '（未配置）'}")
        if manual:
            print("access_token : 到期时间未知（以后台标示或接口返回为准）")
            print("refresh_token: 无（无法自动续期，过期时接口返回 10301，重贴即可）")
        else:
            print(f"access_token : 剩余 {_fmt_remain(tok.get('expires_at', 0) - time.time())}")
            print(f"refresh_token: {'有' if tok.get('refresh_token') else '无'}")
            if tok.get("expires_at", 0) - REFRESH_AHEAD_SEC < time.time():
                print("⚠ 即将过期，下次调用会自动刷新")
        return 0

    if cmd == "probe":
        cfg = load_config().get("tdoc") or {}
        tgt = cfg.get("product") or cfg.get("order") or {}
        if not (tgt.get("file_id") and tgt.get("sheet_id")):
            print("config.json 里没有可诊断的表，先配置 tdoc.product / tdoc.order")
            return 2
        r = probe(tgt["file_id"], tgt["sheet_id"])
        print(f"file={r['file_id']}  sheet={r['sheet_id']}  open_id={r['open_id'] or '（空）'}")
        if r["id_changed"]:
            print(f"内部 fileID={r['resolved_file_id']}（业务接口只认这个，已自动换）")
        if r["title"]:
            print(f"文档：{r['title']}（{r['doc_type']}，isOwner={r['is_owner']}）")
        for s in r["steps"]:
            flag = "OK  " if s["ok"] else "FAIL"
            print(f"[{flag}] {s['label']}  http={s['http']} ret={s['ret']} msg={s['msg']}")
            print(f"       {s['endpoint']}")
            if s["trace"]:
                print(f"       traceID={s['trace']}")
        print(f"\n可用形态：{r['working_style'] or '无'}")
        if r["advise"]:
            print(f"建议：{r['advise']}")
        return 0 if r["working_style"] else 1

    if cmd == "test":
        cfg = load_config()
        order = (cfg.get("tdoc") or {}).get("order") or {}
        # 拿商品表测更安全：只读，不碰订单台账
        prod = (cfg.get("tdoc") or {}).get("product") or {}
        target = prod if prod.get("sheet_id") else order
        if not target.get("sheet_id"):
            print("config.json 里没有可测的表，先配置 tdoc.product / tdoc.order")
            return 2
        print(f"用 OAuth 身份读取 file={target['file_id']} sheet={target['sheet_id']} …", flush=True)
        fs = list_fields(target["file_id"], target["sheet_id"])
        print(f"[OK] 字段 {len(fs)} 个：")
        for f in fs:
            print(f"     {f['field_id']}  {f['field_title']}  ({f['field_type']})")
        rs = list_records(target["file_id"], target["sheet_id"])
        print(f"[OK] 记录 {len(rs)} 条")
        if rs:
            r = rs[0]
            shown = {}
            for fv in r["field_values"]:
                v = fv.get("text_value") or fv.get("number_value") or fv.get("bool_value")
                if v not in (None, "", []):
                    shown[fv["field"]] = str(v)[:30]
            print(f"     首行：{json.dumps(shown, ensure_ascii=False)}")
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except OAuthError as e:
        print(f"[错误] {e}", file=sys.stderr)
        sys.exit(1)
