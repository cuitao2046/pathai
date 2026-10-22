(function() {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();

  var chart = echarts.init(document.getElementById('chart-floor'), null, { renderer: 'svg' });
  chart.setOption({
    animation: false,
    color: [accent, accent2],
    tooltip: {
      trigger: 'axis',
      appendToBody: true,
      axisPointer: { type: 'shadow' },
      backgroundColor: bg2,
      borderColor: rule,
      textStyle: { color: ink },
      formatter: function(items) {
        return items[0].axisValue + '<br>' + items.map(function(item) {
          return item.marker + item.seriesName + '：' + item.value + ' 间';
        }).join('<br>');
      }
    },
    legend: { top: 0, textStyle: { color: muted } },
    grid: { left: 48, right: 24, top: 42, bottom: 36, containLabel: true },
    xAxis: {
      type: 'category',
      data: ['一层', '二层'],
      axisLine: { lineStyle: { color: rule } },
      axisTick: { show: false },
      axisLabel: { color: muted }
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 40,
      interval: 10,
      name: '房间数',
      nameTextStyle: { color: muted },
      axisLabel: { color: muted },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: rule } }
    },
    series: [
      { name: '纳入检查', type: 'bar', data: [37, 28], barMaxWidth: 54, itemStyle: { color: accent }, label: { show: true, position: 'top', color: ink } },
      { name: '盲模式不可达', type: 'bar', data: [3, 3], barMaxWidth: 54, itemStyle: { color: accent2 }, label: { show: true, position: 'top', color: ink } }
    ]
  });
  window.addEventListener('resize', function() { chart.resize(); });
})();
