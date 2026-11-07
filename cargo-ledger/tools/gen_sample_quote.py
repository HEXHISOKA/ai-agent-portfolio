"""生成一张模拟档口报价单，用来验证「图片 → 结构化行」的端到端链路。

为什么不用真实客户图：真实图里有客户名、档口内部价，不该在我这里留副本。
这张图结构上与真实报价单一致（型号 × 配置 × 颜色档位），足以验证：
  1. 服务端能否把图送到模型
  2. 模型能否读出多颜色档位的分层
  3. "35××" 这类占位能否被正确留空而不是瞎猜
"""
from PIL import Image, ImageDraw, ImageFont

W, H = 1180, 620
img = Image.new("RGB", (W, H), "#ffffff")
d = ImageDraw.Draw(img)

FONT = "C:/Windows/Fonts/msyh.ttc"
f_title = ImageFont.truetype(FONT, 34)
f_head = ImageFont.truetype(FONT, 26)
f_row = ImageFont.truetype(FONT, 25)

d.rectangle([0, 0, W, 86], fill="#1f2937")
d.text((30, 24), "郑州长弘  10.28  安卓报价", font=f_title, fill="#ffffff")
d.text((W - 250, 30), "单位：元", font=f_head, fill="#d1d5db")

head_y = 110
for i, label in enumerate(["型号", "12+256", "12+512"]):
    d.text((30 + i * 380, head_y), label, font=f_head, fill="#6b7280")
d.line([20, head_y + 40, W - 20, head_y + 40], fill="#e5e7eb", width=2)

rows = [
    ("Mate80", "混色 3800", "混色 4330"),
    ("小米17", "黑 3700 / 白粉 3800", "黑 4200 / 白 4180 / 粉蓝 4100"),
    ("K90", "混色 2500", "—"),
    ("荣耀500pro", "白橙 3550", "35××"),
]
y = head_y + 60
for model, a, b in rows:
    d.text((30, y), model, font=f_row, fill="#111827")
    d.text((410, y), a, font=f_row, fill="#111827")
    d.text((790, y), b, font=f_row, fill="#111827")
    d.line([20, y + 40, W - 20, y + 40], fill="#f3f4f6", width=1)
    y += 62

d.text((30, H - 60), "备注：以上为档口现款价，不含运费，不退不换", font=f_head, fill="#9ca3af")

out = "sample_quote.png"
img.save(out)
print("saved", out, img.size)
