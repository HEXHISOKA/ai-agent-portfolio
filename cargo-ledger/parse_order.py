"""下单文案 → 结构化数据。

确定性规则，不调大模型，可离线跑、可回归测试。目标是**永远给得出结果、
但会诚实地标出哪里不确定**：拿不准的字段进 needs_review，绝不猜一个值填进去。

这台生意的文案里通常同时出现三个金额，含义完全不同，混了就全错：

    12+256白色下单2209送背夹
    成本2180 行情2430

- 下单价 2209  实际付出去的钱（含赠品加价）
- 成本   2180  手机本身的进货成本（不含赠品）
- 行情   2430  档口当日回收价

所以这里按"角色标注"分别匹配，**不做"取第一个数字"这种兜底**。
早期版本只认「行情35××」这种带星号的写法，写成「行情2430」就漏了，
正是"只认一种写法"造成的。

用法：
    python parse_order.py samples/sample_order_jd.txt
    python parse_order.py --text "【京东】... 3270下单 行情35×× ..."
"""

import argparse
import json
import re
import sys

PLATFORMS = [
    (r"【?京东】?|jd\.com|u\.jd\.com|京东", "京东"),
    (r"【?淘宝】?|taobao\.com|tmall\.com|天猫|淘宝", "淘宝"),
    (r"拼多多|yangkeduo|pinduoduo", "拼多多"),
    (r"抖音|douyin|jinritemai", "抖音"),
    (r"闲鱼|goofish", "闲鱼"),
    (r"快手|kuaishou", "快手"),
]

# 型号必须带品牌或数字，避免把「包售后」这类词当型号
BRAND_WORDS = ("vivo", "iQOO", "OPPO", "一加", "realme", "小米", "红米", "Redmi", "华为",
               "荣耀", "三星", "苹果", "iPhone", "魅族", "努比亚", "中兴", "摩托罗拉",
               "Pixel", "索尼")
BRANDS = "|".join(BRAND_WORDS)

SPEC_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[+＋]\s*(\d{2,4})\s*(G|GB|T|TB)?", re.I)

# ── 金额：三种角色各自的写法。都要求"标签紧挨着数字"，避免跨行误抓 ──────────
# 数字后面不能紧跟「年/月/日/-」：否则「下单 2026-10-29」会把年份当成价格。
_NUM_END = r"(?![0-9年月日\-/])"

ORDER_PRICE_RE = re.compile(
    r"(?:下单价|下单|实付|到手价?|付款|成交价)[：: ]*([0-9]{3,7})" + _NUM_END +
    r"|(?<![0-9])([0-9]{3,7})\s*(?:元)?\s*(?:下单|下单价|实付|到手)"
)
COST_RE = re.compile(
    r"成本(?:价)?[：: ]*([0-9]{3,7})" + _NUM_END +
    r"|(?<![0-9])([0-9]{3,7})\s*(?:元)?\s*成本(?:价)?"
)
# 「行情35××」：星号表示这一位不确定，位数决定精度
QUOTE_RANGE_RE = re.compile(r"行情(?:价)?[：: ]*([0-9]{1,4})\s*([×xX星＊*]{1,3})\s*([0-9]{0,2})?")
# 「行情2430」：写死了就直接用这个数，不留区间
QUOTE_PLAIN_RE = re.compile(r"行情(?:价)?[：: ]*([0-9]{3,6})" + _NUM_END)

# 赠品：送/赠/带/附送 后面跟的东西。「不送」不算赠品。
GIFT_RE = re.compile(
    r"(?<![不])(?:赠品[：:]?|赠送|附送|送|赠|带)\s*"
    r"([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9\+]{0,11})"
)
_GIFT_STOP = ("成本", "行情", "下单", "实付", "到手", "包售后", "不包", "价格", "元")

URL_RE = re.compile(r"https?://[^\s，,。；;）)]+")
MODEL_RE = re.compile(rf"({BRANDS})\s*((?:[A-Za-z0-9][A-Za-z0-9\-]+\s*){{0,2}})", re.I)
COLORS = ("黑", "白", "粉", "蓝", "绿", "紫", "橙", "银", "金", "红", "灰", "青", "彩色", "混色")
AFTERSALE_RE = re.compile(r"(不需要?包售后|不包售后|包售后|售后自理)")
QTY_RE = re.compile(r"([0-9]{1,3})\s*(台|部|件|个|套|只)")

TAG_WORDS = ("畅销", "爆款", "尾货", "二手", "拆封", "全新", "官换", "国行", "港版", "合约机")


def _find_platform(text):
    for pat, name in PLATFORMS:
        if re.search(pat, text, re.I):
            return name
    return ""


def _parse_quote(text):
    """返回 (low, high, raw, note, match)。

    「行情35××」→ 3500~3599（星号是"这一位不确定"）；
    「行情2430」→ 2430（写死了的数，不当区间）；都不做"取中间值"这种编造。
    """
    m = QUOTE_RANGE_RE.search(text)
    if m:
        head, marks, tail = m.group(1), m.group(2), m.group(3) or ""
        width = min(3, len(marks))
        low = int(head) * (10 ** width)
        high = low + 10 ** width - 1
        raw = f"{head}{marks}{tail}"
        return low, high, raw, f"原文「{raw}」→ {low}~{high}", m
    m = QUOTE_PLAIN_RE.search(text)
    if m:
        v = int(m.group(1))
        return v, v, m.group(1), "", m
    return None, None, "", "", None


def _clean_gift(s):
    """把「背夹成本2180」这种黏连截回「背夹」。"""
    s = (s or "").strip()
    for w in _GIFT_STOP:
        i = s.find(w)
        if i > 0:
            s = s[:i]
    s = s.strip("　 、，,。;；:：-＋+")
    if not s or len(s) > 12:
        return ""
    return s


