import re

from sqlalchemy import text

from app.database import query_engine, QuerySessionLocal
from app.agent import router_rules

# 编号、统计与明确筛选条件优先交给 SQL，避免拿五条候选代替全量查询。
STRUCTURED_PATTERN = re.compile(
    r"订单|(?<![A-Za-z0-9])EO\d{6,}(?![A-Za-z0-9])|库存|价格|售价|销量|销售额|金额|"
    # 编号后允许直接接数字，也支持“为”、冒号或等号。
    r"(?:商品|产品|评价|评论)\s*(?:id|编号)\s*(?:[:：=]|为)?\s*\d+|"
    r"统计|数量|总数|总计|总共|合计|平均|排名|最多|最少|最高|最低|占比|多少|几\s*[个条笔件]|"
    r"低分|上架|下架|待复核|"
    r"(?:评分|星级)\s*(?:[<>=≤≥]+|低于|高于|不超过|不少于)?\s*\d|"
    r"[1-5一二两三四五]\s*(?:分(?!\s*钟)|星)|\d+(?:\.\d+)?\s*元|"
    r"(?:最近|过去|近)\s*[0-9一二两三四五六七八九十]+\s*(?:天|日|周|月)|"
    r"今天|昨天|本周|上周|本月|上月|今年|\d{4}[-年]\d{1,2}",
    re.IGNORECASE,
)


def load_product_names() -> list[str]:
    """读取当前商品完整名称，供路由判断问题是否点名商品。"""
    # 没有只读连接就无法读取名称，直接提示配置，不把查询失败当成没有商品。
    if query_engine is None:
        raise ValueError("请先配置 QUERY_DATABASE_URL")
    with QuerySessionLocal() as db:
        return list(db.execute(text("SELECT name FROM products ORDER BY id")).scalars())


def decide_query_route(question: str, product_names: list[str]) -> dict[str, str]:
    """接收问题和已知的商品名，返回 查询方式、语义类型和选择理由"""
    question = question.strip()
    if not question:
        raise ValueError("对话的内容不能为空")

    # 全店范围要查数据库，不能把语义Top K候选当作全店商品列表。
    if re.search(r"(?:全店|全站|全库|所有|全部).*(?:商品|产品)", question):
        return {
            "route": "sql",
            "kind": "",
            "reason": "明确要求查询全店商品范围",
        }

    if STRUCTURED_PATTERN.search(question):
        return {
            "route": "sql",
            "kind": "",
            "reason": "检测到了编号、统计或者明确的筛选条件",
        }

    # 复用商品原名与去规格别名；不把“跑鞋”这类尾词扩成具体商品。
    aliases = router_rules.product_aliases(product_names)
    if any(alias in question for alias in aliases):
        return {
            "route": "sql",
            "kind": "",
            "reason": "检测到了商品名称或省略规格的别名",
        }

    if "评价" in question or "评论" in question:
        return {
            "route": "semantic",
            "kind": "review",
            "reason": "按评价内容含义进行语义检索",
        }

    if "商品" in question or "产品" in question:
        return {
            "route": "semantic",
            "kind": "product",
            "reason": "按商品描述含义进行语义检索",
        }

    return {
        "route": "semantic",
        "kind": "review",
        "reason": "未识别到明确的条件，默认按语义进行检索",
    }


def expand_product_aliases(question: str, product_names: list[str]) -> str:
    """仅为SQL生成补全已知商品别名，保留评分、时间、否定和编号等原条件。"""
    aliases = router_rules.product_aliases(product_names)
    if not aliases:
        return question
    names = {name for name in product_names if name}
    candidates = {}
    for name in names:
        core = re.split(r"[\s　]+", name)[0].strip()
        candidates.setdefault(core, set()).add(name)

    def replace(match):
        alias = match.group()
        # 长原名优先匹配，完整名称不再追加一次规格。
        if alias in names:
            return alias
        # 多种规格共享别名时不能任取第一项，要求用户提供完整商品名。
        if len(candidates.get(alias, ())) != 1:
            raise ValueError(f"商品别名「{alias}」对应多个规格，请补充完整商品名称")
        return aliases[alias]

    pattern = "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
    # 单次替换只扫描原文，补全后的名称不会再次被短别名命中。
    return re.sub(pattern, replace, question)
