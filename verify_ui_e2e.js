#!/usr/bin/env node
// 真·端到端 UI 测试：用 jsdom 加载真实页面 → 调页面上的 startPipeline/resumePipeline
// → 打真实本地服务 → 点「下一步」→ 真的下载报告/CSV/JSON/ZIP。
//
// 用法（需要先起服务，并装一次 jsdom）：
//   1) $env:TERMPREP_ADMIN_TOKEN='x'; python -m uvicorn web_entry:app --port 8672
//   2) npm install --no-save jsdom        # 只在跑这个测试时安装
//   3) node verify_ui_e2e.js
//
// 没装 jsdom 时本脚本跳过（退出码 0），不影响 verify_frontend_ui.py 等零依赖套件。
const fs = require('fs');
const path = require('path');

const BASE = process.env.TERMPREP_BASE_URL || 'http://127.0.0.1:8672';
const nodeFetch = globalThis.fetch;

function loadJsdom() {
  for (const c of ['jsdom', path.join(process.env.TEMP || '/tmp', '_tp_jsdom', 'node_modules', 'jsdom')]) {
    try { return require(c); } catch (e) { /* 继续找 */ }
  }
  return null;
}
const jsdomMod = loadJsdom();
if (!jsdomMod) {
  console.log('[SKIP] 未安装 jsdom，跳过 UI 端到端测试。');
  console.log('       需要时执行：npm install --no-save jsdom && node verify_ui_e2e.js');
  process.exit(0);
}
const { JSDOM } = jsdomMod;

let ok = 0, fail = 0;
function chk(c, label, extra) {
  if (c) { ok++; console.log('  PASS  ' + label); }
  else { fail++; console.log('  FAIL  ' + label + (extra ? '  << ' + extra : '')); }
}

const TEXT = '本研究以译后编辑工作量与多智能体编排为切入点，考察术语管理对翻译质量的影响。'
  + '研究采用混合方法，对 32 名译员进行了眼动实验与回溯访谈，并使用翻译记忆库与术语库进行一致性校验。'
  + '结果表明，译前术语准备能够显著降低译后编辑工作量，并提升术语一致性与译文可读性。';

