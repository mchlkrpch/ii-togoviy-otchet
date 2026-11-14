import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {JSDOM} from 'jsdom';

const fixture={sets:Array.from({length:16},(_,i)=>({id:'S'+String(i+1).padStart(2,'0'),title:'Проверка '+(i+1),
  formula:'=1+2',calculated:'3',status:'verified',note:'',documents:['source'],matches:[
    {reference:{документ:'source',страница_PDF:5,строка_или_показатель:'Итого',значение:'3'},actual:'3',status:'matched',method:'row_code_and_column',url:'sets/source.viewer.html#page=5&focus=value'},
    {reference:{документ:'source',страница_PDF:3,строка_или_показатель:'Источник',значение:'1'},actual:'1',status:'matched',method:'row_code_and_column',url:'sets/source.viewer.html#page=3'}]})),
  documents:[{id:'source',name:'Источник',viewer:'sets/source.viewer.html',original:'sets/source.pdf',structure:'sets/source.json'}],summary:{sets:16,documents:1,pages:5}};
const template=readFileSync(new URL('../../document_sets_viewer.html',import.meta.url),'utf8');
function setup(t){const errors=[];const dom=new JSDOM(template.replace('__SETS_DATA__',JSON.stringify(fixture)),{url:'http://127.0.0.1:8878/document-sets.html',runScripts:'dangerously',beforeParse(w){w.addEventListener('error',e=>errors.push(e.message));}});t.after(()=>{assert.deepEqual(errors,[]);dom.window.close();});return dom;}

test('all sixteen sets are present and every target/source opens a page',t=>{
  const dom=setup(t),d=dom.window.document;
  assert.equal(d.querySelectorAll('[data-set]').length,16);
  for(let i=1;i<=16;i++){
    d.querySelector(`[data-set="S${String(i).padStart(2,'0')}"]`).click();
    assert.match(d.getElementById('viewer').getAttribute('src'),/#page=5/);
    d.querySelector('[data-reference="1"]').click();
    assert.match(d.getElementById('viewer').getAttribute('src'),/#page=3/);
    d.getElementById('viewer').dispatchEvent(new dom.window.Event('load'));
  }
  assert.equal(Object.keys(JSON.parse(dom.window.localStorage.getItem('pdf-structure-set-history'))).length,16);
});

test('set search and previous/next navigation work',t=>{
  const dom=setup(t),d=dom.window.document;
  d.getElementById('next').click();assert.match(d.getElementById('title').textContent,/S02/);
  d.getElementById('previous').click();assert.match(d.getElementById('title').textContent,/S01/);
  d.getElementById('search').value='S16';d.getElementById('search').dispatchEvent(new dom.window.Event('input'));
  assert.equal(d.querySelectorAll('[data-set]').length,1);
});

test('generated six document viewers open without script errors and accept exact page/field links',{skip:!existsSync(new URL('../../../output/pdf/document-sets.manifest.json',import.meta.url))},t=>{
  const manifest=JSON.parse(readFileSync(new URL('../../../output/pdf/document-sets.manifest.json',import.meta.url),'utf8'));
  for(const doc of manifest.documents){
    const report=manifest.sets.flatMap(s=>s.matches).find(m=>m.reference['документ']===doc.id);
    const html=readFileSync(new URL('../../../output/pdf/'+doc.viewer,import.meta.url),'utf8');const errors=[];
    const dom=new JSDOM(html,{url:'http://127.0.0.1:8878/'+report.url,runScripts:'dangerously',beforeParse(w){w.HTMLElement.prototype.scrollTo=function(){};w.addEventListener('error',e=>errors.push(e.message));}});
    assert.deepEqual(errors,[],doc.id);
    assert.equal(Number(dom.window.document.getElementById('page').value),report.reference['страница_PDF']);
    assert.ok(dom.window.document.getElementById('selection-layer'),doc.id);
    dom.window.close();
  }
});
