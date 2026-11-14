import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';

const template=readFileSync(new URL('../../structure_viewer.html',import.meta.url),'utf8');
const script=readFileSync(new URL('../../structure_editor.js',import.meta.url),'utf8');
function fixture(){return {name:'example.pdf',sha256:'a'.repeat(64),pages:[{number:1,size:[200,200],image:'data:image/png;base64,',
  roots:['h'],row_roots:[],nodes:[
    {id:'h',kind:'header',label:'Организация',bbox:[10,0,150,20],field_bbox:[10,0,150,8],parent:null,color:'#2563eb',depth:1,column:1,column_span:1,children:['a','b']},
    {id:'a',kind:'text',label:'организация МФТИ',raw:'организация МФТИ',bbox:[10,10,90,20],field_bbox:[10,10,90,20],parent:'h',color:'#2563eb',children:[]},
    {id:'b',kind:'text',label:'значение',bbox:[100,10,150,20],field_bbox:[100,10,150,20],parent:'h',color:'#2563eb',children:[]}
  ]}]};}
async function setup(t,{restored=false}={}){
  let model=fixture(),revision=restored?1:0;const calls=[],errors=[];
  if(restored)model.pages[0].nodes[1].label='Сохраненный текст';
  const response=()=>({revision,operation_count:revision,model:structuredClone(model)});
  const html=template.replace('__DOCUMENT_DATA__',JSON.stringify(fixture())).replace('</body>','<script>'+script+'</script></body>');
  const dom=new JSDOM(html,{url:'http://127.0.0.1:8878/example.viewer.html',runScripts:'dangerously',pretendToBeVisual:true,beforeParse(w){
    w.HTMLElement.prototype.scrollTo=function(){};w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
    w.addEventListener('error',e=>errors.push(e.message));
    w.fetch=async(url,options={})=>{
      const body=options.body?JSON.parse(options.body):null;calls.push({url,body});
      if(String(url).endsWith('/preview'))return {ok:true,json:async()=>({boxes:[[10,10,50,20],[60,10,90,20]],method:'source_word_boxes',coverage:[1,1]})};
      if(String(url).endsWith('/save')){
        const op=body.operation;revision++;
        if(op.action==='update'){const n=model.pages[0].nodes.find(n=>n.id===op.ids[0]);if(op.text){n.label=op.text;n.raw=op.text;n.manual=true;n.text_modified=true;}if(op.bbox)n.bbox=n.field_bbox=op.bbox;}
        return {ok:true,json:async()=>({...response(),selected:op.ids?.[0]})};
      }
      return {ok:true,json:async()=>response()};
    };
  }});
  t.after(()=>{assert.deepEqual(errors,[]);dom.window.close();});
  await new Promise(resolve=>setImmediate(resolve));
  return {dom,d:dom.window.document,calls,tick:()=>new Promise(resolve=>setImmediate(resolve))};
}

test('saved model restores and text saves with optimistic revision',async t=>{
  const {d,calls,tick}=await setup(t,{restored:true});
  d.getElementById('expand').click();
  assert.match(d.getElementById('tree').textContent,/Сохраненный текст/);
  d.querySelector('[data-select="a"]').click();d.getElementById('editToggle').click();d.getElementById('editText').click();
  d.getElementById('editContent').value='Исправленная организация';d.getElementById('editSave').click();await tick();
  const save=calls.find(c=>c.url.endsWith('/save'));
  assert.equal(save.body.revision,1);assert.equal(save.body.operation.text,'Исправленная организация');
  assert.match(d.getElementById('tree').textContent,/Исправленная организация/);
  assert.match(d.getElementById('editStatus').textContent,/Сохранено на диск/);
  assert.equal(d.getElementById('editPanel').parentElement.className,'inspector editor-open');
  assert.equal(d.querySelectorAll('dialog').length,0);
  assert.equal(d.querySelectorAll('#editor-layer text').length,0);
  assert.equal(d.getElementById('editContent').value,'Исправленная организация');
});

test('split at caret requests deterministic preview and persists both parts',async t=>{
  const {d,calls,tick}=await setup(t);
  d.getElementById('expand').click();d.querySelector('[data-select="a"]').click();d.getElementById('editToggle').click();d.getElementById('editSplit').click();
  const source=d.getElementById('splitSource');source.setSelectionRange(11,11);
  d.getElementById('splitAtCursor').click();await tick();
  assert.equal(d.getElementById('splitFirst').value,'организация');assert.equal(d.getElementById('splitSecond').value,'МФТИ');
  assert.equal(d.querySelectorAll('[data-split-preview]').length,2);
  d.getElementById('editSave').click();await tick();
  assert.deepEqual(calls.find(c=>c.url.endsWith('/save')).body.operation.parts,['организация','МФТИ']);
});

