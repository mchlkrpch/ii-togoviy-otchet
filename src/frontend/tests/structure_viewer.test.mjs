import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const template = readFileSync(new URL('../../structure_viewer.html', import.meta.url), 'utf8');
const box = [10, 10, 80, 80];
const fixture = { name: 'sample.pdf', pages: [{number:1, size:[100,100], image:'data:image/png;base64,',
  roots:['table'], row_roots:[{label:'2026', children:['value']}], nodes:[
    {id:'table',kind:'table',label:'Таблица',bbox:box,children:['header']},
    {id:'header',kind:'header',label:'Оплата труда',bbox:box,field_bbox:[10,10,80,20],color:'#2563eb',depth:1,column:1,column_span:1,parent:'table',children:['leaf'],column_path:['Оплата труда']},
    {id:'leaf',kind:'header',label:'Всего',bbox:[10,20,80,80],field_bbox:[10,20,80,30],color:'#2563eb',depth:2,column:1,column_span:1,parent:'header',children:['value'],column_path:['Оплата труда','Всего']},
    {id:'value',kind:'value',label:'120.50',bbox:[10,30,80,40],field_bbox:[10,30,80,40],color:'#2563eb',parent:'leaf',children:[],value_type:'decimal',row_path:['2026'],column_path:['Оплата труда','Всего'],needs_review:true,alternatives:[{text:'120,50'}]},
  ]}]};

function setup(t, model=fixture) {
  const errors=[];
  const dom=new JSDOM(template.replace('__DOCUMENT_DATA__',JSON.stringify(model)), {
    runScripts:'dangerously',pretendToBeVisual:true,
    beforeParse(w){w.HTMLElement.prototype.scrollTo=function(){};w.addEventListener('error',e=>errors.push(e.message));},
  });
  t.after(()=>{assert.deepEqual(errors,[]);dom.window.close();});
  return {w:dom.window,d:dom.window.document};
}

test('viewer draws static branches and separate source cells', t=>{
  const {d}=setup(t);
  assert.equal(d.querySelectorAll('[data-group]').length,1);
  assert.equal(d.querySelectorAll('[data-field]').length,3);
  d.getElementById('depth').value='all';
  d.getElementById('depth').dispatchEvent(new d.defaultView.Event('change'));
  assert.equal(d.querySelectorAll('[data-group]').length,2);
});

test('branch selection highlights its descendant fields',t=>{
  const {d}=setup(t);
  d.querySelector('[data-select="header"]').click();
  assert.equal(d.querySelectorAll('#selection-layer .chosen-cell').length,3);
  assert.match(d.getElementById('detailValue').textContent,/1 значений/);
});

test('clicking PDF value reveals full field path and OCR alternatives',t=>{
  const {w,d}=setup(t);
  d.querySelector('[data-field="value"]').dispatchEvent(new w.MouseEvent('click'));
  assert.equal(d.getElementById('detailValue').textContent,'120,50');
  assert.equal(d.getElementById('detailPath').textContent,'Оплата труда → Всего');
  assert.match(d.getElementById('review').textContent,/120,50/);
  assert.equal(d.querySelector('.tree-row.selected').dataset.node,'value');
});

test('search expands matching paths and reports no results',t=>{
  const {w,d}=setup(t);const search=d.getElementById('search');
  search.value='120';search.dispatchEvent(new w.Event('input'));
  assert.ok(d.querySelector('[data-select="value"]'));
  search.value='нет такого';search.dispatchEvent(new w.Event('input'));
  assert.match(d.getElementById('tree').textContent,/Совпадений нет/);
});

test('row view, original toggle and reset preserve usable selection',t=>{
  const {w,d}=setup(t);
  d.getElementById('byRows').click();assert.ok(d.querySelector('[data-select="value"]'));
  d.querySelector('[data-select="value"]').click();
  d.getElementById('original').click();assert.equal(d.getElementById('overlay').children.length,0);
  d.getElementById('original').click();assert.ok(d.getElementById('selection-layer'));
  d.getElementById('reset').click();assert.equal(d.getElementById('selection-layer'),null);
  d.getElementById('plus').click();assert.equal(d.getElementById('zoomLabel').textContent,'125%');
  d.getElementById('fit').click();assert.equal(d.getElementById('zoomLabel').textContent,'100%');
});

test('value selection highlights both axes and explains its row context',t=>{
  const model=structuredClone(fixture),n=model.pages[0].nodes.at(-1);
  Object.assign(n,{metric:'Трудоемкость',unit:'человеко-месяц',row_hierarchy:['3 этап','2026 г.'],
    row_header_bboxes:[[0,30,10,40]],column_header_bboxes:[[10,10,80,20]],row_bbox:[0,30,90,40]});
  const {w,d}=setup(t,model);
  d.querySelector('[data-field="value"]').dispatchEvent(new w.MouseEvent('click'));
  assert.equal(d.querySelectorAll('[data-axis="row"]').length,1);
  assert.equal(d.querySelectorAll('[data-axis="column"]').length,1);
  assert.match(d.getElementById('detailRow').textContent,/3 этап → 2026 г/);
  assert.match(d.getElementById('detailMeaning').textContent,/человеко-месяц/);
});

test('metadata displays key/value and separate key and value extents',t=>{
  const model=structuredClone(fixture);
  model.pages[0].nodes.push({id:'kv',kind:'key_value',label:'Исполнитель',key:'исполнитель',value:'МФТИ, Физтех',
    bbox:box,field_bbox:box,key_bbox:[10,10,30,20],value_bbox:[40,10,80,20],color:'#0f766e',children:[]});
  model.pages[0].roots.push('kv');
  const {d}=setup(t,model);
  d.querySelector('[data-select="kv"]').click();
  assert.equal(d.getElementById('detailValue').textContent,'МФТИ, Физтех');
  assert.match(d.getElementById('detailMeaning').textContent,/"исполнитель":"МФТИ, Физтех"/);
  assert.ok(d.querySelector('[data-axis="key"]'));
  assert.ok(d.querySelector('[data-axis="value"]'));
});

test('search combines column meaning with a row year and stage',t=>{
  const model=structuredClone(fixture);
  model.pages[0].nodes.at(-1).row_hierarchy=['3 этап','2026 г.'];
  const {w,d}=setup(t,model),search=d.getElementById('search');
  search.value='оплата труда за 3 этап 2026';search.dispatchEvent(new w.Event('input'));
  assert.ok(d.querySelector('[data-select="value"]'));
  search.value='оплата труда за 3 этап 2025';search.dispatchEvent(new w.Event('input'));
  assert.match(d.getElementById('tree').textContent,/Совпадений нет/);
});
