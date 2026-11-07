import logging
import re

from app.agent import router_rules
from app.services import llm_service


logger = logging.getLogger(__name__)

# 指代词（长的在前，避免「这个」先于「这个商品」命中）
_REFERENCE_WORDS = (
    "这个商品",
    "该商品",
    "此商品",
    "这款商品",
    "这个产品",
    "该产品",
    "它",
    "这个",
    "那个",
    "这款",
    "该款",
)

# 只回看最近这几条消息，避免很久之前提到的商品被误当上下文
_HISTORY_SCAN_TURNS = 6

# 全局范围词：出现说明用户问的是全店/跨商品，不做“承接上文商品”的补全
_GLOBAL_SCOPE_WORDS = (
    "哪些商品", "哪个商品", "各商品", "各个商品", "所有商品", "全部商品",
    "类目", "分类", "品类", "整体", "全店", "全站", "全库",
)

# “承接上文”的判断：问句很短（自己没主语）、没提商品、不问全局、且问的是业务属性
_INHERIT_MAX_LENGTH = 16
# 业务属性词：出现才认为这句是在问“它的某个指标/情况”
_INHERIT_KEYWORDS = (
    "评价", "评论", "评分", "星级", "差评", "好评", "口碑", "销量", "订单",
    "库存", "价格", "金额", "退货", "售后", "表现", "情况", "怎么样", "多少",
)


# 时间词与“其它”内部的代词字样不是商品指代，匹配时保护这些文本区间。
_NON_REFERENCE_PATTERN = re.compile(r"这(?:个)?(?:月|星期|周|季度|年度|年|礼拜)|其它")


def _reference_span(text: str, word: str):
    """返回第一个不落在保护词段内的指代词位置，找不到时返回 None。"""
    protected = [match.span() for match in _NON_REFERENCE_PATTERN.finditer(text)]
    for match in re.finditer(re.escape(word), text):
        # 两个区间有交集就跳过，避免把“这个月”中的“这个”当商品。
        if not any(match.start() < end and match.end() > start for start, end in protected):
            return match.span()
    return None


def _non_reference_without_pronoun(text: str) -> bool:
    """有保护词段但没有真正指代时，阻止继承旧商品及模型再次改写。"""
    return bool(_NON_REFERENCE_PATTERN.search(text)) and not any(
        _reference_span(text, word) for word in _REFERENCE_WORDS
    )


def _find_product_in_history(history: list[dict], aliases: dict[str, str]):
    """从最近的消息往回找商品名：先看用户说过的话，再看助手回复。

    优先用户自己的提问（「洗衣液」），因为助手的长回答里可能提到一堆商品。
    """
    turns = list(history or [])[-_HISTORY_SCAN_TURNS:]
    for role in ("user", "assistant"):
        for turn in reversed(turns):
            # 先扫描用户消息，再用助手消息兜底。
            if str((turn or {}).get("role") or "") != role:
                continue
            text = str((turn or {}).get("content") or "")
            # 空消息没有可匹配的商品，继续向前扫描。
            if not text:
                continue
            hits = [alias for alias in aliases if alias in text]
            # 多个别名命中时取最长名称，减少短别名的干扰。
            if hits:
                alias = max(hits, key=len)
                return alias, aliases[alias]
    return None


def _should_inherit(text: str, aliases: dict[str, str]) -> bool:
    """要不要把上文那个商品"接"到这句问句上。

    适用于「有几条评价」「评价怎么样」这种没有主语、接着上一个商品问的短句；
    问句里自己带了商品名、或问的是全局（哪些商品/类目/整体）时都不接。
    """
    # 长问题按独立问题处理，不强行继承上文主语。
    if len(text) > _INHERIT_MAX_LENGTH:
        return False
    # 全店与跨商品问题不能强行限定为上轮的一个商品。
    if any(word in text for word in _GLOBAL_SCOPE_WORDS):
        return False
    # 问题已有商品名时，保留用户本轮明确给出的对象。
    if any(alias in text for alias in aliases):
        return False
    return any(word in text for word in _INHERIT_KEYWORDS)


def resolve_followup(
    question: str, history: list[dict] | None = None, product_names: list[str] | None = None
) -> tuple[str, str | None]:
    """把指代/隐含主语补成具体商品名。

    两种情形：
    1. 显式指代：问句里有「它 / 这个 / 该商品」→ 替换成上文商品名；
    2. 隐含承前：问句很短、自己没提商品、问的是业务属性（如「有几条评价」）→ 补上商品名。
    没有可指代的对象时原样返回，说明为 None。
    """
    text = (question or "").strip()
    # 无问题或无历史时保留单轮行为，不补出未知对象。
    if not text or not history:
        return question, None

    # 完整时间表达或“其它”中没有真正指代时，原问题不能再继承旧商品。
    if _non_reference_without_pronoun(text):
        return question, None

    aliases = router_rules.product_aliases(product_names or [])
    # 当前没有已知商品时无法建立指代关系。
    if not aliases:
        return question, None

    found = _find_product_in_history(history, aliases)
    # 历史里没找到商品就保留原问题，不伪造商品名。
    if found is None:
        return question, None

    _, canonical = found

    # 1) 显式指代
    for word in _REFERENCE_WORDS:
        # 按位置只替换保护词段之外的指代，保留同句里的时间词和“其它”。
        if (span := _reference_span(text, word)) is not None:
            start, end = span
            rewritten = text[:start] + canonical + text[end:]
            return rewritten, f"结合上文：把「{word}」理解为「{canonical}」"

    # 2) 隐含承前（「有几条评价」→「洗衣液 2L 有几条评价」）
    if _should_inherit(text, aliases):
        return f"{canonical} {text}", f"结合上文：接着上面的「{canonical}」问"

    return question, None


