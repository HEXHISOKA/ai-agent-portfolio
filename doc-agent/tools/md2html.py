"""把 Markdown 指南转成可离线阅读 / 可打印的单文件 HTML。

用法：
    python tools/md2html.py <input.md> <output.html> "页面标题"

设计考虑（不是随便套个模板）：
  · 单文件，不引外部 CSS/JS —— 面试现场可能没网，双击就能开
  · 正文宽度按中文阅读调（约 40 个汉字一行），行高 1.75
  · 表格允许横向滚动而不是被压扁 —— 文档里有大量四列表格
  · 左侧目录由 h2 自动生成，手机（<900px）自动收起
  · 打印样式单独写：隐藏目录、避免表格/代码块跨页断开
"""
from __future__ import annotations

import html
import re
import sys

import markdown


CSS = """
:root{
  --ink:#1b1b1a; --ink2:#4a4a46; --ink3:#7a7a74;
  --line:#e4e1d8; --bg:#fdfcf8; --card:#fff; --wash:#f6f4ec;
  --accent:#8a6d1f; --accent2:#b8862b;
  --code-bg:#23241f; --code-ink:#e8e6dc;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei","Hiragino Sans GB",sans-serif;
  font-size:16px; line-height:1.75; letter-spacing:.01em;
}
.wrap{display:flex; max-width:1240px; margin:0 auto; padding:0 24px;}
nav{
  position:sticky; top:0; align-self:flex-start; flex:0 0 232px;
  max-height:100vh; overflow-y:auto; padding:28px 20px 40px 0;
  font-size:13.5px; line-height:1.5;
}
nav::-webkit-scrollbar{width:6px}
nav::-webkit-scrollbar-thumb{background:#dcd8cb; border-radius:3px}
nav .ttl{font-size:11px; letter-spacing:.14em; color:var(--ink3); margin:0 0 12px}
nav a{display:block; color:var(--ink2); text-decoration:none; padding:4px 0 4px 9px;
  border-left:2px solid transparent; margin-left:-11px}
nav a:hover{color:var(--accent); border-left-color:var(--accent2); background:#faf7ee}
nav a.sub{padding-left:22px; font-size:12.5px; color:var(--ink3)}
main{flex:1 1 auto; min-width:0; padding:32px 0 120px; max-width:900px}
header.top{border-bottom:2px solid var(--ink); padding-bottom:18px; margin-bottom:34px}
header.top h1{font-size:29px; line-height:1.35; margin:0 0 8px; letter-spacing:-.01em}
header.top .meta{color:var(--ink3); font-size:13px}
h1,h2,h3,h4{line-height:1.4; letter-spacing:-.005em}
h2{font-size:22px; margin:52px 0 14px; padding-top:12px; border-top:1px solid var(--line)}
h2:first-of-type{border-top:none; margin-top:10px}
h3{font-size:17.5px; margin:32px 0 10px; color:#2a2a27}
h4{font-size:15.5px; margin:24px 0 8px; color:var(--ink2)}
p{margin:11px 0}
strong{font-weight:650; color:#111}
em{font-style:normal; background:#fdf1c9; padding:0 3px; border-radius:3px}
a{color:#8a6d1f}
hr{border:none; border-top:1px solid var(--line); margin:34px 0}
blockquote{
  margin:16px 0; padding:12px 18px; background:var(--wash);
  border-left:3px solid var(--accent2); border-radius:0 6px 6px 0; color:#3a3a35;
}
blockquote p{margin:6px 0}
code{
  font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace;
  font-size:13.5px; background:#f0ece0; padding:1.5px 5px; border-radius:4px; color:#4a3a12;
}
pre{
  background:var(--code-bg); color:var(--code-ink); padding:16px 18px; border-radius:9px;
  overflow-x:auto; margin:16px 0; line-height:1.62;
}
pre code{background:none; color:inherit; padding:0; font-size:13px}
table{
  border-collapse:collapse; width:100%; margin:18px 0; font-size:14px; display:block;
  overflow-x:auto; white-space:normal;
}
thead th{
  background:var(--wash); text-align:left; font-weight:650; color:#3a3628;
  border-bottom:2px solid #ddd6c2; padding:9px 11px; white-space:nowrap;
}
tbody td{border-bottom:1px solid var(--line); padding:9px 11px; vertical-align:top}
tbody tr:nth-child(even){background:#fbfaf4}
tbody tr:hover{background:#faf6e8}
td code, th code{font-size:12.5px}
ul,ol{padding-left:24px; margin:11px 0}
li{margin:5px 0}
li>ul,li>ol{margin:5px 0}
.tag{display:inline-block; font-size:11px; padding:1px 7px; border-radius:20px;
  background:#efe7cd; color:#6b5518; vertical-align:2px; margin-left:6px}
footer{margin-top:70px; padding-top:18px; border-top:1px solid var(--line);
  color:var(--ink3); font-size:12.5px}

@media (max-width:900px){
  .wrap{display:block; padding:0 16px}
  nav{position:static; max-height:none; padding:16px 0 0; border-bottom:1px solid var(--line);
      margin-bottom:8px; display:none}
  nav.open{display:block}
  main{padding:18px 0 80px; max-width:100%}
  body{font-size:15.5px}
  h2{font-size:20px; margin-top:38px}
  header.top h1{font-size:23px}
  .toc-btn{display:block !important}
}
.toc-btn{display:none; position:fixed; right:14px; bottom:18px; z-index:9;
  background:#2b2a24; color:#fff; border:none; border-radius:22px;
  padding:10px 16px; font-size:13px; box-shadow:0 4px 14px rgba(0,0,0,.22)}

@media print{
  nav,.toc-btn{display:none !important}
  .wrap{display:block; max-width:none; padding:0}
  main{max-width:none; padding:0}
  body{font-size:10.5pt; background:#fff}
  h2{page-break-after:avoid; margin-top:22pt}
  h3{page-break-after:avoid}
  table,pre,blockquote{page-break-inside:avoid}
  tr{page-break-inside:avoid}
  a{color:#000; text-decoration:none}
  pre{background:#f4f4f0; color:#111; border:1px solid #ddd}
  pre code{color:#111}
}
"""

