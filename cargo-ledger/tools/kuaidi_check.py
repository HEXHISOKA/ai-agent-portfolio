"""物流查询连通性诊断（只查不发，不碰台账数据）。

用途：填好快递鸟 key 之后，先用它把真实单号跑通，再去动台账。
台账那边一旦 provider 换成 kdniao，错一条就会写错「物流状态」，
所以在外面先验证更省事。

用法：
    python tools/kuaidi_check.py --config          # 只看配置填没填全
    python tools/kuaidi_check.py SF1234567890123
    python tools/kuaidi_check.py SF1234567890123 --phone 0234
    python tools/kuaidi_check.py JD0012345678 --phone 0234 --provider kdniao
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import carriers  # noqa: E402


def load_cfg():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f).get("kuaidi", {})


def show_config(cfg):
    provider = cfg.get("provider", "mock")
    eid = (cfg.get("kdniao_ebusiness_id") or "").strip()
    key = (cfg.get("kdniao_app_key") or "").strip()
    print("当前 provider:", provider)
    print("  快递鸟 EBusinessID:", (eid[:4] + "…" + eid[-3:]) if len(eid) > 8 else (eid or "（未填）"))
    print("  快递鸟 AppKey    :", ("已填，长度 %d" % len(key)) if key else "（未填）")
    ok = True
    if provider == "kdniao" and (not eid or not key):
        print("\n  ⚠️ provider 是 kdniao 但 key 没填全，查询必然失败。")
        print("     去 www.kdniao.com 登录 → 用户中心 → 我的会员中心，")
        print("     复制「用户ID」填 kdniao_ebusiness_id，「API Key」填 kdniao_app_key。")
        ok = False
    if provider == "mock":
        print("\n  ⚠️ 现在是 mock：轨迹是按单号哈希生成的假数据，只用于联调。")
        print("     要查真实轨迹，把 config.json 的 kuaidi.provider 改成 kdniao 并填 key。")
        ok = False
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tracking_no", nargs="?", help="快递单号")
    ap.add_argument("--phone", default="", help="收件人或寄件人手机号后四位")
    ap.add_argument("--carrier", default="", help="指定承运商中文名，如 顺丰")
    ap.add_argument("--provider", default="", help="临时覆盖 provider：mock / kdniao / kuaidi100")
    ap.add_argument("--config", action="store_true", help="只打印配置状态")
    ap.add_argument("--probe", action="store_true",
                    help="体检账号：区分「凭证错」和「没买套餐」")
    a = ap.parse_args()

    cfg = load_cfg()
    if a.provider:
        cfg = dict(cfg, provider=a.provider)

    print("=" * 62)
    show_config(cfg)

    if a.probe:
        print("\n" + "=" * 62)
        print("账号体检（会发一条不存在的探针单号，不消耗真实查询次数）")
        r = carriers.probe_kdniao(cfg)
        print("  用户ID:", r.get("ebusiness_id") or "（未填）")
        for s in r.get("steps", []):
            flag = "✅" if s.get("ok") else "❌"
            print("  %s %-18s %s" % (flag, s.get("endpoint", ""),
                                     s.get("raw_reason") or s.get("reason") or ""))
        print("\n  判定:", r.get("verdict"))
        print("  建议:", r.get("advise"))
        return 0 if r.get("verdict") == "working" else 1

    if a.config or not a.tracking_no:
        return 0

    no = a.tracking_no.strip()
    cands = carriers.guess_carriers(no)
    print("\n" + "=" * 62)
    print("单号:", no, "｜手机尾号:", a.phone or "（未提供）")
    if not cands:
        print("❌ 这个单号的前缀认不出来，需要人工指定承运商（--carrier 顺丰）")
        return 2
    print("候选承运商:", " → ".join("%s(%s)" % (n, c) for n, c in cands))

    # 逐个候选试；真实通道下一个单号可能命中多家，所以不只看第一家
    hit = False
    for name, code in cands:
        print("\n" + "-" * 62)
        print("试 %s (%s)" % (name, code))
        try:
            res = carriers.query(no, a.phone, name, cfg)
        except Exception as e:  # noqa: BLE001
            print("  ❌ 异常:", type(e).__name__, e)
            continue
        if not res.get("ok"):
            print("  ❌ 失败:", res.get("error"))
            continue
        hit = True
        print("  ✅ 成功")
        print("     状态:", res.get("status"))
        print("     签收:", res.get("signed"))
        print("     最新:", res.get("last_trace") or "（无轨迹）")
        traces = res.get("traces") or []
        print("     轨迹 %d 条%s:" % (len(traces), "（按时间倒序，只列前 6 条）" if len(traces) > 6 else ""))
        for t in traces[:6]:
            print("       %s  %s" % (t.get("time", ""), t.get("desc", "")))
        print("     → 台账会写成：承运商=%s 状态=%s" % (res.get("carrier") or name, res.get("status")))
        break

    print("\n" + "=" * 62)
    if (cfg.get("provider") or "mock") == "mock":
        print("结论：这是 mock 通道的假轨迹（按单号哈希生成的），只能证明解析链路通，")
        print("      不代表快递鸟真的接通了。要验真连通性，加 --provider kdniao 并填好 key。")
        return 0
    if hit:
        print("结论：这条单号能查到真实轨迹，可以用了。")
        return 0
    print("结论：没查到。先跑 `--probe` 分清是哪一层：")
    print("  凭证/签名错  → 改 config.json 的 id / key")
    print("  没有可用套餐 → 去 www.kdniao.com 用户中心 → 我的服务，开通「即时查询」")
    print("                 （免费版要先实名认证；付费版按次数购买）")
    print("  都没问题     → 才轮到单号本身：顺丰/京东必须给「收件人或寄件人」手机尾号，")
    print("                 下单人手机号不算；刚下单未揽收任何接口都查不到")
    return 1


if __name__ == "__main__":
    sys.exit(main())
