import re


def product_aliases(known_product_names: list[str]) -> dict[str, str]:
    """商品别名表：原名 + 去掉规格后缀的名字（第一个空格前的部分）。

    库里商品名带规格（「洗衣液 2L」「保温杯 500ml」），用户往往只说「洗衣液」。
    只按全名匹配会漏，明明点名了商品却走语义检索，答案只能从评价文本里拼凑。

    刻意不加「尾词」别名（如把「轻云运动跑鞋」缩成「跑鞋」）：那样会把
    「跑鞋脱胶」这类主题型问题也拉去 SQL，丢掉语义检索在措辞无关场景的价值。
    """
    alias: dict[str, str] = {}
    for name in known_product_names or []:
        # 空名称不能作为匹配词，跳过无效数据。
        if not name:
            continue
        alias.setdefault(name, name)
        core = re.split(r"[\s　]+", name)[0].strip()
        # 过短别名容易误命中，仅保留至少两个字符的名称。
        if len(core) >= 2:
            alias.setdefault(core, name)
    return alias
