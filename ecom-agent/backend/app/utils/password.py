import base64
import hashlib

import bcrypt


# 标记预处理方式，使旧 bcrypt 账号和新密码可以共用登录入口。
_HASH_PREFIX = "{bcrypt-sha256-v1}"


def _prepare_password(password: str) -> bytes:
    # 固定为 44 个 ASCII 字节，避免多字节字符超过 bcrypt 的 72 字节限制。
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    """预处理完整密码后使用 bcrypt 加盐哈希，并保存格式标记。"""
    hashed = bcrypt.hashpw(_prepare_password(password), bcrypt.gensalt())
    return _HASH_PREFIX + hashed.decode("ascii")


def verify_password(password: str, hashed_password: str) -> bool:
    """按保存的格式验证密码，兼容未带标记的历史 bcrypt 账号。"""
    try:
        if hashed_password.startswith(_HASH_PREFIX):
            candidate = _prepare_password(password)
            stored = hashed_password[len(_HASH_PREFIX):]
        else:
            candidate = password.encode("utf-8")
            stored = hashed_password
            # 旧格式无法验证超过 72 字节的输入，直接判为密码错误。
            if len(candidate) > 72:
                return False
        return bcrypt.checkpw(candidate, stored.encode("ascii"))
    except (ValueError, UnicodeError):
        return False
