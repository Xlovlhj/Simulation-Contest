/* 离线项目工作台：源文档由Python同步，学习勾选只保存在浏览器。 */
(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('portal-data').textContent);
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const storageKey = 'simulation-2026-C-learning-v1';
  const termIds = new Set(data.terms.map(t => t.id));
  let mastered = new Set(), storageAvailable = true;
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || '[]');
    if (Array.isArray(saved)) mastered = new Set(saved.filter(id => termIds.has(id)));
    localStorage.setItem(storageKey, JSON.stringify([...mastered]));
  } catch (_) { storageAvailable = false; }
  let currentPage = 'home', termFilter = 'all', termCategory = 'all', termQuery = '', toastTimer;
  const builtin = [
    {id:'home',title:'首页',group:'开始'},
    {id:'terms',title:'参数与术语',group:'开始'},
    {id:'results',title:'结果与图表',group:'开始'},
    {id:'resources',title:'赛题与附件',group:'开始'}
  ];
  const allPages = [...builtin, ...data.pages];
  const byId = new Map(allPages.map(p => [p.id,p]));
  const content = $('content');
  const pLink = (path, text, cls='') => `<a class="${cls}" href="#p=${esc(data.paths[path] || 'home')}" data-page="${esc(data.paths[path] || 'home')}">${text}</a>`;

  function toast(message) {
    $('toast').textContent = message;
    $('toast').classList.add('visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $('toast').classList.remove('visible'), 3000);
  }
  function saveLearning() {
    try { localStorage.setItem(storageKey, JSON.stringify([...mastered])); storageAvailable=true; }
    catch (_) { storageAvailable=false; toast('浏览器未允许保存。关闭前请导出学习记录。'); }
  }
  function nav() {
    const groups = [...new Set(allPages.map(p => p.group))];
    $('navigation').innerHTML = groups.map(group => `<section class="nav-group"><h2>${esc(group)}</h2>${allPages.filter(p => p.group===group).map(p => `<a class="nav-item" id="nav-${p.id}" href="#p=${p.id}" data-page="${p.id}"><span class="nav-dot" aria-hidden="true"></span><span>${esc(p.title)}</span></a>`).join('')}</section>`).join('');
    $('build-date').textContent = data.built_at.slice(0,10);
  }
  function setActive(id) {
    document.querySelectorAll('.nav-item').forEach(a => {
      const active = a.dataset.page===id;
      a.classList.toggle('active',active);
      if (active) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current');
    });
    $('current-label').textContent = byId.get(id)?.title || '全文搜索';
    document.title = `${byId.get(id)?.title || '搜索'} · C题项目工作台`;
  }
  function go(id, extra={}) {
    $('global-search').value='';
    const params = new URLSearchParams({p:id,...extra});
    const hash = '#'+params.toString();
    if (location.hash===hash) route(); else location.hash=hash;
    document.querySelector('.sidebar').classList.remove('open');
    $('menu-toggle').setAttribute('aria-expanded','false');
  }
  function deadlineHTML(item) {
    const hours = (new Date(item.date)-new Date())/36e5;
    const remaining = hours <= 0 ? '已到截止时间' : hours < 24 ? `剩余约 ${Math.ceil(hours)} 小时` : `剩余约 ${Math.ceil(hours/24)} 天`;
    return `<div class="deadline"><div><small>${esc(item.title)} · 北京时间</small><strong>${esc(item.short)}</strong></div><span class="remaining">${remaining}</span></div>`;
  }
  function homeHTML() {
    const project=data.project, state=data.state;
    const evidence=state.durability;
    const corrective=state.remediation;
    const paired=corrective?`${corrective.two_hour_parking.passed} / ${corrective.two_hour_parking.total}`:evidence?`${evidence.two_hour_near_equal_feasible} / ${evidence.two_hour_parking_pairs}`:`${state.control_robustness.both_feasible} / ${state.control_robustness.paired_cases}`;
    const pairedLabel=corrective?'修正策略两小时停车热终态接近':evidence?'两小时停车热终态接近':'阶段三配对双方均可行';
    const evidenceNote=corrective?`新观测器精确模型偏差试验${corrective.sensor_exact_model.passed}/${corrective.sensor_exact_model.total}可行；加入模型等误差后仅${corrective.sensor_mismatch_stress.passed}/${corrective.sensor_mismatch_stress.total}可行。旧策略与新策略的` :evidence?`另有${evidence.two_hour_parking_pairs-evidence.two_hour_near_equal_feasible}组停车热终态尚未接近；测温试验${evidence.sensor_trials-evidence.sensor_passed}个严格终温失败。`:`阶段三另有${state.control_robustness.failed}组测温偏差未达严格真实终温。`;
    const stages=project.stages.map(stage => {
      const done=stage.items.filter(i=>i.done).length, total=stage.items.length, percentage=Math.round(100*done/total);
      return `<article class="stage-card"><div class="stage-top"><h3>${esc(stage.name)}</h3><span class="step">${esc(stage.step)}</span></div><div class="stage-count">${percentage}% <small>${done} / ${total} 项完成</small></div><div class="track" role="progressbar" aria-label="${esc(stage.name)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percentage}"><span style="width:${percentage}%"></span></div><details><summary>查看工作项</summary><ul class="stage-items">${stage.items.map(item=>`<li><span class="${item.done?'done':'waiting'}" aria-hidden="true">${item.done?'✓':'○'}</span><span>${esc(item.text)}${item.status?`<small class="item-status">${esc(item.status)}</small>`:item.done?'':'（待完成）'}</span></li>`).join('')}</ul></details></article>`;
    }).join('');
    return `<div class="hero"><div class="hero-copy"><div class="eyebrow">项目概览</div><h1>${esc(project.subtitle)}</h1><p class="lead">${esc(project.description)}</p><div class="quick-links">${pLink('docs/三问任务与成果速览.md','三问任务与成果速览 →','button primary')}</div></div><div class="hero-aside"><span class="badge">${esc(project.stage_label)}</span><span class="note">内容更新 ${esc(state.date_beijing)}</span></div></div>
      <section class="focus"><div><div class="eyebrow">现在要做</div><h2>${esc(project.current_title)}</h2><p>${esc(project.current_description)}</p>${pLink(state.report,'查看最新进展 ↗')}</div><div class="focus-list">${project.current_tasks.map(task=>`<div><strong>${esc(task.title)}</strong><small>${esc(task.detail)}</small></div>`).join('')}</div></section>
      <section aria-labelledby="progress-title"><div class="section-head"><h2 id="progress-title">项目进度</h2><p>先完成，再验证，再定稿</p></div><div class="stage-grid">${stages}</div><p class="note" style="margin-top:12px">${esc(project.progress_note)}</p></section>
      <section class="deadline-row" aria-label="提交时间">${project.deadlines.map(deadlineHTML).join('')}</section>
      <div class="two-columns"><section><div class="section-head"><h2>已经有的证据</h2></div><div class="metric-line"><div><strong>${state.verification.tests_run}</strong><span>程序检查通过</span></div><div><strong>${paired}</strong><span>${pairedLabel}</span></div><div><strong>${data.pages.length}</strong><span>文档集中阅读</span></div></div><p class="note">${evidenceNote}失败已保留。</p><div class="quick-links">${pLink('docs/C题项目执行方案_精简版.md','从精简计划开始','button') }<a class="button" href="#p=terms" data-page="terms">学习参数与术语</a></div></section><section><div class="section-head"><h2>仍要记住的边界</h2></div><ul class="plain-list">${project.limits.map(t=>`<li>${esc(t)}</li>`).join('')}</ul></section></div>`;
  }

  function termsHTML() {
    const categories=[...new Set(data.terms.map(t=>t.category))];
    return `<div class="page-heading"><div><div class="eyebrow">边做边懂</div><h1>参数与术语</h1><p class="lead">先读人话，再看专业解释。掌握一个，勾选一个。</p></div><div class="tools"><button id="export-learning" class="button" type="button">导出学习记录</button><button id="import-learning" class="button" type="button">导入记录</button></div></div>
      <div class="learning-summary"><strong id="learning-count"></strong><span id="storage-status"></span></div><div class="track" id="learning-progress" role="progressbar" aria-label="术语掌握进度" aria-valuemin="0" aria-valuemax="100"><span></span></div>
      <div class="terms-toolbar"><div class="segmented" role="group" aria-label="掌握情况"><button type="button" data-filter="all">全部</button><button type="button" data-filter="unmastered">未掌握</button><button type="button" data-filter="mastered">已掌握</button></div><label class="field">按分类<select id="term-category"><option value="all">全部分类</option>${categories.map(c=>`<option>${esc(c)}</option>`).join('')}</select></label><label class="field grow">查找词条<input id="term-search" type="search" placeholder="例如：SOC、滚阻、回收" value="${esc(termQuery)}"></label></div><div class="learning-summary"><span id="term-visible-count"></span><span>参考值与暂定校准值分别列示</span></div><div id="term-list" class="term-list"></div>`;
  }
  function termCard(term) {
    const learned=mastered.has(term.id);
    return `<article class="term-card ${learned?'mastered':''}" id="term-${term.id}"><div class="term-title"><label class="term-check"><input type="checkbox" data-term="${term.id}" ${learned?'checked':''} aria-label="已掌握：${esc(term.name)}"><span class="term-name">${esc(term.name)}<span class="term-symbol">${esc(term.symbol)}${term.unit!=='—'?' · '+esc(term.unit):''}</span></span></label><span class="badge neutral term-state">${learned?'已掌握':esc(term.category)}</span></div><div class="term-body"><div class="term-defs"><div><small>专业解释</small><p>${esc(term.professional)}</p></div><div><small>人话解释</small><p>${esc(term.plain)}</p></div></div>${term.values.length?`<div class="term-values">${term.values.map(v=>`<span>${esc(v.label)} <b>${esc(v.value)}</b></span>`).join('')}</div>`:''}${term.note?`<p class="term-note">${esc(term.note)}</p>`:''}<p class="term-source">来源：${esc(term.source)}</p></div></article>`;
  }
  function filteredTerms() {
    const query=termQuery.trim().toLowerCase();
    return data.terms.filter(t=>(termFilter==='all'||mastered.has(t.id)===(termFilter==='mastered'))&&(termCategory==='all'||t.category===termCategory)&&(!query||[t.name,t.symbol,t.professional,t.plain,t.note,...t.values.map(v=>v.value)].join(' ').toLowerCase().includes(query)));
  }
  function updateLearningSummary() {
    if (!$('learning-count')) return;
    $('learning-count').textContent=`已掌握 ${mastered.size} / ${data.terms.length} 个词条`;
    $('storage-status').textContent=storageAvailable?'自动保存到此浏览器；换浏览器前请导出。':'浏览器未允许保存；关闭前请导出。';
    const percentage=Math.round(100*mastered.size/data.terms.length);
    $('learning-progress').setAttribute('aria-valuenow',percentage);
    $('learning-progress').firstElementChild.style.width=percentage+'%';
  }
  function renderTermList() {
    const filtered=filteredTerms();
    $('term-list').innerHTML=filtered.length?filtered.map(termCard).join(''):'<div class="empty-state">没有符合条件的词条。试试更换分类或关键词。</div>';
    $('term-visible-count').textContent=`显示 ${filtered.length} 个词条`;
    document.querySelectorAll('[data-filter]').forEach(button=>{
      const active=button.dataset.filter===termFilter;
      button.classList.toggle('active',active);button.setAttribute('aria-pressed',active);
    });
    updateLearningSummary();
  }
  function wireTerms() {
    $('term-category').value=termCategory;
    $('term-category').addEventListener('change',e=>{termCategory=e.target.value;renderTermList();});
    $('term-search').addEventListener('input',e=>{termQuery=e.target.value;renderTermList();});
    content.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{termFilter=b.dataset.filter;renderTermList();}));
    $('term-list').addEventListener('change',e=>{
      const id=e.target.dataset.term;if(!id)return;
      if(e.target.checked)mastered.add(id);else mastered.delete(id);
      saveLearning();
      if(termFilter!=='all'){renderTermList();return;}
      const card=$('term-'+id);card.classList.toggle('mastered',mastered.has(id));
      card.querySelector('.term-state').textContent=mastered.has(id)?'已掌握':data.terms.find(t=>t.id===id).category;
      updateLearningSummary();
    });
    $('export-learning').addEventListener('click',()=>{
      const record={format:'c-topic-learning-v1',project:'2026-simulation-C',exported_at:new Date().toISOString(),mastered:[...mastered]};
      const url=URL.createObjectURL(new Blob([JSON.stringify(record,null,2)],{type:'application/json;charset=utf-8'}));
      const link=document.createElement('a');link.href=url;link.download='C题学习记录_'+new Date().toISOString().slice(0,10)+'.json';link.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);toast('学习记录已导出。');
    });
    $('import-learning').addEventListener('click',()=>$('learning-import').click());
    renderTermList();
  }

  function resultsHTML() {
    const values=data.state.control_nominal_heat_corrected_savings_pct;
    const durability=data.state.durability;
    const reliability=data.state.reliability;
    const parameterLabels={drag_coefficient:'空阻 Cd',rolling_coefficient:'滚阻 Cr',resistance_ref_ohm:'内阻 Rref / Ω'};
    const parameterRows=reliability?reliability.primary_ranges.filter(r=>parameterLabels[r.parameter]).map(r=>'<tr><td>'+parameterLabels[r.parameter]+'</td><td>'+r.frozen_estimate.toPrecision(5)+'</td><td>['+r.percentile025.toPrecision(5)+', '+r.percentile975.toPrecision(5)+']</td></tr>').join(''):'';
    const uncertainty=reliability?'<h2>参数可靠性：已计算，仍有限制</h2><p>'+reliability.accepted+' / '+reliability.requested+' 次非线性重拟合正常结束。主分析中 <strong>'+(100*reliability.Cr_lower_bound_fraction).toFixed(1)+'%</strong> 的滚阻估计贴着工程下界，不能据此认为已估准。</p><div class="table-wrap"><table class="data-table"><thead><tr><th>参数</th><th>原冻结值</th><th>主分析条件百分位范围</th></tr></thead><tbody>'+parameterRows+'</tbody></table></div><p class="note">只使用训练575/596；原参数与控制未替换。范围受原模型、误差机制和工程边界限制，不保证实车总体95%覆盖。旧线性区间穿零的结果保留。</p><div class="quick-links">'+pLink('docs/参数可靠性与非线性区间通俗说明.md','参数可靠性 · 人话版','button')+pLink('results/阶段五进展.md','完整诊断与失败记录','button')+'</div>':'';
    const recovery=durability?`<h2>驾驶90分钟 + 停车温控2小时</h2><div class="table-wrap"><table class="data-table"><thead><tr><th>环境</th><th>总账节能率</th><th>节省电能</th><th>热终态接近</th></tr></thead><tbody>${durability.ninety_minute_plus_two_hour_parking.map(r=>`<tr><td>${r.ambient_c}℃</td><td><strong>${r.raw_saving_pct.toFixed(2)}%</strong></td><td>${r.saved_energy_kwh.toFixed(3)} kWh</td><td>${r.valid_close_end_comparison?'是':'否'}</td></tr>`).join('')}</tbody></table></div><p class="note">实际恢复耗电已计入，停车保留空调和附件。18组两小时停车对照中17组热终态接近；40℃驾驶180分钟的一组仍超出温差阈值。测温偏差42个试验中30个满足全部约束，±1℃的12个严格终温失败。低SOC和热状态缺少实测支持。</p>`:'';
    return `<div class="eyebrow">结果与证据</div><h1>先看结论，再看曲线。</h1><p class="lead">以下为指定模型与条件下的仿真；完整定义和失败记录见最新进展。</p><div class="results-banner">同一工况、同一模型、同一初值与约束。控制试验初始SOC94%，座舱预调24℃，未计预调温耗电。</div>${uncertainty}${recovery}<h2>单次CLTC设计场景</h2><div class="table-wrap"><table class="data-table"><thead><tr><th>环境温度</th><th>含理想终态热修正的节能率</th><th>结果范围</th></tr></thead><tbody>${[25,40,-10].map(t=>`<tr><td>${t}℃</td><td><strong>${Number(values[t.toFixed(1)]).toFixed(2)}%</strong></td><td>30分钟驾驶，满足共同约束</td></tr>`).join('')}</tbody></table></div><p class="note">此表采用理想热量核算；与上方含两小时实际停车耗电的总账范围不同。两表均不代表实车节能率。</p><div class="quick-links">${pLink(data.state.report,'阅读最新报告','button primary')}${pLink('docs/连续行驶与热恢复通俗说明.md','热恢复与测温说明','button')}${pLink('results/阶段二进展.md','查看校准与局限','button')}</div><div class="section-head"><h2>动态开发样片</h2><p>15秒 · 720p · 无声</p></div><div class="video-block">${data.video?`<video id="development-video" controls preload="none" playsinline src="${data.video}" aria-label="25℃控制对照开发样片"></video>`:'<p>尚未生成开发样片。</p>'}<p>用于查看功率流、累计能量、SOC和温度。尚非正式三分钟四环节视频。</p></div><div class="section-head"><h2>实验图表</h2><p>点击图片可放大</p></div><div class="gallery">${data.figures.map((f,i)=>`<figure class="figure-card"><button type="button" data-figure="${i}" aria-label="放大：${esc(f.title)}"><img loading="lazy" src="${f.image}" alt="${esc(f.title)}"></button><figcaption>${esc(f.title)}</figcaption></figure>`).join('')}</div>`;
  }
  function resourcesHTML() {
    return `<div class="eyebrow">原始资料</div><h1>赛题与附件</h1><p class="lead">PDF可在页内预览。Word与Excel通过本地原文件打开，保留原始内容。</p><div class="resource-list">${data.resources.map((file,i)=>`<div class="resource"><span class="file-type">${esc(file.type.toUpperCase())}</span><div class="resource-title">${esc(file.name)}<small>${(file.bytes/1024).toFixed(0)} KB · 项目内原始复制件</small></div><div class="tools">${file.type==='pdf'?`<button class="button compact" data-pdf="${i}" type="button">页内预览</button>`:''}<a class="button compact" target="_blank" rel="noopener" href="${esc(file.path)}">打开原件</a></div></div>`).join('')}</div><p class="note" style="margin-top:17px">原件链接需保留项目目录结构。其余文档、图表和样片已包含在本页面中。</p>`;
  }
  function docHTML(page) {
    const history=page.historical?'<div class="notice">这是历史阶段快照，保留当时结论。当前进展请看首页和最新进展。</div>':'';
    return `<div class="doc-meta">${esc(page.group)} / ${esc(page.title)} · <a href="${encodeURI(page.path)}" target="_blank" rel="noopener">查看源文件</a></div>${history}<div class="doc-layout"><article class="document" id="document-content">${page.html}</article><aside class="toc" aria-label="本页目录"><h3>本页目录</h3>${page.toc}</aside></div>`;
  }
  let mermaidCount=0, mathQueue=Promise.resolve();
  const formulaCache=new Map();
  function enhanceDocument() {
    const article=$('document-content');if(!article)return;
    article.querySelectorAll('pre > code.language-mermaid').forEach(code=>{
      const source=code.textContent, pre=code.parentElement;
      const figure=document.createElement('figure');figure.className='mermaid-figure';
      const diagram=document.createElement('div');diagram.setAttribute('role','img');diagram.setAttribute('aria-label','项目流程图');
      const details=document.createElement('details');details.innerHTML='<summary>查看图的文字定义</summary><pre></pre>';details.querySelector('pre').textContent=source;
      figure.append(diagram,details);pre.replaceWith(figure);
      try {
        mermaid.mermaidAPI.render('portal-diagram-'+(++mermaidCount),source,svg=>{diagram.innerHTML=svg;});
      } catch(error) {
        diagram.textContent='流程图未能绘制，可展开文字定义阅读。';details.open=true;
      }
    });
    if(window.MathJax?.startup?.promise) {
      mathQueue=mathQueue.then(()=>MathJax.startup.promise).then(async()=>{
        // 独立转换公式，避免快速切页时整页排版仍引用已移除的元素。
        for(const element of article.querySelectorAll('.math-inline,.math-display')){
          if(!article.isConnected)break;
          const raw=element.textContent.trim(),display=element.classList.contains('math-display');
          const tex=raw.slice(2,-2),key=String(display)+tex;
          let svg=formulaCache.get(key);
          if(!svg){svg=(await MathJax.tex2svgPromise(tex,{display})).outerHTML;formulaCache.set(key,svg);}
          if(element.isConnected)element.innerHTML=svg;
        }
      }).catch(error=>{
        if(article.isConnected){article.dataset.mathError=String(error);toast('部分公式未能排版，原始公式仍可查看。');}
      });
    }
  }
  function route() {
    const params=new URLSearchParams(location.hash.slice(1));
    const id=byId.has(params.get('p'))?params.get('p'):'home';
    currentPage=id;setActive(id);
    content.innerHTML=id==='home'?homeHTML():id==='terms'?termsHTML():id==='results'?resultsHTML():id==='resources'?resourcesHTML():docHTML(byId.get(id));
    if(id==='terms')wireTerms();else if(!builtin.some(p=>p.id===id))enhanceDocument();
    window.scrollTo({top:0,behavior:'instant'});
    const term=params.get('term');
    if(id==='terms'&&termIds.has(term)){
      termFilter='all';termCategory='all';termQuery='';$('term-category').value='all';$('term-search').value='';renderTermList();
      requestAnimationFrame(()=>$('term-'+term)?.scrollIntoView({block:'center'}));
    }
    const anchor=params.get('anchor');
    if(anchor)requestAnimationFrame(()=>document.getElementById(anchor)?.scrollIntoView());
  }

  const searchIndex=[...data.pages.map(p=>({title:p.title,group:p.group,text:p.text,id:p.id})),...data.terms.map(t=>({title:t.name+' '+t.symbol,group:'参数与术语',text:[t.name,t.symbol,t.category,t.professional,t.plain,t.note,...t.values.map(v=>v.value)].join(' '),id:'terms',term:t.id}))];
  function searchHTML(query) {
    const words=query.toLowerCase().split(/\s+/).filter(Boolean);
    const hits=searchIndex.filter(p=>words.every(word=>(p.title+' '+p.text).toLowerCase().includes(word)));
    function snippet(entry) {
      const compact=entry.text.replace(/[#*`|]/g,'').replace(/\n+/g,' ');
      const index=compact.toLowerCase().indexOf(words[0]),start=Math.max(0,index-40);
      return (start?'…':'')+compact.slice(start,start+170)+(compact.length>start+170?'…':'');
    }
    return `<div class="eyebrow">全文搜索</div><h1>查找“${esc(query)}”</h1><p class="lead">找到 ${hits.length} 条结果，覆盖全部文档和术语。</p>${hits.length?hits.map(hit=>`<button class="search-result" type="button" data-search-page="${hit.id}" ${hit.term?`data-search-term="${hit.term}"`:''}><small>${esc(hit.group)}</small><strong>${esc(hit.title)}</strong><p>${esc(snippet(hit))}</p></button>`).join(''):'<div class="empty-state">没有找到。试试更短的关键词。</div>'}`;
  }
  let searchTimer;
  $('global-search').addEventListener('input',()=>{
    clearTimeout(searchTimer);
    searchTimer=setTimeout(()=>{
      const query=$('global-search').value.trim();
      if(!query){route();return;}
      setActive('search');content.innerHTML=searchHTML(query);window.scrollTo({top:0,behavior:'instant'});
    },120);
  });
  document.addEventListener('keydown',e=>{
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('global-search').focus();}
    if(e.key==='Escape'&&document.activeElement===$('global-search')){$('global-search').value='';route();$('global-search').blur();}
  });
  function openDialog(title, body) {
    $('dialog-title').textContent=title;$('dialog-body').innerHTML=body;$('detail-dialog').showModal();
  }
  $('close-dialog').addEventListener('click',()=>$('detail-dialog').close());
  $('detail-dialog').addEventListener('click',e=>{if(e.target===$('detail-dialog'))$('detail-dialog').close();});
  document.addEventListener('click',e=>{
    const page=e.target.closest('[data-page]');
    if(page){e.preventDefault();go(page.dataset.page,page.dataset.anchor?{anchor:page.dataset.anchor}:{});return;}
    const hit=e.target.closest('[data-search-page]');
    if(hit){go(hit.dataset.searchPage,hit.dataset.searchTerm?{term:hit.dataset.searchTerm}:{});return;}
    const figure=e.target.closest('[data-figure]');
    if(figure){const f=data.figures[Number(figure.dataset.figure)];openDialog(f.title,`<img src="${f.image}" alt="${esc(f.title)}">`);return;}
    const pdf=e.target.closest('[data-pdf]');
    if(pdf){const file=data.resources[Number(pdf.dataset.pdf)];openDialog(file.name,`<p class="note">若浏览器不支持预览，可<a target="_blank" rel="noopener" href="${esc(file.path)}">打开原件</a>。</p><iframe title="${esc(file.name)}" src="${esc(file.path)}"></iframe>`);return;}
    const image=e.target.closest('.document img');
    if(image){openDialog(image.alt||'实验图表',`<img src="${image.src}" alt="${esc(image.alt)}">`);return;}
    const anchor=e.target.closest('.toc a, .document a[href^="#"]');
    if(anchor&&!anchor.dataset.page){
      const id=decodeURIComponent(anchor.getAttribute('href').slice(1));
      if(document.getElementById(id)){e.preventDefault();document.getElementById(id).scrollIntoView({behavior:'smooth'});}
    }
  });
  $('learning-import').addEventListener('change',async e=>{
    const file=e.target.files[0];if(!file)return;
    try {
      if(file.size>1024*1024)throw new Error('文件过大，请选择导出的学习记录。');
      const record=JSON.parse(await file.text());
      if(record.format!=='c-topic-learning-v1'||record.project!=='2026-simulation-C'||!Array.isArray(record.mastered)||record.mastered.some(id=>typeof id!=='string'))throw new Error('这不是有效的C题学习记录。');
      const known=record.mastered.filter(id=>termIds.has(id));
      if(!confirm(`将导入 ${new Set(known).size} 个已掌握词条，并替换当前勾选。继续吗？`))return;
      mastered=new Set(known);saveLearning();
      if(currentPage==='terms')renderTermList();
      toast('学习记录已导入。');
    } catch(error){toast(error instanceof SyntaxError?'文件不是有效的JSON学习记录。':error.message);}
    finally{e.target.value='';}
  });
  $('menu-toggle').addEventListener('click',()=>{
    const open=document.querySelector('.sidebar').classList.toggle('open');$('menu-toggle').setAttribute('aria-expanded',open);
  });
  $('about-button').addEventListener('click',()=>openDialog('使用说明',`<p>左侧切换内容；搜索框可查全部文档和术语。手机上点击“目录”。</p><p>术语勾选自动保存在当前浏览器。换浏览器、换电脑或移动文件前，先导出学习记录。</p><p>页面是文档快照。后续项目推进时会同步更新；你在这里的学习勾选会保留。进度按工作项统计，不代表得分。</p><p>生成时间：${esc(data.built_at.replace('T',' ').replace('+08:00',' 北京时间'))}</p><details><summary>离线阅读组件与许可</summary>${data.licenses.map(item=>`<h3>${esc(item.name)}</h3><pre>${esc(item.text)}</pre>`).join('')}</details>`));
  window.addEventListener('hashchange',route);
  window.addEventListener('storage',e=>{if(e.key===storageKey){try{const ids=JSON.parse(e.newValue||'[]');if(Array.isArray(ids)){mastered=new Set(ids.filter(id=>termIds.has(id)));if(currentPage==='terms')renderTermList();}}catch(_){}}});
  mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:'base',fontFamily:'Microsoft YaHei, sans-serif',themeVariables:{primaryColor:'#e8f2e9',primaryTextColor:'#233c2c',primaryBorderColor:'#94b49c',lineColor:'#6f8776',fontSize:'14px'},flowchart:{htmlLabels:false,curve:'linear'}});
  nav();route();
})();