def parse_order_text(text):
    t = (text or "").strip()
    out = {
        "platform": "", "product": "", "brand": "", "spec": "", "color": "",
        "order_price": None, "cost": None, "profit": None,
        "quote_low": None, "quote_high": None, "quote_raw": "", "quote_note": "",
        "gift": "", "has_gift": "",
        "aftersale": "", "qty": 1, "url": "", "tags": [],
        "needs_review": [], "review_fields": [], "review_items": [], "raw": t,
    }
    review, rfields, ritems = [], [], []

    def flag(msg, field=""):
        review.append(msg)
        ritems.append({"field": field, "msg": msg})
        if field:
            rfields.append(field)

    if not t:
        flag("输入为空")
        out["needs_review"] = review
        out["review_items"] = ritems
        return out

    out["platform"] = _find_platform(t)
    if not out["platform"]:
        flag("平台识别不出", "platform")

    m = URL_RE.search(t)
    if m:
        out["url"] = m.group(0)

    sp = SPEC_RE.search(t)
    if sp:
        unit = (sp.group(3) or "G").upper().replace("B", "")
        out["spec"] = f"{sp.group(1)}+{sp.group(2)}"
        out["spec_unit"] = unit
    else:
        flag("配置（如 12+256）没找到", "spec")

    # 颜色：只在配置附近的窗口里找，并先把品牌词剔掉（否则「红米」的「红」会被当成颜色）
    window = t[max(0, sp.start() - 10): sp.end() + 16] if sp else t
    for bw in BRAND_WORDS:
        window = re.sub(bw, "", window, flags=re.I)
    hits = [c for c in COLORS if c in window]
    out["color"] = "/".join(dict.fromkeys(hits))

    mo = MODEL_RE.search(t)
    if mo:
        out["brand"] = mo.group(1)
        tail = " ".join(mo.group(2).split())
        # 型号后面紧跟 +256 这类配置时，最后一个 token 其实是配置的首段，剔掉
        if t[mo.end():mo.end() + 1] in ("+", "＋") and " " in tail:
            tail = tail.rsplit(" ", 1)[0]
        out["product"] = f"{mo.group(1)} {tail}".strip()
    else:
        flag("商品型号没找到", "product")

    # ── 金额：先按角色标注各认各的，认不出才做"唯一金额"兜底 ──────────────
    m_cost = COST_RE.search(t)
    if m_cost:
        out["cost"] = int(m_cost.group(1) or m_cost.group(2))

    m_ord = ORDER_PRICE_RE.search(t)
    if m_ord:
        out["order_price"] = int(m_ord.group(1) or m_ord.group(2))

    low, high, raw, note, qm = _parse_quote(t)
    out.update({"quote_low": low, "quote_high": high,
                "quote_raw": raw or "", "quote_note": note})
    if low is None:
        flag("行情价没找到", "quote")

    if out["order_price"] is None:
        # 兜底：把已知角色的金额（成本/行情/千元配置）全部剔掉，只剩一个才敢认。
        # 剩下多个就必须交给人，硬挑一个等于编数据。
        cands = []
        for num in re.finditer(r"(?<![0-9])([0-9]{3,7})" + _NUM_END, t):
            s = num.start(1)
            if sp and sp.start() <= s < sp.end():
                continue
            hit_known = False
            for mm in (m_cost, qm):
                if mm and mm.start() <= s < mm.end():
                    hit_known = True
                    break
            if not hit_known:
                cands.append(int(num.group(1)))
        uniq = list(dict.fromkeys(cands))
        if len(uniq) == 1:
            out["order_price"] = uniq[0]
            flag("下单价没标注「下单」，按唯一剩下的金额推断", "order_price")
        elif not uniq:
            flag("下单价没找到", "order_price")
        else:
            flag("文案里有多个金额，判断不出哪个是下单价（" +
                 "/".join(str(x) for x in uniq) + "），请手填", "order_price")

    # 毛利 = 行情价 − 成本（用户 2026-10-29 定义）：成本是买手机付的钱，
    # 行情是档口当日的回收价，两者之差才是这单真正能落袋的钱。
    # 下单价不参与毛利——下单价高于成本的部分是赠品换来的，不是利润。
    # 行情是区间时取下限，与比价口径一致，不猜中间值、不取平均。
    basis = out["quote_low"] if out["quote_low"] is not None else out["quote_high"]
    if basis is not None and out["cost"] is not None:
        out["profit"] = basis - out["cost"]

    gm = GIFT_RE.search(t)
    if gm:
        g = _clean_gift(gm.group(1))
        if g:
            out["gift"] = g
            out["has_gift"] = "有"

    am = AFTERSALE_RE.search(t)
    if am:
        out["aftersale"] = "不包" if am.group(1).startswith(("不", "需要不")) or "不包" in am.group(1) else "包"

    qty_m = QTY_RE.search(t)
    if qty_m:
        out["qty"] = int(qty_m.group(1))

    out["tags"] = [w for w in TAG_WORDS if w in t]

    # 商品名与配置合成商品表里的规范名，方便和商品档案对齐
    if out["product"] and out["spec"]:
        out["canonical_name"] = f'{out["product"]} {out["spec"]}'
    else:
        out["canonical_name"] = out["product"] or ""

    out["needs_review"] = review
    out["review_items"] = ritems
    out["review_fields"] = sorted(set(rfields))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", default="")
    ap.add_argument("--text", default="")
    a = ap.parse_args()
    if a.text:
        text = a.text
    elif a.file:
        with open(a.file, encoding="utf-8") as f:
            text = f.read()
    else:
        print("给个文件或 --text")
        return 2
    res = parse_order_text(text)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
