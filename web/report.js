/* Local, dependency-free report renderer. Business labels are always text nodes. */
function buildBusinessReport(report, plainText) {
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const number = (v, decimals = 2) => Number(v).toLocaleString('zh-CN', {minimumFractionDigits: decimals, maximumFractionDigits: decimals});
  const signed = v => `${v > 0 ? '+' : ''}${number(v)}`;
  const root = el('section', undefined, 'business-report');
  const p = report.periods;
  const current = report.summary.find(r => r.period === 'current');
  const previous = report.summary.find(r => r.period === 'previous');
  const delta = Number(current.amount) - Number(previous.amount);
  const header = el('header', undefined, 'report-header');
  header.append(el('span', 'STORE REVIEW · 经营洞察', 'report-eyebrow'), el('h2', report.title));
  header.append(el('p', `${p.start} — ${p.end}`), el('p', `对比 ${p.previous_start} — ${p.previous_end}`, 'report-muted'));
  root.append(header);
  const lead = delta === 0 ? '本期销售额与此前持平。' : `本期销售额${delta > 0 ? '增加' : '减少'} ${number(Math.abs(delta))} 元。`;
  root.append(el('p', lead, 'report-lead'));
  report.warnings.forEach(text => root.append(el('p', text, 'report-warning')));
  const cards = el('div', undefined, 'report-kpis');
  const average = r => r.receipts ? Number(r.amount) / r.receipts : null;
  for (const [label, now, before, unit, precision] of [
    ['销售额', Number(current.amount), Number(previous.amount), '元', 2],
    ['小票数', current.receipts, previous.receipts, '张', 0],
    ['全店客单价', average(current), average(previous), '元', 2],
  ]) {
    const card = el('div', undefined, 'report-kpi');
    card.append(el('span', label), el('strong', now === null ? '暂无数据' : `${number(now, precision)} ${unit}`));
    const change = now === null || before === null || before <= 0 ? '基数不足，不计算变化率' : `${now >= before ? '+' : ''}${((now-before)/before*100).toFixed(1)}% 较此前`;
    card.append(el('p', change, now !== null && before !== null && now >= before ? 'report-up' : 'report-down'));
    card.append(el('small', `此前 ${before === null ? '暂无数据' : number(before, precision) + ' ' + unit}`));
    cards.append(card);
  }
  root.append(cards);
  for (const dimension of ['类别', '商品']) {
    const section = el('section', undefined, 'report-chart-section');
    section.append(el('h3', `${dimension}变化主要体现在哪里`), el('p', '金额变化 · 左侧为减少，右侧为增加 · 各展示前3项', 'report-muted'));
    const all = report.breakdowns[dimension].map(r => ({...r, delta: Number(r.current)-Number(r.previous)}));
    const up = all.filter(r => r.delta>0).sort((a,b)=>b.delta-a.delta).slice(0,3);
    const down = all.filter(r => r.delta<0).sort((a,b)=>a.delta-b.delta).slice(0,3);
    const selected = [...up,...down];
    const scale = Math.max(1, ...selected.map(r => Math.abs(r.delta)));
    const chart = el('div', undefined, 'report-bars');
    chart.setAttribute('aria-label', `${dimension}销售额变化图，单位元`);
    for (const row of selected) {
      const line = el('div', undefined, 'report-bar-row');
      const label = el('span', row.name || '未命名', 'report-bar-label');
      label.title = `${row.name}（${row.id}）`;
      const track = el('div', undefined, 'report-bar-track');
      track.setAttribute('aria-hidden', 'true');
      const bar = el('span', undefined, `report-bar ${row.delta>0 ? 'positive' : 'negative'}`);
      bar.style.width = `${Math.abs(row.delta)/scale*50}%`;
      bar.style.left = `${row.delta>0 ? 50 : 50-Math.abs(row.delta)/scale*50}%`;
      track.append(bar);
      line.append(label, track, el('strong', `${signed(row.delta)} 元`, row.delta>0 ? 'report-up' : 'report-down'));
      chart.append(line);
    }
    if (!selected.length) chart.append(el('p', '本期间没有金额变化项。'));
    section.append(chart);
    const remainder = delta - selected.reduce((sum,r)=>sum+r.delta,0);
    section.append(el('p', `其余${dimension}净变化 ${signed(remainder)} 元 · 全部净变化 ${signed(delta)} 元，与全店对账一致。`, 'report-reconciliation'));
    const details = el('details');
    details.append(el('summary', `查看全部${dimension}数据（${all.length}项）`));
    const wrap = el('div', undefined, 'report-table-wrap');
    const table = el('table');
    const head = el('tr');
    ['名称 / 编号', '此前（元）', '当前（元）', '变化（元）'].forEach(v=>head.append(el('th',v)));
    const thead = el('thead'); thead.append(head); table.append(thead);
    const tbody = el('tbody');
    all.sort((a,b)=>Math.abs(b.delta)-Math.abs(a.delta)).forEach(r=>{
      const tr = el('tr');
      [`${r.name} / ${r.id}`, number(r.previous), number(r.current), signed(r.delta)].forEach(v=>tr.append(el('td',v)));
      tbody.append(tr);
    });
    table.append(tbody); wrap.append(table); details.append(wrap); section.append(details); root.append(section);
  }
  const notes = el('section', undefined, 'report-notes');
  notes.append(el('h3', '如何使用这份报告'), el('p', '优先核实变化较大的商品是否存在价格、促销、录入或供货变化。图表说明变化体现在哪里，并不能证明经营原因。类别与商品是两种拆分，不能相加；小票数不等于顾客人数。'), el('small', `数据截止 ${report.freshness} · 本机计算 · 金额变化已对账`));
  root.append(notes);
  const original = el('details', undefined, 'report-original');
  original.append(el('summary', '查看文字证据与口径'), el('pre', plainText));
  root.append(original);
  return root;
}
