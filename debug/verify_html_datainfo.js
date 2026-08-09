// 验证 HTML 中所有 data-info JSON 可解析（房间详情回归检查）
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf-8');
let bad = 0, total = 0;
// data-info 属性用单引号包裹，JSON 内部用双引号；JSON 内容不含单引号（dict 已转 json）
const re = /data-info='(\{.*?\})'/g;
let m;
while ((m = re.exec(html))) {
  total++;
  const raw = m[1];
  try {
    JSON.parse(raw);
  } catch (e) {
    bad++;
    if (bad <= 5) console.log('BAD:', raw.slice(0, 220));
  }
}
console.log('data-info 总数:', total, '解析失败:', bad);
process.exit(bad > 0 ? 1 : 0);
