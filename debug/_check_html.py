# -*- coding: utf-8 -*-
import re
html = open("result/floor_layout_v9_interactive.html", encoding="utf-8").read()
i = html.find("</div><!-- /svg-wrapper -->")
print("--- context after svg-wrapper close ---")
print(html[i:i+450])
leg = html.find('id="legend-panel"')
print("legend-panel at:", leg, "(should be after the injected script)")
opens = len(re.findall(r"<script[ >]", html))
closes = len(re.findall(r"</script>", html))
print("script open tags:", opens, "close tags:", closes)