JS = """
document.querySelectorAll('nav a').forEach(function(a){
  a.addEventListener('click', function(){
    if (window.innerWidth <= 900) document.querySelector('nav').classList.remove('open');
  });
});
var btn = document.querySelector('.toc-btn');
if (btn) btn.addEventListener('click', function(){
  document.querySelector('nav').classList.toggle('open');
});
"""


def slug(i: int) -> str:
    return f"s{i}"


def build(md_text: str, title: str) -> str:
    lines = md_text.split("\n")

    # 1) 抽出首个 H1 当页面标题，避免正文里重复一个大标题
    body_start = 0
    doc_title = title
    for i, ln in enumerate(lines):
        if ln.startswith("# "):
            doc_title = ln[2:].strip()
            body_start = i + 1
            break
        if ln.strip() and not ln.startswith(">"):
            break
    # 保留 H1 之前的内容（通常是导语引用），去掉 H1 本身
    head_part = [
        ln for j, ln in enumerate(lines[:body_start]) if not ln.startswith("# ")
    ]
    rest = "\n".join(head_part + lines[body_start:])

    # 2) 给 h2/h3 加锚点 id（用 attr_list 语法，比事后正则补 id 可靠），同时生成目录
    toc: list[tuple[int, str, str]] = []
    out_lines = []
    n = 0
    in_code = False
    for ln in rest.split("\n"):
        if ln.strip().startswith("```"):
            in_code = not in_code
            out_lines.append(ln)
            continue
        if not in_code and re.match(r"^#{2,3} ", ln):
            level = 2 if ln.startswith("## ") else 3
            raw = ln[level + 1:].strip()
            text = re.sub(r"[`*]", "", raw)
            n += 1
            sid = slug(n)
            toc.append((level, text, sid))
            out_lines.append(f"{ln} {{#{sid}}}")
        else:
            out_lines.append(ln)
    md2 = "\n".join(out_lines)

    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "attr_list"])
    body = md.convert(md2)

    nav = ['<div class="ttl">目录</div>']
    for level, text, sid in toc:
        cls = "" if level == 2 else ' class="sub"'
        nav.append(f'<a href="#{sid}"{cls}>{html.escape(text)}</a>')

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(doc_title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<nav>{''.join(nav)}</nav>
<main>
<header class="top">
  <h1>{html.escape(doc_title)}</h1>
  <div class="meta">技术复盘 · 生成于离线单页，可直接打印或手机阅读</div>
</header>
{body}
<footer>本页由 Markdown 生成，内容与同目录 .md 文件一致。</footer>
</main>
</div>
<button class="toc-btn">目录</button>
<script>{JS}</script>
</body>
</html>"""


def main() -> None:
    if len(sys.argv) < 3:
        print("用法: python tools/md2html.py <in.md> <out.html> [标题]")
        raise SystemExit(2)
    src, dst = sys.argv[1], sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 else "文档"
    with open(src, encoding="utf-8") as f:
        text = f.read()
    with open(dst, "w", encoding="utf-8") as f:
        f.write(build(text, title))
    print(f"已生成 {dst}（{len(text)} 字符）")


if __name__ == "__main__":
    main()
