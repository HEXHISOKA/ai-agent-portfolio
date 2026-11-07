"""保守识别明确寒暄、无关话题及信息不足的输入，不调用模型。"""
import re
from app.agent import router_rules

REPLIES = {
    "smalltalk": "你好！我是电商数据分析助手，可以查询商品、订单和评价。请告诉我商品名称或你想分析的问题。",
    "off_topic": "这个问题不属于当前电商数据分析范围。可以补充商品名称、订单信息或评价问题后再问我。",
    "vague": "还缺少可查询的信息。请补充商品名称，或说明要查的评价、价格、库存及时间范围。",
}
MESSAGES = {"smalltalk": "日常对话", "off_topic": "问题与当前业务无关", "vague": "需要补充查询信息"}
_GREETING_CHUNKS = tuple(sorted((
    "你好", "您好", "哈喽", "哈啰", "嗨", "hi", "hello", "hey", "ok", "okay",
    "thanks", "thx", "thankyou", "早上好", "中午好", "下午好", "晚上好", "早安", "晚安",
    "在吗", "在么", "在不在", "有人吗", "测试", "test", "谢谢", "多谢", "感谢",
    "你是谁", "你叫什么", "你能做什么", "你会什么", "自我介绍", "辛苦了", "好的", "收到", "明白了", "再见", "拜拜", "bye",
), key=len, reverse=True))
_META_QUERIES = {"你是谁", "你叫什么", "你能做什么", "你会什么", "有什么功能", "怎么用", "怎么问", "使用说明", "自我介绍", "介绍一下你", "随便聊聊", "聊聊天"}
_BUSINESS_SIGNALS = (
    "商品", "产品", "订单", "评价", "评论", "评分", "星级", "差评", "好评", "口碑",
    "销量", "销售", "库存", "退货", "售后", "上架", "下架", "类目", "店铺", "买家",
    "包装", "物流", "脱胶", "开胶", "缩水", "杂音", "磨脚", "破损", "异味", "掉色",
    "漏水", "卡顿", "断连", "变形", "跑鞋", "耳机", "洗衣液", "保温杯",
)
_OFF_TOPIC = (
    "天气", "气温", "下雨", "空气质量", "星期几", "周几", "几点", "今天日期", "今天几号",
    "笑话", "段子", "古诗", "写诗", "对联", "作文", "翻译", "语法", "微积分", "方程",
    "电影", "电视剧", "综艺", "小说", "音乐", "歌曲", "游戏", "股票", "基金", "汇率",
    "彩票", "房价", "新闻", "足球", "篮球", "减肥", "菜谱", "旅游", "星座", "运势",
    "学习路线", "面试题", "写代码", "编程",
)
# 裸名词没有说明对象或目标；缺陷主题则可以直接作为语义检索问题。
_VAGUE_NOUNS = {"商品", "产品", "价格", "库存", "评价", "评论", "订单"}
_SHORT_THEMES = {"脱胶", "开胶", "缩水", "杂音", "磨脚", "破损", "异味", "掉色", "漏水", "卡顿", "断连", "变形", "差评", "好评", "退货", "售后", "查价"}
_REFERENCE_ONLY = re.compile(r"(?:它|这个|那个|这款|该商品|这个商品)(?:呢|的呢)?$")
_QUESTION_WORDS = ("吗", "呢", "怎么", "如何", "为什么", "什么", "多少", "哪些", "哪个", "几")


def classify_input(question: str, product_names: list[str]) -> str | None:
    """只拦截明确类型；未识别的主题交给原查询流程，不凭长度判断无关。"""
    raw = question.strip()
    if not raw:
        return None
    # 商品原名或已有别名优先，即使名称与寒暄、无关词重合也不误拦。
    if any(alias in raw for alias in router_rules.product_aliases(product_names)):
        return None
    compact = re.sub(r"[\W_]+", "", raw, flags=re.UNICODE).lower()
    greeting = compact.rstrip("呀啊哦呢吧嘛啦咯哟诶嗯")
    if not greeting:
        return "smalltalk"
    rest = greeting
    while rest:
        chunk = next((word for word in _GREETING_CHUNKS if rest.startswith(word)), None)
        if chunk is None:
            break
        rest = rest[len(chunk):]
    if not rest or greeting in _META_QUERIES:
        return "smalltalk"
    if compact in _SHORT_THEMES:
        return None
    if compact in _VAGUE_NOUNS or _REFERENCE_ONLY.fullmatch(compact):
        return "vague"
    # 业务对象或缺陷优先，避免把“耳机听音乐有杂音”等真实业务问题当跑题。
    if any(word in raw for word in _BUSINESS_SIGNALS):
        return None
    if any(word in raw for word in _OFF_TOPIC):
        return "off_topic"
    # 数字可能是编号，疑问词可能构成“贵吗”这类短问题，不能一律拦截。
    if len(compact) <= 2 and not any(ch.isdigit() for ch in compact) and not any(word in raw for word in _QUESTION_WORDS):
        return "vague"
    return None