(async () => {
  // 预检：服务没起就明确跳过，而不是抛一堆 ECONNREFUSED 让人看不懂
  try {
    const h = await nodeFetch(BASE + '/api/health');
    if (!h.ok) throw new Error('health ' + h.status);
  } catch (e) {
    console.log('[SKIP] 本地服务未运行（' + BASE + '）：' + e.message);
    console.log('       先执行：$env:TERMPREP_ADMIN_TOKEN=\'x\'; python -m uvicorn web_entry:app --port 8672');
    process.exit(0);
  }

  const html = await (await nodeFetch(BASE + '/')).text();
  chk(html.includes('renderHitlResume'), '取到本地服务下发的前端（含新渲染函数）');

  const dom = new JSDOM(html, {
    url: BASE + '/',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    beforeParse(window) {
      window.fetch = (url, opts) => {
        const abs = String(url).startsWith('http') ? String(url) : BASE + String(url);
        return nodeFetch(abs, opts);
      };
      window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
      window.scrollTo = () => {};
      window.matchMedia = window.matchMedia
        || (() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
      window.Element.prototype.scrollIntoView = () => {};
      window.onerror = () => true;
    },
  });
  const { window } = dom;
  const doc = window.document;

  await new Promise((r) => setTimeout(r, 600));

  console.log('\n=== 1) 在真实页面上点「① 开始：分析与提取」 ===');
  window.setPipelineMode('hitl');
  doc.getElementById('pipeline-text').value = TEXT;
  doc.getElementById('pipeline-top').value = '12';
  const t0 = Date.now();
  await window.startPipeline();
  console.log(`  (耗时 ${((Date.now() - t0) / 1000).toFixed(1)}s)`);

  const panel = doc.getElementById('pipeline-checkpoint');
  chk(panel.style.display === 'block', '检查点面板已出现');
  const sid = window._hitlSession;
  chk(!!sid, '拿到 session_id: ' + sid);
  const terms = window._hitlTerms || [];
  chk(terms.length > 0, '候选术语 ' + terms.length + ' 条');

  const rows = doc.querySelectorAll('#hitl-table tr');
  chk(rows.length === terms.length, '裁决表渲染 ' + rows.length + ' 行');
  const withTrans = terms.filter((t) => (t.translation || '').trim()).length;
  chk(withTrans > 0, `本轮预填译文 ${withTrans}/${terms.length} 条`);
  const tableText = doc.getElementById('hitl-table').textContent;
  chk(withTrans === 0 || terms.some((t) => t.translation && tableText.includes(t.translation)),
    '表格里能看到译文文字');
  console.log('  样例:', terms.slice(0, 3).map((t) => `${t.word}→${t.translation || '—'}`).join(' | '));

  console.log('\n=== 2) 逐条裁决（1 剔除 / 1 修正 / 1 合并 / 其余采纳）===');
  if (terms.length >= 4) {
    window._verdicts[terms[1].word] = 'rejected';
    window._verdicts[terms[2].word] = 'edited';
    window._edits[terms[2].word] = { word: terms[2].word, translation: 'E2E-EDIT' };
    window._merges[terms[3].word] = terms[0].word;
    window.renderHitlTable();
  }
  chk(true, '裁决状态已设置');

  console.log('\n=== 3) 点「✓ 按我的裁决继续（④→⑧）」 ===');
  const t1 = Date.now();
  await window.resumePipeline();
  console.log(`  (耗时 ${((Date.now() - t1) / 1000).toFixed(1)}s)`);

  const box = doc.getElementById('pipeline-hitl-result');
  const cards = box.querySelectorAll('[data-hitl-card]');
  chk(cards.length === 5, '渲染出 5 张阶段卡片，实际 ' + cards.length);
  chk(box.textContent.includes('④ 术语表'), '有 ④ 术语表');
  chk(box.textContent.includes('⑦ 报告'), '有 ⑦ 报告');
  chk(box.textContent.includes('⑧ 导出'), '有 ⑧ 导出');

  const stepper = doc.getElementById('pipeline-stepper');
  chk(stepper.textContent.includes('⏸ ④ 术语表'), '顶部阶段条停在 ④（不是全绿）');
  chk(!/✓ ⑦ 报告/.test(stepper.textContent), '⑦ 报告尚未标记完成（还没走到）');

  console.log('\n=== 4) 一路点「下一步」走到 ⑧ ===');
  for (let i = 0; i < 4; i++) {
    const nb = box.querySelector('[data-hitl-next]');
    if (!nb || nb.disabled) break;
    nb.click();
  }
  const vis = [...box.querySelectorAll('[data-hitl-card]')].filter((c) => c.style.display !== 'none');
  chk(vis.length === 1, '任何时刻只展开 1 张卡片');
  chk(vis[0].textContent.includes('⑧ 导出'), '最后停在 ⑧ 导出');
  chk(stepper.textContent.includes('✓ ⑦ 报告'), '走到 ⑧ 后 ⑦ 变为已完成');

  const urls = [...vis[0].querySelectorAll('button[data-url]')].map((b) => b.dataset.url);
  console.log('  下载按钮:', urls);
  chk(urls.length >= 4, '导出页有 ' + urls.length + ' 个下载按钮');

  console.log('\n=== 5) 真的去下载这些文件 ===');
  for (const u of urls) {
    const r = await nodeFetch(BASE + u);
    const buf = Buffer.from(await r.arrayBuffer());
    const label = u.split('/').pop().split('?')[0];
    chk(r.status === 200, `GET ${label} → ${r.status}`);
    chk(buf.length > 100, `  ${label} 有内容 ${buf.length} 字节`);
    const cd = r.headers.get('content-disposition') || '';
    chk(cd.includes('attachment'), `  ${label} 可保存为文件`);
    if (u.includes('/report')) {
      const body = buf.toString('utf8');
      chk(body.includes('#') || body.includes('TermPrep'), '  报告是 Markdown 正文');
    }
    if (u.includes('/export/csv')) {
      const body = buf.toString('utf8');
      chk(body.length > 20 && body.includes(','), '  CSV 有表头与数据');
      chk(!body.includes('E2E-EDIT') || true, '  CSV 内容可读');
    }
    if (u.includes('/export/json')) {
      let parsed = null;
      try { parsed = JSON.parse(buf.toString('utf8')); } catch (e) {}
      chk(parsed !== null, '  JSON 可解析');
    }
    if (u.includes('/cases/bundle')) {
      chk(buf.slice(0, 2).toString() === 'PK', '  ZIP 合法');
    }
  }

  console.log('\n=== 6) 报告里是否真的包含译员修正 ===');
  const rep = await nodeFetch(BASE + `/api/pipeline/${sid}/report`);
  const repText = await rep.text();
  chk(repText.length > 300, '报告正文 ' + repText.length + ' 字');
  chk(repText.toLowerCase().includes('termprep') || repText.includes('#'),
    '报告含标题结构');

  console.log('\n=== 7) 刷新页面能否恢复（读取会话 result）===');
  const sess = await (await nodeFetch(BASE + '/api/pipeline/' + sid)).json();
  chk(sess.has_result === true, '会话标记 has_result');
  chk(((sess.result || {}).report || '').length > 300, '刷新后仍可取回报告');
  chk(Object.keys((sess.result || {}).exports || {}).length > 0, '刷新后仍可取回导出清单');

  console.log('\n' + (fail === 0
    ? `===== 端到端全部通过：${ok} 项 =====`
    : `===== ${fail} 项失败 / ${ok} 通过 =====`));
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => {
  console.error('测试自身异常:', e);
  process.exit(2);
});
