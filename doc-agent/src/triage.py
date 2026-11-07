"""疑点分诊：哪些疑点必须由人做决定，哪些已经被证据消解。

为什么需要这一层
----------------
在此之前，前端用一句 `issues.filter(i => i.level !== "info")` 当作"要不要问人"。
这个判据是**按严重程度**分的，而业务上要分的是**按有没有结论**：

    · "单据编号只有 1 条历史样本，不足以断定正确写法" —— 看图复核逐字核对后
      已确认当前值正确，结论已经有了，再问人只是让人空点一次"通过"；
    · "供应商与原文差 1 个字" —— 遮住它的正是印章，去掉印章后字迹清楚，
      这类不是数据矛盾，问二十遍也只是同一句话；
    · "明细之和 88,000 ≠ 合计 77,100" —— 这才是真疑点：三处数字至少一处错，
      机器没有依据判断该改哪一个，必须人看过原图来选。

所以判据改成"有没有可判定的结论"，而不是"等级高不高"。用户侧的原始定义是：

    疑点 = ①同一性质的数据出现多个互相矛盾的取值
           ②勾稽不成立（数量×单价≠金额、明细之和≠合计）
    不算疑点 = ①写法已按本厂档案归一（info）
               ②看图复核逐字一致（证据充分）
               ③图上看不清是因为印章/模糊/遮挡，而该值在 OCR 原文里出处完整
                  （即"遮住它的东西拿掉之后，文字是清楚的"）

第三类为什么可以放过
--------------------
印章是**油墨覆盖**：被完全盖死的笔画，像素上已经不存在了，任何模型都读不出。
但 OCR 走的是另一条路——`preprocess.py` 先把红色油墨当红色去掉再识别，所以
被印章压住的黑字往往在文本层是完整的。此时：
    · 看图复核读不出（unreadable，cause=seal）—— 这不是"数据可疑"，是"看图这条路走不通"；
    · 文字出处覆盖率 ≥ 0.98 —— 说明这个值确实来自这张单据的文本层。
两条合起来，等于"值有出处、只是照片上被盖住了"。若非要人放行一次，问到的
只是"你要不要相信自己的 OCR"，这不是业务决策。

反过来，若文字出处也不完整（覆盖率低），说明**两条路都断了**，那就必须问人。

三档处置
--------
auto     不需要人，也不需要记录（写法归一、提示类、币种推断……）
resolved 不需要人，但**要记录**是谁/哪条证据消解的（进审计链，事后可追溯）
ask      必须人给一个处置，且必须给**选项**——不是"通过/退回"两个键
"""
from __future__ import annotations

import patterns

# 看图看不清的成因里，属于"遮挡物本身"的那些。这些成因下读不出 ≠ 数据可疑，
# 而是照片这条路被挡住了；能否放过要看文字出处（见 _resolved_by_occlusion）。
_OCCLUSION_CAUSES = {"seal", "blur", "occlusion"}

# 文字出处判"完整"的门槛。与 grounding.PASS_COV 同值：L4 用它判"通过"，
# 这里用它判"文本层清楚"，两边必须一致——否则会出现"L4 说通过、分诊说不算"。
_GROUNDED_COV = 0.98

# 勾稽类：三处数字至少一处错，机器没有依据判断改哪一个。
_CROSS_LAYERS = {"L3"}

# 即使不带 L3 层号、也属于"多值/矛盾"的 code —— 这类必须人判。
_CONFLICT_CODES = {
    "total_is_qty",          # 合计被读成了数量之和
    "total_no_source",       # 合计在明细里找不到任何来源
    "slot_price_is_amount",  # 金额被填进了单价格
    "label_line_contradict", # 供应商与单据「供应商：」那一行写的不是同一家
    "value_not_in_source",   # 值在原文里完全找不到（可能是编造，也可能是被整块遮挡）
    "duplicate_price",       # 同一物料的重复行单价不一致
    "multi_total",           # 单据上出现两个合计：选哪个算数，不是看图能决定的
}

