# -*- coding: utf-8 -*-
import io, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src", "render_interactive.py")
JS = os.path.join(BASE, "debug", "search_feature.js")

s = open(SRC, encoding="utf-8").read()
js = open(JS, encoding="utf-8").read()

# ---- 1) 搜索 JS：翻倍花括号（与最终脚本块 {{ -> { 还原机制一致）----
js2 = js.replace("{", "{{").replace("}", "}}")
script_block = "\n<script>\n" + js2 + "\n</script>\n"

anchor_js = "</div><!-- /svg-wrapper -->\n"
assert anchor_js in s, "anchor_js not found"
idx = s.index(anchor_js) + len(anchor_js)
s = s[:idx] + script_block + s[idx:]

# ---- 2) CSS（翻倍花括号，注入到 .zoom-info 规则之后）----
css = (
    ".search-panel {{ position: absolute; top: 10px; left: 10px; z-index: 20; width: 250px; font-size: 13px; }}\n"
    ".search-panel input {{ width: 100%; box-sizing: border-box; padding: 8px 10px; border: 1px solid #c7c7c7; border-radius: 7px; font-size: 13px; background: #fff; box-shadow: 0 1px 5px rgba(0,0,0,0.12); outline: none; }}\n"
    ".search-panel input:focus {{ border-color: #1565C0; box-shadow: 0 1px 7px rgba(21,101,192,0.25); }}\n"
    ".search-results {{ margin-top: 6px; background: #fff; border: 1px solid #e0e0e0; border-radius: 7px; max-height: 320px; overflow-y: auto; box-shadow: 0 6px 18px rgba(0,0,0,0.16); display: none; }}\n"
    ".search-results.show {{ display: block; }}\n"
    ".sr-item {{ padding: 7px 10px; cursor: pointer; border-bottom: 1px solid #f1f1f1; display: flex; flex-direction: column; gap: 2px; }}\n"
    ".sr-item:last-child {{ border-bottom: none; }}\n"
    ".sr-item:hover, .sr-item.active {{ background: #E3F2FD; }}\n"
    ".sr-title {{ font-weight: 600; color: #222; }}\n"
    ".sr-meta {{ font-size: 11px; color: #888; }}\n"
    ".sr-tag {{ display: inline-block; background: #EEEEEE; color: #555; border-radius: 3px; padding: 0 5px; margin-right: 5px; font-size: 11px; }}\n"
    ".search-empty {{ padding: 10px; color: #999; font-size: 12px; }}\n"
)
anchor_css = (".zoom-info {{ position: absolute; bottom: 10px; right: 10px; background: rgba(255,255,255,0.9); "
              "padding: 4px 8px; border-radius: 4px; font-size: 12px; color: #666; z-index: 10; "
              "border: 1px solid #ddd; }}\n")
assert anchor_css in s, "anchor_css not found"
s = s.replace(anchor_css, anchor_css + css, 1)

# ---- 3) HTML 标记：在 #svg-container 内加入搜索面板 ----
anchor_markup = "<div id=\"svg-container\">\n"
assert anchor_markup in s, "anchor_markup not found"
markup = (
    "<div id=\"svg-container\">\n"
    "<div class=\"search-panel\">\n"
    "  <input id=\"search-input\" type=\"text\" placeholder=\"按名称 / 编号 / 类型模糊搜索…\" autocomplete=\"off\">\n"
    "  <div id=\"search-results\" class=\"search-results\"></div>\n"
    "</div>\n"
)
s = s.replace(anchor_markup, markup, 1)

# ---- 4) 将 #floor-jump 移到顶部居中，避免与搜索框重叠 ----
anchor_floor = "#floor-jump {{ position: absolute; top: 10px; left: 10px; z-index: 10; display: flex; gap: 4px; }}\n"
new_floor = "#floor-jump {{ position: absolute; top: 10px; left: 50%; transform: translateX(-50%); z-index: 10; display: flex; gap: 4px; }}\n"
assert anchor_floor in s, "anchor_floor not found"
s = s.replace(anchor_floor, new_floor, 1)

open(SRC, "w", encoding="utf-8").write(s)
print("INJECT_DONE")
