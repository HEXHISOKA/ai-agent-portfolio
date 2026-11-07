# -*- coding: utf-8 -*-
"""把演示账号弱密码替换为随机强密码（复用后端 bcrypt 哈希口径）。

密码不写进代码：运行时通过环境变量或交互输入，避免凭据进入版本库。
"""
import getpass
import os
import secrets
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.utils.password import hash_password, verify_password  # noqa: E402

DB_PATH = BACKEND / "data" / "ecom.db"

USERS = ["admin", "aaa", "bbb"]


def resolve_password() -> str:
    """优先取环境变量，否则交互输入；留空则自动生成随机密码。"""
    pw = os.environ.get("NEW_PASSWORD", "")
    if not pw:
        pw = getpass.getpass("新密码（直接回车 = 自动生成随机密码）: ").strip()
    if not pw:
        alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*-_"
        pw = "".join(secrets.choice(alphabet) for _ in range(16))
        print(f"已自动生成密码: {pw}")
    return pw


def main() -> None:
    new_password = resolve_password()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    hashed = hash_password(new_password)
    for name in USERS:
        row = cur.execute(
            "SELECT id, username, role FROM users WHERE username = ?", (name,)
        ).fetchone()
        if row is None:
            print(f"  [跳过] 用户不存在: {name}")
            continue
        uid, username, role = row
        cur.execute(
            "UPDATE users SET password = ? WHERE id = ?", (hashed, uid)
        )
        # 回读校验：新密码可登录、旧密码失效
        stored = cur.execute(
            "SELECT password FROM users WHERE id = ?", (uid,)
        ).fetchone()[0]
        ok_new = verify_password(new_password, stored)
        print(f"  [完成] id={uid} {username}({role})  新密码校验={ok_new}")
    conn.commit()
    conn.close()
    print(f"数据库: {DB_PATH}")


if __name__ == "__main__":
    main()