# ---------------------------------------------------------------- 意图改写（兜底）
#
# 规则法处理不了「还有别的吗」「换个角度看看」这类整句省略的追问。
# 这里加一层大模型改写，但**只在明显是短追问时**才调用，正常问题不受影响：
#   - 问句很短（≤20 字）、自己没提商品、也不是在问全局；
#   - 且规则改写没成功时才会走到这里。

_CONTINUATION_WORDS = (
    "还有", "别的", "其他", "其它", "那呢", "然后", "接着", "再来", "再说", "继续", "换个",
)
_LLM_REWRITE_MAX_LENGTH = 20  # 超过这个长度认为是完整问题，不改写
_LLM_REWRITE_MAX_OUTPUT = 60  # 改写结果过长视为异常，丢弃

_REWRITE_SYSTEM_PROMPT = """你是多轮对话的问题改写器。根据最近的对话，把用户新提出的问题改写成一句不依赖上文、可直接检索的完整问题。
规则：
1. 只补全指代与省略，不要新增原文没有的筛选条件
2. **省略式追问优先理解为「同一个商品的同一件事」**：上一轮在看某商品的评价，追问「还有别的吗」应改写为「该商品还有别的评价吗」，不要变成「还有别的商品吗」
3. 只能使用对话里出现过的商品名，不要编造
4. 如果新问题本身已经完整，或你无法确定用户想问什么，就原样输出原问题（不要猜）
5. 只输出改写后的一句话，不要解释、不要引号、不要 Markdown

示例（对话里刚看过「洗衣液 2L」的评价）：
- 「还有别的吗」→「洗衣液 2L 还有别的评价吗」
- 「它的差评呢」→「洗衣液 2L 的差评呢」
- 「换个角度看看」→「洗衣液 2L 的评价还有别的角度吗」
- 「谢谢」→「谢谢」"""


def looks_like_followup(text: str, aliases: dict[str, str]) -> bool:
    """是否值得调用大模型改写：短、自己没提商品、也不是问全局。"""
    # 超过 20 字的问题不进入模型改写，较短问题继续核对范围、商品名和续问特征。
    # 规则已保留的非指代表达不能交给模型重新补入旧商品。
    if _non_reference_without_pronoun(text):
        return False
    if not text or len(text) > _LLM_REWRITE_MAX_LENGTH:
        return False
    # 全店与跨商品问题不能强行限定为上轮的一个商品。
    if any(word in text for word in _GLOBAL_SCOPE_WORDS):
        return False
    # 问题已有商品名时，保留用户本轮明确给出的对象。
    if any(alias in text for alias in aliases):
        return False
    # 前面先排除超过 20 字、有商品名或全局范围的问题；剩余句子中，
    # 含续问词或不超过 8 字就交给模型判断，因此“谢谢”也可能进入，提示词要求原样返回。
    return any(word in text for word in _CONTINUATION_WORDS) or len(text) <= 8


def rewrite_followup_with_llm(
    question: str, history: list[dict] | None = None, product_names: list[str] | None = None
) -> tuple[str, str | None]:
    """规则改写失败时的兜底：让大模型把追问补成完整问题。

    任何异常（超时、模型报错、输出异常）都原样返回，绝不因为这一层让问答失败。
    """
    text = (question or "").strip()
    # 完整时间表达或“其它”中没有真正指代时，原问题不能再继承旧商品。
    if _non_reference_without_pronoun(text):
        return question, None

    aliases = router_rules.product_aliases(product_names or [])
    # 没有历史或不像追问时不调用模型，避免无依据改写。
    if not history or not looks_like_followup(text, aliases):
        return question, None

    lines: list[str] = []
    for turn in list(history)[-_HISTORY_SCAN_TURNS:]:
        role = "用户" if str((turn or {}).get("role") or "") == "user" else "助手"
        content = str((turn or {}).get("content") or "")[:300]
        # 只拼接有内容的消息，避免空占位干扰模型。
        if content:
            lines.append(f"{role}：{content}")
    # 截取后没有有效历史时，没有可交给模型的上下文。
    if not lines:
        return question, None

    user_prompt = "最近对话：\n" + "\n".join(lines) + f"\n\n新问题：{text}\n改写："
    try:
        rewritten = llm_service.complete_text(_REWRITE_SYSTEM_PROMPT, user_prompt).strip()
    except Exception:
        logger.warning("意图改写调用失败，沿用原问题", exc_info=True)
        return question, None

    rewritten = rewritten.strip().strip('"“”').strip()
    # 空结果和异常长结果不能交给路由，回退原问题。
    if not rewritten or len(rewritten) > _LLM_REWRITE_MAX_OUTPUT:
        return question, None
    # 模型未作补全时保持单轮行为，不显示改写成功。
    if rewritten == text:
        return question, None
    return rewritten, f"结合上文（意图改写）：{rewritten}"
