"""抽取模型选择层的回归测试（不连网、不烧积分）。

覆盖四件事：
  ① 可选清单只含"平台声明支持图片且已启用"的模型，其余一个都不放进来；
  ② 客户端递什么都被核对，不在清单里的 id 一律拒绝，且错误里带可选清单；
  ③ 目录读不到时**不放行任意 id**，退回只留默认模型并标记降级；
  ④ 实测标注与档位标签对得上（被劫持的模型不能标成"可用"）。
  ⑤ 选了被劫持的模型，入库前的分诊必须拦下（untrusted_model）。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))   # app.py 在项目根目录

import extract_models as em  # noqa: E402
import triage  # noqa: E402

FAILED: list[str] = []


def ck(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok  " if cond else "  FAIL") + "  " + name + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


class FakeCloud:
    """按平台目录的真实字段形状造三个模型：能读图 / 不能读图 / 已停用。"""

    def __init__(self, raise_exc: Exception | None = None):
        self.raise_exc = raise_exc

    def models(self):
        if self.raise_exc:
            raise self.raise_exc
        return [
            {"id": "deepseek-v4.1-flash", "name": "DS 4.1 Flash", "vendor": "deepseek",
             "supportsImages": True, "supportsReasoning": True, "enabled": True},
            {"id": "glm-5v-turbo", "name": "GLM 5V Turbo", "vendor": "zhipu",
             "supportsImages": True, "enabled": True},
            {"id": "hunyuan-chat", "name": "Hunyuan Chat", "vendor": "tencent",
             "supportsImages": False, "enabled": True},
            {"id": "retired-vision", "name": "Retired", "supportsImages": True,
             "enabled": False},
        ]


def reset() -> None:
    em._cache.update({"at": 0.0, "models": [], "error": None})


def main() -> None:
    default = "deepseek-v4.1-flash"

    print("\n① 可选清单：只放行能读图的")
    reset()
    cat = em.catalog(FakeCloud(), default, force=True)
    ids = [m["id"] for m in cat["models"]]
    ck("清单非空", bool(ids), str(ids))
    ck("默认模型在清单里", default in ids, str(ids))
    ck("文本模型被排除", "hunyuan-chat" not in ids, str(ids))
    ck("已停用模型被排除", "retired-vision" not in ids, str(ids))
    ck("默认模型排第一", ids[0] == default, str(ids))
    first = cat["models"][0]
    ck("默认档位标签正确", first["tier"] == 0 and "默认" in first["tier_label"],
       f'{first["tier"]} {first["tier_label"]}')

    print("\n② 客户端递的 id 必须被核对")
    reset()
    mid, err = em.resolve(FakeCloud(), "", default)
    ck("不传 → 用默认", mid == default and err is None, f"{mid} {err}")
    mid, err = em.resolve(FakeCloud(), "   ", default)
    ck("空白 → 用默认", mid == default and err is None, f"{mid} {err}")
    mid, err = em.resolve(FakeCloud(), default, default)
    ck("清单内 → 放行", mid == default and err is None)
    mid, err = em.resolve(FakeCloud(), "glm-5v-turbo", default)
    ck("有前科的模型也放行（靠下游拦）", mid == "glm-5v-turbo" and err is None, f"{mid} {err}")
    mid, err = em.resolve(FakeCloud(), "hunyuan-chat", default)
    ck("文本模型 → 拒绝", mid is None and (err or {}).get("code") == "model_not_allowed")
    ck("拒绝时回可选清单", default in (err or {}).get("available", []), str(err))
    mid, err = em.resolve(FakeCloud(), "gpt-4o", default)
    ck("清单外的任意 id → 拒绝", mid is None and (err or {}).get("code") == "model_not_allowed")
    mid, err = em.resolve(FakeCloud(), "../../etc/passwd", default)
    ck("畸形 id → 拒绝", mid is None, f"{mid}")

    print("\n③ 目录读不到：不放行任意 id，也不把服务堵死")
    reset()
    bad = FakeCloud(raise_exc=RuntimeError("boom"))
    mid, err = em.resolve(bad, "glm-5v-turbo", default)
    ck("降级时只认默认模型", mid is None and (err or {}).get("code") == "model_not_allowed", f"{mid}")
    mid, err = em.resolve(bad, "", default)
    ck("降级时不传 → 仍然用默认", mid == default and err is None, f"{mid} {err}")
    cat = em.catalog(bad, default)
    ck("降级被标记", cat.get("degraded") is True, str(cat.get("degraded")))
    ck("降级仍给出一个可选模型", len(cat["models"]) == 1, str(cat["models"]))

    print("\n③b 目录读过一次之后断了：用快照，但快照里的 id 仍受限")
    reset()
    em.catalog(FakeCloud(), default, force=True)
    em._cache["at"] = 0.0          # 强制过期
    mid, err = em.resolve(bad, "glm-5v-turbo", default)
    ck("过期后用快照放行清单内的 id", mid == "glm-5v-turbo" and err is None, f"{mid} {err}")
    mid, err = em.resolve(bad, "gpt-4o", default)
    ck("快照里没有的 id 仍然拒绝", mid is None, f"{mid}")

    print("\n④ 实测标注与档位一致")
    r = em.risk_of("glm-5v-turbo")
    ck("被改写过的模型标成 injection", r["risk"] == "injection", r["risk"])
    ck("hunyuan-chat 也是 injection", em.risk_of("hunyuan-chat")["risk"] == "injection")
    ck("默认模型标成 ok", em.risk_of(default)["risk"] == "ok", em.risk_of(default)["risk"])
    ck("无记录 → untested", em.risk_of("no-such-model")["risk"] == "untested")
    reset()
    cat = em.catalog(FakeCloud(), default, force=True)
    glm = next(m for m in cat["models"] if m["id"] == "glm-5v-turbo")
    ck("injection 不许标成可用", "慎用" in glm["tier_label"], glm["tier_label"])
    ck("injection 档位在默认之后", glm["tier"] > 0, str(glm["tier"]))

    print("\n⑤ 选了被劫持的模型 → 分诊必须拦下")
    iss = [{"level": "warn", "layer": "L0", "field": "", "code": "untrusted_model",
            "value": "glm-5v-turbo", "msg": "本单由 glm-5v-turbo 读出，该模型实测曾被改写"}]
    t = triage.triage(iss, [], [])
    ck("进入人工档", t["counts"]["ask"] == 1, str(t["counts"]))
    ck("需要人", t["need_human"] is True)
    a = t["ask"][0]
    ck("不给方向（不能一键改值）", a["must_choose"] is False, str(a.get("must_choose")))
    hows = [o["how"] for o in a["options"]]
    ck("只有认可/退回两条", set(hows) == {"keep", "reject"}, str(hows))
    ck("证据说清问题在来源", "来源" in (a.get("evidence") or ""), a.get("evidence"))

    print("\n⑥ 正常模型的单子不受影响")
    ok_iss = [{"level": "warn", "layer": "L4", "field": "supplier",
               "code": "label_line_fuzzy", "msg": "供应商与单据写法略有出入"}]
    t = triage.triage(ok_iss, [{"field": "supplier", "verdict": "match", "reason": "逐字一致"}],
                      [{"field": "supplier", "coverage": 1.0}])
    ck("看图一致 → 消解，不问人", t["counts"]["ask"] == 0 and t["counts"]["resolved"] == 1,
       str(t["counts"]))


    print("\n⑦ 接口层：/api/extract 真的会把不合格的模型挡在门外")
    import time as _t
    from collections import OrderedDict
    import app as appmod

    did = "testdoc" + "0" * 25
    # sid 不能是空串：get_doc 里 `if owner else None` 会把 "" 当"没有归属"，
    # 于是兜底分支永远查不到。真实请求里 sid 来自 cookie，不会是空串。
    sid = "testsid"
    appmod._SESS.setdefault(sid, OrderedDict())[did] = {
        "at": _t.time(), "sid": sid, "filename": "t.png", "image_b64": "ZmFrZQ==",
        "text": "供应商：宁波恒达塑胶制品有限公司\n合计 5600.00",
        "content_risk": {}, "words": 10, "ocr_ms": 1, "skew_deg": 0.0,
    }
    appmod._ALL[did] = sid

    class Cap(appmod.Handler):
        def __init__(self):
            self.out = None

        def _json(self, payload, code=200, head_only=False):
            self.out = (payload, code)

        def _session(self):
            return sid

    h = Cap()
    h._post_extract({"doc_id": did, "model": "hunyuan-chat"})
    body, code = h.out
    ck("文本模型 → HTTP 400", code == 400, str(code))
    ck("错误码 model_not_allowed", body.get("code") == "model_not_allowed", str(body.get("code")))
    ck("回可选清单", bool(body.get("available")), str(body)[:120])

    h2 = Cap()
    h2._post_extract({"doc_id": did, "model": "gpt-4o"})
    ck("伪造 id → 也挡", h2.out[1] == 400 and h2.out[0].get("code") == "model_not_allowed")

    # 放行路径：把模型调用换成桩，验证"实际用的模型"被记在 doc 上、
    # 并由服务端回报（前端与入库记录都以它为准）。
    calls = {}

    def fake_chat(messages, model, **kw):
        calls["model"] = model
        return ('{"doc_no":"PO20261024001","supplier":"宁波恒达塑胶制品有限公司",'
                '"order_date":"2026-10-24","total_amount":5600,"items":[]}',
                {"prompt_tokens": 10, "completion_tokens": 20})

    orig = appmod.CLOUD.chat
    appmod.CLOUD.chat = fake_chat
    try:
        h3 = Cap()
        h3._post_extract({"doc_id": did, "model": default})
        body3, code3 = h3.out
        ck("合法模型 → 200", code3 == 200 and body3.get("ok") is True, f"{code3} {str(body3)[:100]}")
        ck("确实用所选模型调用", calls.get("model") == default, str(calls.get("model")))
        ck("响应回报实际模型", body3.get("model") == default, str(body3.get("model")))
        ck("风险档一并回报", body3.get("model_risk") == "ok", str(body3.get("model_risk")))
        ck("doc 上记了模型（入库据此写 model_id）",
           appmod.get_doc(did, sid).get("extract_model") == default,
           str(appmod.get_doc(did, sid).get("extract_model")))
    finally:
        appmod.CLOUD.chat = orig

    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: {FAILED}")
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
