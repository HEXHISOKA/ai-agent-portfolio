"""行情报价图 → 结构化行。

为什么不是"识别成一段文字再让人抄"：档口报价单是「型号 × 配置 × 颜色档位」
三层结构，同一个 12+256 有 2~3 个价（黑 3700 / 白粉 3800）。落成自由文本后
比价就没法做了，所以这里直接产出「一行一个可入库的报价」。

两条硬规则，都是踩过坑才写进来的：
  · 日期取图上标的日期，不取今天。用户当天发的图可能是前一天的报价，
    用上传日会永久性差一天，而且没人会发现。
  · "35××" 这种占位不猜成 3550。猜出来的数字不是图上给的，后面拿它算毛利
    误差会一路传下去。价格拿不到就留 null，把原文放进 note。
"""

from __future__ import annotations

import json
import re
import time

PROMPT = """你是手机档口报价单的录入员。任务：把图上的价格逐行抄成结构化数据。

**只抄图上真实存在的内容。不推断、不补全、不换算、不取整。**

输出严格 JSON（不要 markdown 代码块、不要解释）：
{
  "date": "图上日期原文：有年份写 YYYY-MM-DD；只有月日就写 MM-DD（如 10.28）；没有日期就 null",
  "source": "抬头/档口名，没有就 null",
  "items": [
    {"model": "型号原文", "spec": "配置如 12+256", "color": "颜色档位原文，如 黑/白粉/混色；没有就 null",
     "price": 整数或 null, "note": "原文补充信息，没有就 null"}
  ],
  "warnings": ["识别不确定的地方，逐条写"]
}

规则：
1. 日期必须取图上标的。图上只有「10.28」这种**没有年份**的写法，就原样输出
   "10-28"，**禁止自己补一个年份**——补错年份比不填更坏，因为它看起来是准的。
   图上没有日期就写 null，绝对不要用今天代替。
2. price 只抄数字本身。图上写「35××」「35xx」这类带占位的，price 填 null，
   把原文写进 note。
3. 同一型号同一配置有多个颜色档位时，每个档位单独一行（这是三个不同的报价）。
4. model 用图上原文，不要自己补品牌名（图上写 K90 就写 K90）。
5. 看不清的行不要猜，跳过并写进 warnings。
6. 如果这张图根本不是报价单，items 返回空数组，warnings 里说明它看起来是什么。
"""


def _normalize_date(raw) -> tuple[str | None, str | None]:
    """把图上日期规整成 YYYY-MM-DD，并说明年份是哪来的。

    为什么补年份要在服务端做：实测模型会把「10.28」补成 2025-10-28（当时是
    2026 年）。它看起来很确定，其实是编的。补年份这件事放服务端，至少能
    留一句"这个年份是补的"，而模型编的东西没有任何痕迹。

    返回 (日期, 需要提醒的话)。
    """
    if not raw:
        return None, None
    s = str(raw).strip().replace("/", "-").replace(".", "-")
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", s)
    if m:
        year = time.strftime("%Y")
        return f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}", \
               f"图上只有月日（原文 {raw}），年份按当前年 {year} 补的，请核对"
    m = re.fullmatch(r"(\d{2,4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, dd = m.group(1), int(m.group(2)), int(m.group(3))
        y = int(y) + 2000 if len(y) == 2 else int(y)
        if y != int(time.strftime("%Y")):
            return f"{y}-{mo:02d}-{dd:02d}", \
                   f"日期年份是 {y}，与当前年份不同——请确认图上到底有没有写年份"
        return f"{y}-{mo:02d}-{dd:02d}", None
    return s, None


def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def parse_quote_image(cloud, image_b64: str, mime: str, model: str,
                      timeout: float = 150.0) -> dict:
    """返回 {ok, date, source, items[], warnings[], raw}。

    raw 保留模型原文：识别错误时必须能回看它到底输出了什么，
    否则"这一行为什么是错的"永远查不清。
    """
    messages = [
        {"role": "system", "content": PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "按上面的规则抄这张报价单。"},
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
            ],
        },
    ]
    raw, usage = cloud.chat(messages, model=model, temperature=0.0,
                            timeout=timeout, json_mode=True)
    body = _strip_fence(raw)
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "error": "模型返回的不是可解析 JSON",
                "raw": raw[:4000], "usage": usage}

    items = []
    for it in (data.get("items") or []):
        if not isinstance(it, dict):
            continue
        price = it.get("price")
        if isinstance(price, str):
            digits = "".join(ch for ch in price if ch.isdigit())
            price = int(digits) if digits else None
        model_name = (it.get("model") or "").strip()
        if not model_name:
            continue
        items.append({
            "model": model_name,
            "spec": (it.get("spec") or "").strip() or None,
            "color": (it.get("color") or "").strip() or None,
            "price": price,
            "note": (it.get("note") or "").strip() or None,
        })

    # 同一型号+配置+颜色出现两条，几乎一定是模型重复抄了同一行。
    seen, deduped, dup = set(), [], 0
    for it in items:
        key = (it["model"], it["spec"], it["color"], it["price"])
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        deduped.append(it)

    warnings = [str(w) for w in (data.get("warnings") or []) if str(w).strip()]
    quote_date, date_note = _normalize_date(data.get("date"))
    if date_note:
        warnings.insert(0, date_note)
    if dup:
        warnings.append(f"已去除 {dup} 条完全重复的行")
    no_price = [f'{i["model"]} {i["spec"] or ""}'.strip() for i in deduped if i["price"] is None]
    if no_price:
        warnings.append(f"{len(no_price)} 行没抄到数字价：{'、'.join(no_price[:5])}")

    return {
        "ok": True,
        "date": quote_date,
        "date_raw": data.get("date") or None,
        "source": data.get("source") or None,
        "items": deduped,
        "warnings": warnings,
        "usage": usage,
        "raw": raw[:4000],
    }