# 明确**不**打断入库的 code。这些是"说明"而不是"疑点"：
# 它们描述的是系统自己的处理动作或单据类型特征，人看了也无法改变结果。
_SILENT_CODES = {
    "currency_inferred",  # 无金额单据上的币种是模型补的，本来就不该当已核实字段
}

# 看图复核**回答不了**的疑点：图上两个值都印着，问题不是"哪个读对了"，
# 而是"这两个值哪个算数"。这类不能因为复核说"当前值一致"就消解。
_ALWAYS_ASK_CODES = {
    "multi_total",  # 单据上出现两个合计（正文一个、页脚一个，或中英文两个）
}

# 能给出"具体改成什么"的方向。带这些方向的疑点必须走处置面板选值，
# 不能用"审核通过"整单放行（见 triage() 末尾 must_choose 的说明）。
_DIRECTIONAL = {"rule", "recompute", "profile", "merge", "image", "alt_total",
                "homoglyph"}
# 注意 manual 不在里面：它是"系统给的方向都不要，我自己填"，属于人的自由裁量，
# 不是系统算出的候选。把它算进来会让"审核放行"永远不可用。

# 人工处置（/api/submit 的 audit.choices）允许出现的 how。
#
# 这份白名单必须**从上面派生**，不能再手抄一遍：之前 app.py 里手抄了
# ("keep","image","manual","reject")，结果 alt_total / homoglyph 这两个后加的
# 方向，人选完之后会被服务端悄悄丢掉——表现是"选了个值，结果没生效，还提示
# 仍有疑点没处置"。这类错误不会报错，只会让人以为系统坏了。
#
# merge 也在集合里，但它 value 为空，会在"选了填写新值但值是空的"那一步被挡下：
# 合并重复行要模型判断两行是不是同一物料，本来就不该走单值处置这条路。
AUDIT_HOWS = frozenset({"keep", "manual", "reject"} | _DIRECTIONAL)


def _finding_map(findings) -> dict:
    return {f.get("field"): f for f in (findings or []) if isinstance(f, dict) and f.get("field")}


def _ground_map(details) -> dict:
    return {d.get("field"): d for d in (details or []) if isinstance(d, dict) and d.get("field")}


def _resolved_by_occlusion(field: str, gmap: dict, cause_label: str) -> str | None:
    """遮挡导致图上看不清时，再看文字出处是否完整。完整 ⇒ 返回消解理由，否则 None。

    这里刻意要求 coverage ≥ 0.98 而不是"有覆盖率就行"：0.6~0.98 那一档恰恰是
    "OCR 读错一个字"与"值被篡改一个字"在字面上无法区分的区间（见 grounding.py
    的说明）。那一档不能由系统自行放过。
    """
    d = gmap.get(field) or {}
    cov = d.get("coverage")
    if cov is None:
        # 这个字段不参与出处校验（数字、日期）——没有第二证据源，就不要替人放过。
        return None
    if float(cov) < _GROUNDED_COV:
        return None
    return (f"图上看不清是因为{cause_label}，而该值在 OCR 原文里有完整出处"
            f"（覆盖率 {cov:.0%}）——遮住它的东西去掉之后，文字是清楚的")