test('merge mode picks exactly two leaves and submits one merge operation',async t=>{
  const {d,calls,tick}=await setup(t);
  d.getElementById('editToggle').click();d.getElementById('editPick').click();
  for(const id of ['a','b'])d.querySelector(`[data-field="${id}"]`).dispatchEvent(new d.defaultView.MouseEvent('click'));
  assert.equal(d.getElementById('editMerge').disabled,false);d.getElementById('editMerge').click();
  d.getElementById('editSave').click();await tick();
  assert.deepEqual(calls.find(c=>c.url.endsWith('/save')).body.operation.ids,['a','b']);
});

test('dragging a field converts screen coordinates to PDF points at zoom',async t=>{
  const {d,dom,calls,tick}=await setup(t);
  d.getElementById('expand').click();d.querySelector('[data-select="a"]').click();d.getElementById('editToggle').click();
  const svg=d.getElementById('overlay');svg.getBoundingClientRect=()=>({left:0,top:0,width:400,height:400});
  d.querySelector('[data-handle="move"]').dispatchEvent(new dom.window.MouseEvent('pointerdown',{clientX:100,clientY:50,bubbles:true}));
  svg.dispatchEvent(new dom.window.MouseEvent('pointermove',{clientX:140,clientY:70}));svg.dispatchEvent(new dom.window.MouseEvent('pointerup'));await tick();
  assert.deepEqual(calls.find(c=>c.url.endsWith('/save')).body.operation.bbox,[30,20,110,30]);
});

test('text is readable and copyable in the lower panel without entering edit mode',async t=>{
  const {d}=await setup(t,{restored:true});
  d.getElementById('expand').click();d.querySelector('[data-select="a"]').click();
  assert.equal(d.getElementById('editPanel').hidden,false);
  assert.equal(d.getElementById('editContent').value,'Сохраненный текст');
  assert.equal(d.getElementById('editContent').readOnly,true);
  assert.equal(d.getElementById('editSave').disabled,true);
  d.getElementById('editCopy').click();
  const text=d.getElementById('editContent');
  assert.equal(text.selectionEnd-text.selectionStart,text.value.length);
});

test('snap aligns moving edges, preserves stationary edges and uses screen pixel tolerance',async t=>{
  const {dom}=await setup(t),snap=dom.window.StructureEditorGeometry.snapBox;
  let result=snap([10,10,99,29],'se',[[100,30,150,50]],[200,200],2,2);
  assert.deepEqual([...result.box],[10,10,100,30]);assert.equal(result.guides.length,2);
  result=snap([10,10,98,28],'se',[[100,30,150,50]],[200,200],4,4);
  assert.deepEqual([...result.box],[10,10,98,28]);
  result=snap([10,10,89,29],'move',[[90,30,150,50]],[200,200],2,2);
  assert.deepEqual([...result.box],[11,11,90,30]);
  result=snap([10,10,99,29],'se',[[100,150,150,180]],[200,200],2,2);
  assert.deepEqual([...result.box],[10,10,99,29]);
});

test('Alt bypasses snapping during drag',async t=>{
  const {d,dom,calls,tick}=await setup(t);
  d.getElementById('expand').click();d.querySelector('[data-select="a"]').click();d.getElementById('editToggle').click();
  const svg=d.getElementById('overlay');svg.getBoundingClientRect=()=>({left:0,top:0,width:400,height:400});
  d.querySelector('[data-handle="move"]').dispatchEvent(new dom.window.MouseEvent('pointerdown',{clientX:100,clientY:50,bubbles:true}));
  svg.dispatchEvent(new dom.window.MouseEvent('pointermove',{clientX:118,clientY:50,altKey:true}));
  assert.equal(d.querySelectorAll('[data-snap-axis]').length,0);
  svg.dispatchEvent(new dom.window.MouseEvent('pointerup'));await tick();
  assert.deepEqual(calls.find(c=>c.url.endsWith('/save')).body.operation.bbox,[19,10,99,20]);
});

test('drag snaps to neighboring rectangle and saves the snapped geometry',async t=>{
  const {d,dom,calls,tick}=await setup(t);
  d.getElementById('expand').click();d.querySelector('[data-select="a"]').click();d.getElementById('editToggle').click();
  const svg=d.getElementById('overlay');svg.getBoundingClientRect=()=>({left:0,top:0,width:400,height:400});
  d.querySelector('[data-handle="move"]').dispatchEvent(new dom.window.MouseEvent('pointerdown',{clientX:100,clientY:50,bubbles:true}));
  svg.dispatchEvent(new dom.window.MouseEvent('pointermove',{clientX:118,clientY:50}));
  assert.ok(d.querySelector('[data-snap-axis="x"]'));
  svg.dispatchEvent(new dom.window.MouseEvent('pointerup'));await tick();
  assert.deepEqual(calls.find(c=>c.url.endsWith('/save')).body.operation.bbox,[20,10,100,20]);
});
