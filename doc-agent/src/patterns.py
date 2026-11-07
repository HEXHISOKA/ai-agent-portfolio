"""格式画像：让"这家客户的单据长什么样"从数据里来，而不是写死在代码里。

为什么单独抽这一层
------------------
`re.fullmatch(r"P0\\d{9,}")` 看上去像通用规则，其实是把**某一家客户的编号规律
当成了世界的规律**。换一家（`XK-2026-0042`、`2026-08-0021`、纯流水号）它立刻
变成误报，而且误报得理直气壮——因为规则不知道自己只是猜的。

所以规则按"依据从哪来"分三层，越靠上越硬：

  A 通用层（不依赖任何外部知识）
    类型对不对、日期是否真实存在、数量×单价=金额、明细之和=合计。
    这些是会计恒等式，换任何客户都成立，永远可以下结论。

  B 画像层（本模块负责）
    编号格式、币种习惯、必填字段。**从历史单据学，或从配置读**。
    换客户只换这一层，代码不动。样本不足时宁可闭嘴，也不猜一个值。

  C 单内自证（不需要任何历史）
    同一个编号在文档别处也出现、同一物料两行单价不同。证据来自这张单据内部，
    比任何历史统计都硬。

关于"格式"怎么表示（踩过一次坑）
--------------------------------
最初我把编号压成一个字符类串（`PO20261024001` → `LLDDDDDDDDDDD`）当形状用。
结果 `PO2026082113`（10 位流水）和 `PO20261024001`（11 位流水）被判成两种
格式——**长度被耦合进了形状里**，而长度本来就是会变的（流水号进位、年份改版）。

现在拆成三件互不干扰的事实：
  · 结构   `LD` = 一段字母 + 一段数字；`LSDSD` = 字母段/分隔符/数字段/…
  · 前缀   字母段的内容本身（`PO`、`XK`）——这是区分形近字符的唯一依据
  · 数字长度  区间（历史 min~max），不参与"是不是同一格式"的判定，只做范围检查
这样 `PO` 10 位和 11 位是同一格式，而在 `PO` 的位置写成 `P0` 会被抓到。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

# 少于这个样本数，只允许提示"可疑"，不给具体候选值。
# 3 是经验值：两条历史里出现一次"另一种写法"，分不清那是另一种格式还是一次误识别。
#
# ⚠️ 这个门槛只管**从历史反推**的画像（source=history）。档案（实施方给的样例、
# 人工确认沉淀）是**认定**，不适用：一条 `PO20261024001` 已经完整给出
# 「前缀 PO + 11 位数字」这个格式，格式判断靠的是结构，不是条数。
MIN_SAMPLES = 3

# 认定型来源：这些画像不是"统计出来的"，是"定下来的"。
AUTHORITATIVE_SOURCES = frozenset({"config", "manual", "profiles", "confirmed"})

# 形近字符组：OCR 混淆几乎都发生在这几组内部（0/O、1/l/I、5/S、8/B、2/Z…）。
# 只在组内替换，不做通用编辑距离——否则会"修"出完全不相关的编号。
_HOMOGLYPH_GROUPS = (
    frozenset("0Oo"),
    frozenset("1lI|"),
    frozenset("5S"),
    frozenset("8B"),
    frozenset("2Z"),
    frozenset("6b"),
    frozenset("9g"),
    frozenset("4A"),
)

_DATE_PATTERNS = (
    re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$"),
    re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日?$"),
    re.compile(r"^(\d{4})(\d{2})(\d{2})$"),
)


# --------------------------------------------------------------------- 基础工具

def _cls(ch: str) -> str:
    """字符类：D 数字 / L 字母 / S 分隔符（- _ / 空格 等）。"""
    if "0" <= ch <= "9":
        return "D"
    if ("A" <= ch <= "Z") or ("a" <= ch <= "z"):
        return "L"
    return "S"


def char_class_seq(value) -> str:
    """逐字符的字符类串，只用于展示和日志（不参与判定，原因见模块说明）。"""
    return "".join(_cls(c) for c in str(value or ""))


def segments(value) -> list[tuple[str, str]]:
    """切成 (类型, 内容) 段：`XK-2026-0042` → [("L","XK"),("S","-"),("D","2026"),…]"""
    out: list[tuple[str, str]] = []
    for ch in str(value or ""):
        kind = _cls(ch)
        if out and out[-1][0] == kind:
            out[-1] = (kind, out[-1][1] + ch)
        else:
            out.append((kind, ch))
    return out


def shape_of(value) -> str:
    """结构串：各段类型首字母拼接，如 `LD`、`LSDSD`。"""
    return "".join(kind for kind, _ in segments(value))


def normalize_name(name) -> str:
    """供应商名归一：去空白/括号/标点并小写，用于把同一家公司的不同写法归到一桶。"""
    if not name:
        return ""
    return re.sub(r"[\s（）()\[\]【】·,，.。:：/\\-]+", "", str(name)).lower()


def supplier_key(supplier) -> str:
    """供应商 → 档案分桶键（`doc_no_profiles.factory_key`）。

    为什么用供应商而不是"工厂"：编号格式是**客户级**知识，而"这个客户是谁"
    在一张单据上唯一稳定的字段就是供应商名。同一家集团的采购单用一套编号，
    换一家客户就换一套；用全厂一份档案，等于把两家的规律互相覆盖。

    空供应商返回 `default`（全厂兜底）。有值返回 `supplier:<归一化名>`——
    前缀是为了和老的 `default` 行共存：老档案仍然有效，只是降级为兜底。
    """
    key = normalize_name(supplier)
    return f"supplier:{key}" if key else "default"


def same_skeleton(a, b) -> bool:
    """两个编号是不是"同一个编号的两种读法"（而不是两个不同编号）。

    这是**归一闸门的最后一道、也是最硬的一道**：允许 O↔0 这类形近替换，
    但绝不允许改动任何非形近字符、绝不允许改变长度。判据逐字写出来：

      · 长度必须相等                 —— `PO20261024001`(13) 与 `PO2026082113`(12) 直接不成立
      · 相同位置相同字符 → 通过
      · 不同位置必须落在同一形近组内 —— `O`/`0` 同组（`0Oo`），`4`/`2` 不同组
      · 最多只允许一处不同           —— 与 `homoglyph_variants` 的"只改一处"一致

    为什么需要它：`suggest()` 用"支持度更高"挑候选，支持度是统计量，统计量
    会出错。若只靠统计，理论上存在把 A 单的流水号"修"成 B 单流水号的可能——
    那正是"不同单据被写成一个编号"。这道判断不依赖任何统计，只看两个字符串
    的骨架，从机器层面保证归一不可能跨单据。
    """
    x = "" if a is None else str(a).strip()
    y = "" if b is None else str(b).strip()
    if not x or not y or len(x) != len(y):
        return False
    diff = 0
    for ca, cb in zip(x, y):
        if ca == cb:
            continue
        diff += 1
        if diff > 1:
            return False
        # 必须同组：同一组里才可能"字形看着一样"
        if not any(ca in g and cb in g for g in _HOMOGLYPH_GROUPS):
            return False
    return diff == 1


def homoglyph_variants(value: str, limit: int = 240) -> list[str]:
    """只改**一个**形近字符能得到的候选（`P020261024001` → `PO20261024001`）。

    刻意限制在"改一处"：两处以上同时错的情形，凭形近关系能凑出的候选太多，
    给出来等于让概率决定数据。
    """
    out: list[str] = []
    seen = {value}
    for i, ch in enumerate(value):
        for group in _HOMOGLYPH_GROUPS:
            if ch not in group:
                continue
            for alt in group:
                if alt == ch:
                    continue
                cand = value[:i] + alt + value[i + 1:]
                if cand in seen:
                    continue
                seen.add(cand)
                out.append(cand)
                if len(out) >= limit:
                    return out
    return out


def prefix_homoglyph_variants(value) -> list[str]:
    """只生成"字母段与数字段交界处"那一个字符的二义候选。

    为什么不直接用 homoglyph_variants
    -------------------------------
    `PO20261024001` 全量形近变体有 21 个（每个 0 变 O、每个 2 变 Z……）。
    一次摆 21 个按钮给人选，等于把决定权交回给概率——而且绝大多数是噪音，
    因为编号里"数字段是数字、字母段是字母"这件事本身是稳定的。

    真正会二义的只有交界处那一个字符：

        PO|20261024001   ← 字母段末位 O，可能是数字 0 被读成了字母
        P|020261024001   ← 字母段末位 P 后面紧跟 0，可能是字母 O 被读成了数字

    这两串在字符类结构上不同（`LLDDD…` vs `LDDDD…`），但在"无衬线字体 + 印章
    遮挡 + 图片识别"这个输入条件下，O 和 0 的包围盒几乎一样，两种读法都成立。
    所以只把这一点摆出来问，问完记进画像（`doc_no_profiles`）就不再问。

    返回的候选**只改这一处**，且只取跨类型的替换（字母↔数字），不做大小写
    变体——大小写不是这个二义性的内容，混进来只会增加噪音。
    """
    s = "" if value is None else str(value).strip()
    m = re.match(r"^([A-Za-z]+)(\d.*)$", s)
    if not m:
        return []
    prefix, rest = m.group(1), m.group(2)
    out: list[str] = []

    # ① 字母段末位是易混字母（O/S/I/B/Z/G）⇒ 可能本应是数字
    last = prefix[-1]
    if last.isalpha():
        for group in _HOMOGLYPH_GROUPS:
            if last not in group:
                continue
            for alt in sorted(group):
                if alt.isdigit():
                    out.append(prefix[:-1] + alt + rest)

    # ② 字母段只有一个字符、且数字段以 0 开头 ⇒ 那个 0 可能是字母 O
    #    限定这么窄是刻意的：不加"单字母前缀"这个条件，任何以 0 开头的纯数字
    #    编号都会冒出一个"首位是字母"的候选，变成噪音。
    if len(prefix) == 1 and rest[:1] == "0":
        for group in _HOMOGLYPH_GROUPS:
            if "0" not in group:
                continue
            for alt in sorted(group):
                if alt.isalpha() and alt.isupper():
                    out.append(prefix + alt + rest[1:])
                    break

    # 去重、保持顺序、去掉和原值相同的
    seen = {s}
    uniq: list[str] = []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def normalize_date(value) -> str | None:
    """多种常见写法 → ISO `YYYY-MM-DD`；不是合法日期返回 None。

    这属于 A 层（通用知识）：`2026年8月21日`、`2026/8/21`、`20260821` 指同一天，
    与客户无关。原来只认 `YYYY-MM-DD`，等于把"这一版数据的写法"当成了唯一写法。
    """
    s = "" if value is None else str(value).strip()
    if not s:
        return None
    for rx in _DATE_PATTERNS:
        m = rx.match(s)
        if not m:
            continue
        y, mo, d = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            return None          # 2 月 30 日这种：格式对、日期不存在
    return None


def _major(counter: dict, key) -> bool:
    """这个取值算不算"多数派"。

    频次 ≥2，或历史里所有样本都是它。前者抗污染（历史混进一条错识别的编号
    不会把画像带偏），后者照顾冷启动（只有一两条历史时不至于全盘否认）。
    """
    if not counter:
        return True
    hit = counter.get(key, 0)
    return hit >= 2 or (hit > 0 and hit == sum(counter.values()))


# ------------------------------------------------------------------- 画像本体

class DocProfile:
    """一类单据（通常是一个客户）的编号格式画像。

    只记三件事：结构序列、字母前缀、数字段长度区间。**不记正则**——
    正则一旦写出来就固定了，而"格式"本身是随客户变的统计事实。
    """

    __slots__ = ("structs", "letters", "dmin", "dmax", "samples", "source", "example", "by")

    def __init__(self, structs: dict | None = None, letters: dict | None = None,
                 dmin: int = 0, dmax: int = 0, samples: int = 0,
                 source: str = "empty", example: str = "", by: str = ""):
        self.structs: dict[str, int] = dict(structs or {})
        self.letters: dict[str, int] = dict(letters or {})
        self.dmin = int(dmin or 0)
        self.dmax = int(dmax or 0)
        self.samples = int(samples)
        self.source = source
        self.example = example
        # 这条档案是"谁认定的"：human（人在面板上选过）/ submit（系统放行时顺手记的）。
        # 两者的可信等级不同，见 authoritative。
        self.by = by

    # -- 判定 ---------------------------------------------------------
    @property
    def empty(self) -> bool:
        return not self.structs

    @property
    def authoritative(self) -> bool:
        """这份画像是不是"认定"而不是"统计"。

        区分它，是因为两种证据的可信来源不一样：
          · 认定（配置 / 实施方样例 / 人工确认沉淀）—— 有人对"这个编号该怎么写"
            表过态。此时**一条就够**：`PO20261024001` 本身就说明了前缀是 `PO`、
            数字段 11 位。格式是结构信息，不是频次信息。
          · 统计（库内历史反推）—— 没人表过态，靠"多数派"猜。历史里可能混着
            没被纠出的错读，所以要 ≥3 条才敢给候选。
        这条区分直接决定用户看到什么：把认定型也卡在 3 条门槛上，就会出现
        "我明明存过这个写法，它还让我人工核对"——门槛用错了对象。

        但"档案里的值"还要再拆一层：如果它只是**系统自己放行时顺手记下的**
        （`by=submit`），那不是"人表过态"，是"系统给自己发了张证"。把它算认定
        就会自我强化：系统归一错一次 → 写进档案 → 下次更确信 → 错得越来越稳。
        所以 submit 只提供格式样例（参与 structs/letters 统计），不免门槛。
        """
        if self.source not in AUTHORITATIVE_SOURCES:
            return False
        return self.by != "submit"

    def accepts(self, value) -> bool:
        """这个编号算不算"本客户的正常编号"。

        三步各自独立，任一步不成立就判不正常——因为三步查的是不同的事：
        结构（段序列）→ 前缀（字母段内容）→ 数字段长度（流水号位数）。
        合在一起查会把"长度变了"误判成"格式变了"，这正是第一版踩的坑。
        """
        if self.empty:
            return True                      # 没有依据就不下判断

        segs = segments(value)
        if not segs:
            return False
        if not _major(self.structs, "".join(k for k, _ in segs)):
            return False

        head = "".join(text for kind, text in segs if kind == "L")
        if self.letters and not _major(self.letters, head):
            return False

        if self.dmin:
            for kind, text in segs:
                if kind != "D":
                    continue
                # 允许比历史上限多一位：流水号跨年/进位会让位数涨一位，
                # 那不是格式变了。再多就不正常了。
                if not (self.dmin <= len(text) <= self.dmax + 1):
                    return False
        return True

    def score(self, value) -> tuple[int, int]:
        """这个值在历史里的"支持度"：(字母前缀出现次数, 结构出现次数)。

        为什么要打分而不是只问"见没见过"：历史本身会被污染——实测库里 9 条
        有 4 条是错误编号（`P0…`），于是 `P` 这个前缀"见过很多次"，只按出现与否
        判断就会认定它正常。比"见过"更有意义的是"哪个解释支持度更高"：
        `PO` 5 次 高于 `P` 4 次，所以 `P0…` 更可能是 `PO…` 的误识别。
        """
        segs = segments(value)
        if not segs:
            return (0, 0)
        head = "".join(text for kind, text in segs if kind == "L")
        shape = "".join(kind for kind, _ in segs)
        return (self.letters.get(head, 0), self.structs.get(shape, 0))

    # -- 说明 ---------------------------------------------------------
    def describe(self) -> str:
        if self.empty:
            return "尚无历史样本"
        # 来源要说准：说成"历史单据"会让"只有 1 条"看着像样本不足，
        # 而它其实是档案里明明白白写着的一条。
        # by=submit 尤其不能说成"人工确认"——那会把系统自己的结论冒充成人的签字。
        if self.by == "submit":
            where = "系统自动沉淀"
        else:
            where = {"config": "配置", "manual": "实施方样例",
                     "profiles": "档案", "confirmed": "人工确认"}.get(self.source, "历史单据")
        tip = self.example or "-"
        n = self.structs.get(shape_of(tip), 0) if tip else 0
        bits = [f"{where}中 {max(n, 1)}/{self.samples} 条形如 {tip}"]
        if self.letters:
            top = max(self.letters.items(), key=lambda kv: kv[1])[0]
            if top:
                bits.append(f"前缀 {top}")
        if self.dmin:
            bits.append(f"数字段 {self.dmin}~{self.dmax} 位")
        return "，".join(bits)

    # -- 给候选 -------------------------------------------------------
    def suggest(self, value) -> tuple[str | None, str | None]:
        """返回 (候选值, 说明)。候选为 None 表示**不给建议**，说明里写原因。

        三种情形要分清楚，界面上也分别显示：
          · 格式正常            → (None, None)，不打扰
          · 不符、有候选        → (候选, 依据)，形近字符改一处就符合画像
          · 不符、没候选        → (None, 原因)，只提示，不编一个值
        """
        text = "" if value is None else str(value).strip()
        if not text:
            return None, None
        if self.empty:
            # 空画像**不产生任何提示**。
            # 说一句"我判断不了"听上去无害，实际会让每张单据都挂一条提示、
            # 每张都触发一次看图复核——把真正的问题淹没在噪音里。
            # "没有依据"是系统状态，不是这张单据的疑点，不该记在单据头上。
            return None, None

        # 第一步：有没有"更站得住脚"的写法？
        # 这一步放在 accepts 之前，才能处理"错误写法在历史里也见过"的情形。
        #
        # 门槛按证据性质分开（这是 2026-10-26 用户实测打回来的那一版）：
        # 认定型档案（档案里写着 PO20261024001，人确认过 7 次）**不需要凑 3 条**——
        # 门槛是为"从历史里猜多数派"设的防污染措施，用在"已经定下来的写法"上，
        # 结果就是"我明明存过它还让我核对"，纯属用错对象。
        enough = self.samples >= MIN_SAMPLES or self.authoritative
        basis = "档案" if self.authoritative else "历史"
        best_score = self.score(text)
        best_cand = None
        for cand in homoglyph_variants(text):
            sc = self.score(cand)
            if sc > best_score:
                best_cand, best_score = cand, sc
        if best_cand is not None and enough:
            return best_cand, (f"{self.describe()}；当前写法在{basis}中的支持度低于"
                              f"「{best_cand}」，且两者只差一个形近字符")

        if self.accepts(text):
            return None, None
        if not enough:
            # 文案要区分"见过但不够"和"系统自己记的但没人认过"：
            # 两种情况都不能自动改写，但原因不同，混成一句"样本不足"会让
            # 用户以为"我明明存过"（见 2026-10-26 那次打回）。
            if self.source == "history":
                return None, (f"只有 {self.samples} 条历史样本，不足以断定正确写法，请人工核对")
            return None, (f"{self.describe()}；这条档案是系统自动沉淀的、没有人确认过，"
                          f"不据此自动改写，请人工核对")
        for cand in homoglyph_variants(text):
            if self.accepts(cand):
                return cand, (f"{self.describe()}；当前写法与该格式不符，"
                              f"且只差一个形近字符")
        return None, f"与{self.describe()}的格式不符，但没有只差形近字符的候选，请人工核对"

    # -- 序列化（配置 / 缓存）-----------------------------------------
    def to_dict(self) -> dict:
        return {"structs": self.structs, "letters": self.letters,
                "dmin": self.dmin, "dmax": self.dmax,
                "samples": self.samples, "source": self.source,
                "example": self.example, "by": self.by}

    @classmethod
    def from_dict(cls, d: dict) -> "DocProfile":
        if not isinstance(d, dict):
            return cls()
        structs = d.get("structs") if isinstance(d.get("structs"), dict) else {}
        letters = d.get("letters") if isinstance(d.get("letters"), dict) else {}
        return cls(
            {str(k): int(v) for k, v in structs.items() if str(k)},
            {str(k): int(v) for k, v in letters.items()},
            int(d.get("dmin") or 0), int(d.get("dmax") or 0),
            int(d.get("samples") or 0),
            str(d.get("source") or "config"),
            str(d.get("example") or ""),
            str(d.get("by") or ""),
        )


EMPTY = DocProfile()


# --------------------------------------------------------------------- 学习

def _build(values: list[str], source: str, by: str = "") -> DocProfile:
    if not values:
        return DocProfile(source=source)

    # 口径：**按不同编号计，同一编号只算一次**。
    # 现实里单据编号不会重复，同一编号出现在库里多次只可能是同一张单被重传、
    # 重测或重试入库。若按出现次数统计，一张单被反复测 7 次就会拿到 7 票，
    # 把"某个写法见过多少次"变成"某张单被传了多少次"——这不是证据，是重复计数。
    values = list(dict.fromkeys(str(v) for v in values))

    structs: dict[str, int] = {}
    letters: dict[str, int] = {}
    lens: list[int] = []
    for v in values:
        segs = segments(v)
        shape = "".join(k for k, _ in segs)
        structs[shape] = structs.get(shape, 0) + 1
        head = "".join(t for k, t in segs if k == "L")
        letters[head] = letters.get(head, 0) + 1
        lens.extend(len(t) for k, t in segs if k == "D")

    top_shape = max(structs.items(), key=lambda kv: (kv[1], kv[0]))[0]
    example = next((v for v in values if shape_of(v) == top_shape), values[0])
    return DocProfile(structs, letters, min(lens) if lens else 0,
                      max(lens) if lens else 0, len(values), source, example, by)


def learn(pairs) -> tuple[dict[str, DocProfile], DocProfile]:
    """从 (编号, 供应商) 序列学习：按供应商分桶，外加一个全局画像。

    为什么要分桶：**编号格式是客户级的，不是行业级的**。同一份单据里，
    供应商名把单据归到某个客户，格式随之切换；供应商认不出来时才退回全局。
    """
    buckets: dict[str, list[str]] = {}
    all_values: list[str] = []
    for no, supplier in pairs:
        text = "" if no is None else str(no).strip()
        if not text:
            continue
        all_values.append(text)
        buckets.setdefault(normalize_name(supplier), []).append(text)
    by_supplier = {k: _build(v, "history") for k, v in buckets.items() if v}
    return by_supplier, _build(all_values, "history")


def pick(by_supplier: dict[str, DocProfile], global_profile: DocProfile,
         supplier) -> DocProfile:
    """按本次单据的供应商挑选画像；认不出来就用全局画像。"""
    key = normalize_name(supplier)
    prof = by_supplier.get(key) if key else None
    if prof is not None and not prof.empty:
        return prof
    return global_profile or EMPTY


def from_config(path: str | None) -> DocProfile | None:
    """从 JSON 配置读画像（换客户时由实施方提供，不改代码）。

    两种写法都支持：
      {"doc_no_examples": ["PO20260101001", "PO20260101002"]}   ← 实施方最省事
      {"structs": {...}, "letters": {...}, "dmin": 11, "dmax": 11}  ← 导入导出用
    读不到或格式不对返回 None，让调用方退回历史学习。
    """
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    examples = raw.get("doc_no_examples")
    if isinstance(examples, list) and examples:
        prof = _build([str(x) for x in examples], "config")
        return prof if not prof.empty else None
    if isinstance(raw.get("structs"), dict):
        prof = DocProfile.from_dict({**raw, "source": "config"})
        return prof if not prof.empty else None
    return None


def from_examples(examples, source: str = "profiles", by: str = "") -> DocProfile | None:
    """直接用一组编号样例建画像（doc_no_profiles 表渠道）。

    来源两种：实施方在上线时直接给的标准写法（source=manual，最权威），
    和人工确认入库时沉淀下来的写法（source=confirmed，逐条积累）。
    样例为空、或拼不出有效画像时返回 None，让调用方退回历史学习——
    档案是优先级最高的证据，但绝不是"有就硬套"。
    """
    vals = [str(x).strip() for x in (examples or []) if str(x).strip()]
    # 卫生闸：一个编号样例至少要含字母或数字。纯符号串（"!!!!"）能拼出
    # "合法"的画像结构，但它只会让真编号全部报不符——垃圾进不如不进。
    vals = [x for x in vals if any(c.isalnum() for c in x)]
    if not vals:
        return None
    prof = _build(vals, source, by)
    return prof if not prof.empty else None
