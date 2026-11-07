"""台账口令闸门（单口令，不建账号体系）。

为什么是"一道口令"而不是用户名+密码：
用这套系统的就是运营和管理员两三个人，建账号体系要配库、要做找回密码、
要考虑离职停用，成本远大于收益。真实威胁只有一个——**拿到链接的人
能看见全部订单**（地址、下单人、金额）。一道口令 + 长期会话足够挡住它。

三条设计原则：

1. **口令不存明文**。config 里只存 PBKDF2 加盐摘要，config 被看到也不等于
   口令泄露。要换口令用 `python -m auth_gate set-password`。
2. **会话用签名 cookie，不用 localStorage**。cookie 设 httpOnly，前端 JS
   拿不到；token 放 localStorage 一旦有 XSS 就被整条偷走。
3. **失败要计数并锁**。单口令意味着暴力破解只剩"猜"这一条路，不限速的话
   6 位以上口令也扛不住有人挂着脚本跑。5 次错锁 15 分钟，按 IP 记。

和"公开页"的关系：白名单里的路径（/f/*、/pub/*、/api/health、/login）
不经过这里。公开页能摸到的接口本来就只有两个，且在各自路由里再做限制。
"""

import base64
import hashlib
import hmac
import os
import secrets
import time

ITERATIONS = 200_000
_COOKIE = "ledger_sess"


# ------------------------------------------------------------------ 口令摘要

def hash_password(pw: str, iterations: int = ITERATIONS) -> str:
    """返回 pbkdf2_sha256$迭代次数$盐$摘要，四段用 $ 分隔。"""
    if not isinstance(pw, str) or not pw:
        raise ValueError("口令不能为空")
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iterations)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return f"pbkdf2_sha256${iterations}${b64(salt)}${b64(dk)}"


def verify_password(pw: str, stored: str) -> bool:
    """常量时间比对。格式不对一律 False，不抛异常——登录接口不能因为
   config 写错就 500，那等于告诉对方"这条路存在但坏了"。"""
    try:
        algo, iters, salt_b64, hash_b64 = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        unpad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        salt = base64.urlsafe_b64decode(unpad(salt_b64))
        want = base64.urlsafe_b64decode(unpad(hash_b64))
        got = hashlib.pbkdf2_hmac("sha256", (pw or "").encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(got, want)
    except (ValueError, TypeError, base64.binascii.Error):
        return False


# ------------------------------------------------------------------ 会话 cookie

def _sign(secret: str, msg: str) -> str:
    return hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def make_session(secret: str, days: int = 30) -> str:
    """值是 `过期时间戳.签名`。不加密内容——里面只有一个时间戳，
    签名保证改不动就行。"""
    exp = int(time.time()) + int(days * 86400)
    return f"{exp}.{_sign(secret, str(exp))}"


def check_session(secret: str, value: str) -> bool:
    try:
        exp_s, sig = (value or "").split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    return hmac.compare_digest(sig, _sign(secret, exp_s))


def cookie_name() -> str:
    return _COOKIE


def new_secret() -> str:
    return secrets.token_hex(32)


# ------------------------------------------------------------------ 失败限流

class RateLimiter:
    """按 IP 记失败次数，超限锁定。单进程内存足够——这套系统就一个进程，
    重启即清空，而重启本身已经打断暴力破解了。"""

    def __init__(self, limit: int = 5, window: int = 600, lock: int = 900):
        self.limit, self.window, self.lock_for = limit, window, lock
        self._fails: dict[str, list] = {}
        self._locked: dict[str, float] = {}

    def locked(self, ip: str) -> int:
        """还锁着就返回剩余秒数，否则 0。"""
        until = self._locked.get(ip, 0)
        left = int(until - time.time())
        if left > 0:
            return left
        self._locked.pop(ip, None)
        return 0

    def fail(self, ip: str) -> int:
        now = time.time()
        hits = [t for t in self._fails.get(ip, []) if now - t < self.window]
        hits.append(now)
        self._fails[ip] = hits
        if len(hits) >= self.limit:
            self._locked[ip] = now + self.lock_for
            self._fails.pop(ip, None)
        return len(hits)

    def reset(self, ip: str):
        self._fails.pop(ip, None)
        self._locked.pop(ip, None)


# ------------------------------------------------------------------ 命令行

def _cli(argv):
    import getpass
    import json
    import sys

    if len(argv) < 2 or argv[1] not in ("set-password", "show", "gen"):
        print("用法：\n"
              "  python -m auth_gate set-password          # 交互设置（输入不显示）\n"
              "  python -m auth_gate set-password --stdin  # 从标准输入读一行，给脚本/远程用\n"
              "  python -m auth_gate gen                   # 只生成一个随机口令，不动 config\n"
              "  python -m auth_gate show                  # 看当前有没有配好")
        return 2

    if argv[1] == "gen":
        # 去掉容易看错的 i/l/o/0/1，这是要人手抄或口述的
        alpha = "abcdefghjkmnpqrstuvwxyz23456789"
        print("".join(secrets.choice(alpha) for _ in range(12)))
        return 0

    here = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(here, "config.json")
    cfg = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)

    if argv[1] == "show":
        a = cfg.get("auth") or {}
        print("password_hash:", (a.get("password_hash") or "(未设置)")[:28] + "...")
        print("session_secret:", "已设置" if a.get("session_secret") else "(未设置)")
        print("session_days:", a.get("session_days", 30))
        return 0

    if "--stdin" in argv:
        pw = sys.stdin.readline().strip()
        if len(pw) < 8:
            print("至少 8 位。这不是形式——单口令是唯一一道门。")
            return 1
    else:
        pw = getpass.getpass("新口令（不显示）：")
        if len(pw) < 8:
            print("至少 8 位。这不是形式——单口令是唯一一道门。")
            return 1
        if pw != getpass.getpass("再输一次："):
            print("两次不一致")
            return 1
    a = cfg.setdefault("auth", {})
    a["password_hash"] = hash_password(pw)
    # 换口令必须同时换 session_secret：不换的话旧 cookie 仍然有效，
    # "改口令把所有已登录设备踢下线"这个唯一的作用就没了。
    a["session_secret"] = new_secret()
    a.setdefault("session_days", 30)
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    print("已写入 config.json 的 auth 段，其他字段未动，旧登录已全部失效。")
    print("⚠️ 线上要生效必须重新发布一次（口令存在本地 config.json 里）。")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv))