def _num_eq(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.005
    except (TypeError, ValueError):
        return str(a) == str(b)


def _match_insufficient(iss: dict) -> str | None:
    """"看图逐字一致"在这类疑点上**不构成证据**，返回原因；否则 None。

    这是对"两个独立来源同时指向当前值"这句话的修正。原先它被当成最强证据，
    依据是"OCR/抽取 与 视觉复核都同意"。但那个前提只在两个来源**真的独立**
    时成立：

      · 勾稽矛盾（数量×单价≠金额、明细之和≠合计）是**算术事实**。
        `500 × 8.2 = 4100` 不需要看图就能算出来。看图读到 4000，只能证明
        "印刷体上写的是 4000"或者"模型把 4100 读成了 4000"——两种情况都
        无法推翻算术。而真实原因可能是印章压住了那一位，图上本来就是 4100。
        这种矛盾不能由"再看一眼"消解，必须人看着原件定。

      · 编号里的同形字符（O/0、I/1、S/5、B/8、Z/2、G/6）在无衬线字体下包围盒
        几乎一致。抽取和复核都以**图像**为输入，会一致地读成同一个字符——
        "两个读数一致"不是两条证据，是同一条证据重复了一次。这一条在
        调用方按 doc_no 单独处理（要给出候选写法，不是简单退回）。

    反过来说，普通字段（供应商、物料名、日期）的"看图一致"仍然是有效证据：
    那里读错通常是**字形差异**（"塑胶" vs "塑料"），两个模型不会一致地错。
    """
    if iss.get("layer") in _CROSS_LAYERS or (iss.get("code") or "") in _CONFLICT_CODES:
        return ("这是勾稽/多值矛盾：数量×单价、明细之和都是算术事实，"
                "再看一遍图不能推翻算术——图上那一位本身可能就是被印章压住的")
    return None


def _doc_no_variants(value) -> list[str]:
    """编号在"字母段/数字段交界处"的另一个可能写法（见 patterns 里同名函数）。"""
    try:
        return patterns.prefix_homoglyph_variants(value)
    except Exception:  # noqa: BLE001 — 画像是增强项，算不出候选不能影响分诊
        return []


def _homoglyph_hint(cur, alt) -> str:
    """指出两种写法差在第几个字符、差的是哪两个字符。

    只说"形近字符不同"没用——人拿着原件也得到处找。说到位置，人只需看那一处。
    """
    a, b = str(cur or ""), str(alt or "")
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return (f"第 {i + 1} 位：票面上印的是「{a[i]}」还是「{b[i]}」"
                    f"——两者在无衬线字体下几乎同形，请照原件确认")
    return "两种写法只差一个字符，请照原件确认"


def _option(value, label: str, how: str, why: str = "") -> dict:
    o = {"value": value, "label": label, "how": how}
    if why:
        o["why"] = why
    return o


def triage(issues: list[dict], findings=None, g_details=None) -> dict:
    """把 issues 分成 auto / resolved / ask 三档。

    返回
    ----
    {
      "auto":      [{...issue, "disposition":"auto", "evidence": "..."}],
      "resolved":  [{...issue, "disposition":"resolved", "evidence": "..."}],
      "ask":       [{...issue, "disposition":"ask", "evidence": "...", "options": [...]}],
      "need_human": bool,          # ask 非空
      "counts": {"auto":n, "resolved":n, "ask":n},
    }

    编号的"同批问一次"不在这里做：那要求改写待入库的编号值，属于入库前的归一
    动作，放在 app._post_submit 里先做（见 batch_nos 的说明），本模块只看
    归一之后还剩什么疑点。
    """
    fmap = _finding_map(findings)
    gmap = _ground_map(g_details)

    out = {"auto": [], "resolved": [], "ask": []}
    for iss in issues or []:
        if not isinstance(iss, dict):
            continue
        field = iss.get("field") or ""
        level = iss.get("level") or "warn"
        code = iss.get("code") or ""
        item = dict(iss)

        # ① 写法已归一 / 说明类：不需要人
        if level == "info":
            out["auto"].append({**item, "disposition": "auto",
                                "evidence": "写法已按本厂档案归一，库内值即为正确写法"})
            continue
        if code in _SILENT_CODES:
            out["auto"].append({**item, "disposition": "auto",
                                "evidence": "说明类提示，不影响入库值"})
            continue

        # 编号交界处二义：必须放在"看图一致"之前拦下。
        #
        # 这一屏给的是**两个具体写法**，不是"通过/退回"——用户要的就是选 PO 还是 P0。
        # 为什么不交给看图复核：复核和抽取都读同一张图，O 与 0 的包围盒在无衬线
        # 字体下几乎一致，两者会一致地读成同一个字符。等选完之后，这个写法会沉淀
        # 进工厂档案（doc_no_profiles），下一张同厂单据由画像直接归一，不再问。
        variants = _doc_no_variants(item.get("value")) if field == "doc_no" else []
        if variants:
            cur = item.get("value")
            out["ask"].append({
                **item, "disposition": "ask",
                "evidence": (
                    f"编号「{cur}」的字母段与数字段交界处是 O / 0 同形，"
                    f"看图复核也是同一张图、同一类模型，读成一样不能排除读错；"
                    f"另一种写法是「{variants[0]}」"),
                "options": [
                    _option(cur, f"就是「{cur}」", "keep",
                            "票面上就是这一串，不需要改"),
                    *[_option(v, f"应为「{v}」", "homoglyph", _homoglyph_hint(cur, v))
                      for v in variants],
                    _option(None, "手动填写", "manual", "两个都不对时照纸质原件填"),
                    _option(None, "退回不入库", "reject"),
                ],
            })
            continue

        f = fmap.get(field)
        if f and code not in _ALWAYS_ASK_CODES:
            verdict = f.get("verdict")
            cause = f.get("dirty_cause") or "none"
            if verdict == "match":
                why_not = _match_insufficient(item)
                if why_not:
                    # 看图说"一致"，但这条证据在这里不成立（见 _match_insufficient）。
                    # 注意只拦 match：复核读到**不同**的值（mismatch）时，那个值本身
                    # 就是最有用的候选，不能连它一起丢掉。
                    out["ask"].append({
                        **item, "disposition": "ask",
                        "evidence": why_not,
                        "options": _options_from_fixes(item),
                    })
                    continue
                # 看图逐字一致 —— 普通字段上这是最强的一条证据：两个独立来源
                # （OCR/模型抽取 与 视觉）同时指向当前值，疑点本身已经不存在。
                out["resolved"].append({
                    **item, "disposition": "resolved",
                    "evidence": "看图复核逐字一致" + (f"（{f.get('reason')}）" if f.get("reason") else ""),
                })
                continue
            if verdict == "unreadable":
                why = (_resolved_by_occlusion(field, gmap, f.get("dirty_label") or cause)
                       if cause in _OCCLUSION_CAUSES else None)
                if why:
                    out["resolved"].append({**item, "disposition": "resolved", "evidence": why})
                    continue
                out["ask"].append({
                    **item, "disposition": "ask",
                    "evidence": f"图上看不清（{f.get('dirty_label') or cause}），且没有第二条判据可以定论",
                    "options": [
                        _option(iss.get("value"), "保持当前值（已知图上读不出，风险自负）", "keep",
                                "当前值来自 OCR；图上读不出时，这是唯一的现有值"),
                        _option(None, "手动填写（对照纸质原件）", "manual",
                                "印章/模糊挡住了图，只能人看着原件填"),
                        _option(None, "退回不入库", "reject"),
                    ],
                })
                continue
            # mismatch：图上读到了不同的值 —— 这是"多个同性质数据冲突"，必须人选
            img = f.get("apply_value") or f.get("image_value")
            opts = []
            if img:
                opts.append(_option(img, f"改为看图读到的「{img}」", "image",
                                    f.get("reason") or "视觉模型在原图上逐字读到该值"))
            opts.append(_option(iss.get("value"), "保持当前值", "keep",
                                "当前值是 OCR + 规则层算出的，未经图面确认"))
            opts.append(_option(None, "手动填写", "manual", "两个候选都不对时，照原件填"))
            opts.append(_option(None, "退回不入库", "reject"))
            out["ask"].append({
                **item, "disposition": "ask",
                "evidence": (f"看图读到「{img}」，当前值是「{iss.get('value')}」"
                             if img else "看图与当前值不一致，但图上没能读出可采纳的值"),
                "image_value": img,
                "options": opts,
            })
            continue

        # ③ 没有复核结论（复核未覆盖、或该字段不进复核）

        # 抽取模型本身不可信（有被单据文字改写的前科）。这不是"某个字段的矛盾"，
        # 而是整份结果的来源有问题，所以给不出候选值，只能"认可 / 退回"。
        # 放在 ③ 最前面：它的 field 是空的，落到下面的分支会被当成"提示类"放过，
        # 而它恰恰是唯一一个必须每次都拦的疑点。
        if code == "untrusted_model":
            out["ask"].append({
                **item, "disposition": "ask",
                "evidence": "抽取模型有被劫持的前科：问题在整份结果的来源，不在某一个字段",
                "options": [
                    _option(iss.get("value"), "已对照原图核对，认可这些值", "keep",
                            "人看过原图，认为结果与单据一致；入库记录会留下这次确认"),
                    _option(None, "退回，换模型重抽", "reject",
                            "退回本单；在页面上换一个模型重新上传"),
                ],
            })
            continue

        if iss.get("layer") in _CROSS_LAYERS or code in _CONFLICT_CODES:
            out["ask"].append({
                **item, "disposition": "ask",
                "evidence": "勾稽/多值冲突：机器没有依据判断该改哪一处",
                "options": _options_from_fixes(iss),
            })
            continue
        if level == "err":
            out["ask"].append({
                **item, "disposition": "ask",
                "evidence": "阻断性错误：不处理无法入库",
                "options": _options_from_fixes(iss),
            })
            continue

        out["auto"].append({**item, "disposition": "auto",
                            "evidence": "提示类，不改变入库值，也不影响勾稽"})

    # 标记哪些疑点**必须给出一个值**、不能用"审核通过"整单放行。
    #
    # 这条是从原设计里保下来的硬约束：复核读到图上写的是 A、待入库是 B 时，
    # 系统没有依据判断该信哪个，但它**知道这两者不能同时成立**。如果允许人点
    # 一次"通过"就把 B 存进库，等于给了一条绕过复核的快捷方式——而这正是
    # 四道闸门里第 ④ 道要挡的事。所以这类必须落到处置面板上选一个值。
    for a in out["ask"]:
        a["must_choose"] = bool(
            a.get("image_value")
            or any(o.get("how") in _DIRECTIONAL for o in (a.get("options") or [])))

    counts = {k: len(v) for k, v in out.items() if isinstance(v, list)}
    out["counts"] = counts
    out["need_human"] = counts["ask"] > 0
    return out


def _options_from_fixes(iss: dict) -> list[dict]:
    """把规则层已经算出的修改方向变成选项。规则层没有依据时，不给假选项——
    只留"手动填写"和"退回"，因为编一个值让人点，比不给更坏。"""
    opts = []
    # 「单据上有两个总额」这种多值冲突，候选值来自单据本身（L5 从文本里解析出来的），
    # 不是规则推算的。所以单列一类 alt_total：可信度按"需复核"，且**不默认选中**——
    # 两个值都在票面上，哪个算数取决于「合计/价税合计」的口径，机器无从判断。
    cur = iss.get("value")
    for v in (iss.get("multi_values") or []):
        # 按数值比，不按字面比：当前值可能是 77100（int），候选是 77100.0，
        # 字面不同但说的是同一个数——按字面比会给出一个"改成和现在一样"的选项。
        if _num_eq(cur, v):
            continue
        opts.append(_option(v, f"改用单据上的 {v}", "alt_total",
                            "同一张单据上印着的另一个总额；选它表示这一处才是有效值"))
    for fx in (iss.get("fixes") or []):
        if not isinstance(fx, dict):
            continue
        v = fx.get("value")
        how = fx.get("how") or "rule"
        label = (f"按系统推算改为「{v}」" if v is not None
                 else "按建议合并为一行")
        opts.append(_option(v, label, how, fx.get("why") or ""))
    opts.append(_option(iss.get("value"), "保持当前值", "keep",
                        "认为票面就是当前值，不改"))
    opts.append(_option(None, "手动填写", "manual", "两个候选都不对时，照原件填"))
    opts.append(_option(None, "退回不入库", "reject"))
    return opts


def summary(t: dict) -> dict:
    """给接口/前端用的精简汇总。"""
    return {
        "need_human": t.get("need_human", False),
        "counts": t.get("counts") or {},
        "ask": t.get("ask") or [],
        "resolved": [{"field": i.get("field"), "msg": i.get("msg"),
                      "evidence": i.get("evidence")} for i in (t.get("resolved") or [])],
    }
