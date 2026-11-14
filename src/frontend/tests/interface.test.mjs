import test from "node:test";
import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { createRequire, isBuiltin } from "node:module";

test("navigation, original selection, zoom, text selection and Word highlights", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "audit-interface-"));
  const require = createRequire(import.meta.url);
  try {
    const entry = path.join(directory, "interface.cjs");
    await build({
      stdin: {
        resolveDir: process.cwd(),
        loader: "tsx",
        contents: `
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import React,{act} from 'react';

import JSZip from 'jszip';
import Viewer from './src/DocumentViewer';
import {WordOriginal} from './src/OfficeOriginal';
import FormulaOverlay from './src/FormulaOverlay';
import TableVariableLabel from './src/TableVariableLabel';
import {WorkspaceActions,ProjectHistory} from './src/ProjectNavigation';
import App from './src/App';
module.exports=async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost'});
 for(const key of ['window','document','HTMLElement','Element','Node','DOMParser','XMLSerializer','FileReader','Blob','getComputedStyle','Event'])globalThis[key]=dom.window[key];
 globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 globalThis.localStorage=window.localStorage;
 window.requestAnimationFrame=fn=>setTimeout(fn,0);
 const {createRoot}=await import('react-dom/client');
 let automaticScroll=0;window.HTMLElement.prototype.scrollIntoView=function(){automaticScroll++;};
 const nativeGetSelection=window.getSelection.bind(window);
 const root=createRoot(document.getElementById('root'));
 const report={id:'report',name:'Отчет.pdf',role:'report',format:'pdf',page_count:1,facts:{},blocks:[{text:'123,00',page:1,rect:[.2,.2,.4,.3]}],cells:{}};
 const primary={...report,id:'primary',name:'Первичка.pdf',role:'source',included:true};
 const other={...primary,id:'other',name:'Другой.pdf'};
 const unused={...primary,id:'unused',name:'Не участвует.pdf'};
 const cell={expected:'100',delta:'23',rule:{expression:'x1 + x2'},inputs:[{name:'x1',ref:'source',value:'70'},{name:'x2',ref:'source2',value:'30'}],id:'r2026_c15',kind:'check',code:'0800',title:'Затраты',row:'2026',reported:'123',status:'review',numeric_status:'mismatch',reported_evidence:{document_id:'report',page:1,rect:[.2,.2,.4,.3]},dependencies:['source','source2']};
 const noFormula={...cell,id:'r2025_c15',row:'2025',rule:null,inputs:[],dependencies:[],expected:null,delta:null,numeric_status:'unverified'};
 await act(async()=>root.render(<FormulaOverlay node={noFormula} onVariable={()=>{}} onDetails={()=>{}} onEdit={()=>{}}/>));
 assert.equal(document.querySelector('.formula-status-pill'),null);
 assert.equal(document.querySelector('.formula-overlay').textContent.includes('Нет формулы'),false);
 assert.ok(document.querySelector('.formula-cell-value').textContent.includes('123'));
 assert.ok(document.querySelector('[aria-label="Редактировать формулу"]'));
 const widthDescriptor=Object.getOwnPropertyDescriptor(window.HTMLElement.prototype,'clientWidth');
 const scrollDescriptor=Object.getOwnPropertyDescriptor(window.HTMLElement.prototype,'scrollWidth');
 Object.defineProperty(window.HTMLElement.prototype,'clientWidth',{configurable:true,get(){return 90;}});
 Object.defineProperty(window.HTMLElement.prototype,'scrollWidth',{configurable:true,get(){return this.textContent.length*parseFloat(this.style.fontSize||'11')*.6;}});
 const longLabel='0200 · x1 / BASE.2026-01 · x1 · C8';
 for(const zoom of [25,100,355]){
   await act(async()=>root.render(<TableVariableLabel label={longLabel} zoom={zoom}/>));
   const label=document.querySelector('.table-variable-label');const font=parseFloat(label.firstElementChild.style.fontSize);
   assert.ok(font*zoom/100<=11.01,'Label has a capped screen size');
   assert.ok(font<=11/(zoom/100),'Label shrinks to fit its cell');
   assert.equal(label.title,longLabel);
 }
 if(widthDescriptor)Object.defineProperty(window.HTMLElement.prototype,'clientWidth',widthDescriptor);else delete window.HTMLElement.prototype.clientWidth;
 if(scrollDescriptor)Object.defineProperty(window.HTMLElement.prototype,'scrollWidth',scrollDescriptor);else delete window.HTMLElement.prototype.scrollWidth;
 const result={id:'case',report_id:'report',documents:[unused,other,primary,report],checks:[cell,noFormula],nodes:{[cell.id]:cell,[noFormula.id]:noFormula,source:{id:'source',evidence:{document_id:'primary',locator:'Страница 1',page:1,rect:[.2,.2,.4,.3]},dependencies:[]},source2:{id:'source2',evidence:{document_id:'other',locator:'Страница 1',page:1,rect:[.2,.2,.4,.3]},dependencies:[]}}};
 let picked='',action='';
 await act(async()=>root.render(<WorkspaceActions result={result} busy={false} onDemo={()=>{}} onView={()=>{}} onRefresh={()=>action='refresh'}/>));
 assert.equal(document.querySelector('[aria-label="Загрузить документы"]'),null);
 assert.equal(document.querySelector('[aria-label="Скачать заключение"]'),null);
 await act(async()=>document.querySelector('[aria-label="Обновить проверку"]').click());assert.equal(action,'refresh');
 let width=220,renamed='',hidden=false;
 await act(async()=>root.render(<ProjectHistory cases={[{id:'one',name:'Первый',created_at:'2026-11-14T12:00:00Z'},{id:'two',name:'Второй',created_at:'2026-11-14T13:00:00Z'}]} activeId="one" onSelect={id=>picked=id} onCreate={()=>{}} onHide={()=>hidden=true} width={width} onWidth={value=>width=value} onRename={async(id,name)=>{renamed=id+':'+name;return true;}}/>));
 await act(async()=>document.querySelectorAll('.project-entry-button')[1].click());assert.equal(picked,'two');
 await act(async()=>document.querySelectorAll('.project-entry-button')[0].dispatchEvent(new window.MouseEvent('dblclick',{bubbles:true})));
 const input=document.querySelector('.project-rename-input');assert.ok(input);
 await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,'Новое имя');input.dispatchEvent(new window.Event('input',{bubbles:true}));});
 await act(async()=>input.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Enter',bubbles:true})));assert.equal(renamed,'one:Новое имя');
 await act(async()=>document.querySelector('[role="separator"]').dispatchEvent(new window.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true})));assert.equal(width,236);
 await act(async()=>document.querySelector('[aria-label="Скрыть проекты"]').click());assert.ok(hidden);
 picked='';
 await act(async()=>root.render(<Viewer caseId="case" document={report} highlights={[cell.reported_evidence]} activeEvidence={cell.reported_evidence} reportNodes={[cell,noFormula]} selectedIds={[]} onSelectReport={n=>picked=n.id}/>));
 assert.equal(document.querySelectorAll('.original-status-dot').length,1,'Only cells with a formula carry a verification status');
 assert.ok(document.querySelector('.report-image-cell.mismatch .original-status-dot'));
 assert.equal(document.querySelector('table'),null);
 assert.ok(document.querySelector('.document-text-layer').textContent.includes('123,00'));
 const canvas=document.querySelector('.file-page-canvas');canvas.getBoundingClientRect=()=>({left:0,top:0,width:1000,height:1000});
 await act(async()=>canvas.dispatchEvent(new window.MouseEvent('click',{bubbles:true,clientX:300,clientY:250})));assert.equal(picked,cell.id);
 picked='';window.getSelection=()=>({toString:()=> '123,00'});
 await act(async()=>canvas.dispatchEvent(new window.MouseEvent('click',{bubbles:true,clientX:300,clientY:250})));assert.equal(picked,'');
 window.getSelection=()=>({toString:()=>''});
 const initialWidth=parseFloat(document.querySelector('.file-page-canvas').style.width);
 assert.equal(document.querySelector('.viewer-zoom'),null);
 await act(async()=>document.querySelector('.original-viewport').dispatchEvent(new window.WheelEvent('wheel',{bubbles:true,cancelable:true,ctrlKey:true,deltaY:-30})));
 assert.ok(parseFloat(document.querySelector('.file-page-canvas').style.width)>initialWidth,'Wheel zoom remains available without toolbar controls');
 const viewport=document.querySelector('.original-viewport');
 await act(async()=>viewport.dispatchEvent(new window.WheelEvent('wheel',{bubbles:true,cancelable:true,shiftKey:true,deltaY:80})));assert.ok(viewport.scrollLeft>=80);
 let pathX=viewport.scrollLeft,pathY=viewport.scrollTop;
 for(const [dx,dy] of [[12,18],[-9,16],[14,-11],[-8,-13],[.75,.5],[-.25,.75]]){
   const wheel=new window.WheelEvent('wheel',{bubbles:true,cancelable:true,deltaX:dx,deltaY:dy});
   await act(async()=>viewport.dispatchEvent(wheel));
   pathX+=dx*2;pathY+=dy*2;
   assert.equal(wheel.defaultPrevented,true,'The viewer owns both axes instead of native axis locking');
   assert.equal(viewport.scrollLeft,pathX,'Every diagonal and reversed horizontal delta is preserved');
   assert.equal(viewport.scrollTop,pathY,'Every diagonal and reversed vertical delta is preserved');
 }
 await act(async()=>viewport.dispatchEvent(new window.WheelEvent('wheel',{bubbles:true,cancelable:true,deltaMode:1,deltaX:2,deltaY:-1})));
 assert.equal(viewport.scrollLeft,pathX+64);assert.equal(viewport.scrollTop,pathY-32);
 viewport.getBoundingClientRect=()=>({left:100,top:60,width:1000,height:700,right:1100,bottom:760});
 viewport.scrollLeft=1250;viewport.scrollTop=420;
 const documentPoint=(clientX,clientY)=>{
   const width=parseFloat(document.querySelector('.file-page-canvas').style.width);
   return [(viewport.scrollLeft+clientX-100-1000)/width,(viewport.scrollTop+clientY-60-350)/width];
 };
 const zoomUnderCursor=async(clientX,clientY,deltas)=>{
   const before=documentPoint(clientX,clientY);
   await act(async()=>{
     for(const deltaY of deltas)viewport.dispatchEvent(new window.WheelEvent('wheel',{bubbles:true,cancelable:true,ctrlKey:true,clientX,clientY,deltaY}));
   });
   await act(async()=>{await new Promise(resolve=>setTimeout(resolve,5));});
   const after=documentPoint(clientX,clientY);
   assert.ok(Math.abs(before[0]-after[0])<1e-9,'Horizontal document point remains under the cursor during a batched zoom burst');
   assert.ok(Math.abs(before[1]-after[1])<1e-9,'Vertical document point remains under the cursor during a batched zoom burst');
 };
 await zoomUnderCursor(370,270,[-12,-18,-9]);
 await zoomUnderCursor(740,380,[18,12,9]);
 viewport.scrollTop=140;viewport.scrollLeft=85;
 await act(async()=>root.render(<Viewer caseId="case" document={report} highlights={[]} activeEvidence={{...cell.reported_evidence,rect:[.8,.8,.9,.9]}} reportNodes={[cell]} selectedIds={[cell.id]}/>));
 assert.equal(viewport.scrollTop,140);assert.equal(viewport.scrollLeft,85);
 assert.equal(document.querySelector('[aria-label="На весь экран"]'),null);
 assert.equal(document.querySelector('.viewer-floating-toolbar a'),null);
 const zip=new JSZip();
 zip.file('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>');
 zip.file('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>');
 zip.file('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Акт № 1</w:t></w:r></w:p><w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="4000"/><w:gridCol w:w="4000"/></w:tblGrid><w:tr><w:tc><w:p><w:r><w:t>Сумма</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>123,00</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>');
 const bytes=await zip.generateAsync({type:'uint8array'});
 globalThis.fetch=async()=>({ok:true,blob:async()=>new Blob([bytes])});
 const evidence={locator:'Таблица 1, строка 1',text:'Сумма | 123,00'};
 let ready=false;
 await act(async()=>root.render(<WordOriginal url="/word" highlights={[evidence]} activeEvidence={evidence} onSelect={()=>{}} onReady={()=>ready=true}/>));
 for(let i=0;i<30&&!ready;i++)await act(async()=>{await new Promise(resolve=>setTimeout(resolve,10));});
 assert.ok(ready,document.body.textContent);
 assert.ok(document.querySelector('section.docx'));
 assert.equal(document.querySelector('.original-word-active').textContent,'Сумма123,00');
 assert.equal(document.querySelector('.source-number-mark').textContent,'123,00');
 assert.equal(document.querySelector('[data-locator="Абзац 1"]').textContent,'Акт № 1');
 result.context={target_year:'2026'}; result.name='Проект'; result.revision=2;
 result.nodes.source.kind='fact';result.nodes.source.expected='70';result.nodes.source.inputs=[];
 result.nodes.source2.kind='fact';result.nodes.source2.expected='30';result.nodes.source2.inputs=[];
 let caseResult=result;let savedRequest;
 globalThis.fetch=async(url,options)=>({ok:true,json:async()=>{
   if(url==='/api/cases')return [{id:'case',name:'Проект',created_at:'2026-11-14T12:00:00Z',revision:caseResult.revision}];
   if(url.endsWith('/numeric-regions'))return [];
   if(url.endsWith('/formula-preview'))return {expected:'223.00',delta:'-100.00',status:'review'};
   if(url.endsWith('/changes')){
     savedRequest=JSON.parse(options.body);const next=structuredClone(caseResult);next.revision++;
     const target=next.nodes[savedRequest.target];target.rule=savedRequest.values.rule;
     for(const variable of savedRequest.values.variables){const id=variable.document_id+':'+variable.key;const fact={...variable,id,kind:'money',label:'Выбранное число',value:variable.value,dependencies:[],needs_review:false};next.documents.find(d=>d.id===variable.document_id).facts[variable.key]=fact;next.nodes[id]={...fact,kind:'fact',expected:variable.value,inputs:[],status:'verified'};next.variables=[...(next.variables||[]),{...variable,id,check_id:target.id}];}
     target.inputs=target.rule.inputs.map(input=>({...input,value:next.nodes[input.ref]?.expected}));next.checks=next.checks.map(n=>n.id===target.id?target:n);caseResult=next;return next;
   }
   return caseResult;
 }});
 window.getSelection=nativeGetSelection;
 await act(async()=>root.render(<App/>));
 assert.equal(document.querySelector('.application-header'),null);
 assert.equal(document.querySelector('.application-tabs'),null);
 assert.equal(document.querySelector('.application-logo'),null);
 assert.equal(document.querySelector('.unified-explorer h2').textContent,'Explorer');
 assert.equal(document.querySelector('.verification-toolbar'),null);
 assert.equal(document.querySelector('.application-save-state'),null);
 assert.equal(document.querySelector('.verification-file-panel'),null);
 assert.ok(document.querySelector('.unified-explorer'));
 assert.ok(document.querySelector('.single-document-stage'));
 assert.equal(document.querySelectorAll('.original-viewer').length,1);
 assert.equal(document.querySelector('.primary-documents'),null);
 assert.equal(document.querySelector('.detail-panel'),null);
 assert.ok(document.querySelector('.project-history'));
 const fileButtons=()=>[...document.querySelectorAll('.project-history [data-document-id]')];
 assert.equal(fileButtons()[0].dataset.documentId,'report');assert.equal(fileButtons().length,1);
 await act(async()=>document.querySelector('[aria-label="Раскрыть первичные файлы"]').click());assert.equal(fileButtons().length,4);
 assert.equal(document.querySelectorAll('.unified-project a[data-document-id]').length,4);
 const primaryLink=fileButtons().find(link=>link.dataset.documentId==='primary');
 assert.equal(primaryLink.getAttribute('href'),'/?project=case&document=primary');
 const loadImage=async()=>{const image=document.querySelector('.file-page-canvas>img');if(image){Object.defineProperty(image,'naturalWidth',{value:1000,configurable:true});Object.defineProperty(image,'naturalHeight',{value:1000,configurable:true});await act(async()=>{image.dispatchEvent(new window.Event('load'));await new Promise(resolve=>setTimeout(resolve,5));});}};
 await loadImage();
 await act(async()=>document.querySelector('.original-viewport').dispatchEvent(new window.WheelEvent('wheel',{bubbles:true,cancelable:true,ctrlKey:true,deltaY:-30})));
 await act(async()=>{await new Promise(resolve=>setTimeout(resolve,5));});
 const reportZoom=document.querySelector('.file-page-canvas').style.width;
 let reportView=document.querySelector('.original-viewport');await act(async()=>{reportView.scrollLeft=1260;reportView.scrollTop=465;reportView.dispatchEvent(new window.Event('scroll'));});

 await act(async()=>primaryLink.click());
 assert.equal(primaryLink.getAttribute('aria-current'),'page');
 assert.equal(window.location.search,'?project=case&document=primary');
 assert.ok(document.querySelector('.viewer-name-pill').textContent.includes('Первичка.pdf'));
 assert.ok(document.querySelector('.variable-cell-label'));
 await act(async()=>document.querySelector('.formula-result-link').click());await loadImage();
 assert.equal(document.querySelector('.file-page-canvas').style.width,reportZoom);
 reportView=document.querySelector('.original-viewport');assert.equal(reportView.scrollLeft,1260);assert.equal(reportView.scrollTop,465);
 const lockedCanvas=document.querySelector('.file-page-canvas');lockedCanvas.getBoundingClientRect=()=>({left:0,top:0,width:1000,height:1000,right:1000,bottom:1000});
 await act(async()=>lockedCanvas.dispatchEvent(new window.MouseEvent('dblclick',{bubbles:true,clientX:300,clientY:250})));assert.equal(document.querySelector('.modal'),null);
 await act(async()=>document.querySelector('[aria-label="Разрешить редактирование документа"]').click());
 await act(async()=>lockedCanvas.dispatchEvent(new window.MouseEvent('dblclick',{bubbles:true,clientX:300,clientY:250})));assert.ok(document.querySelector('.modal'));
 await act(async()=>[...document.querySelectorAll('.modal button')].find(button=>button.textContent==='Отмена').click());

 await act(async()=>fileButtons()[0].click());
 assert.ok(document.querySelector('.formula-overlay').textContent.includes('Сейчас'));
 assert.ok(document.querySelector('.formula-overlay.viewer-pill .formula-primary-row .formula-status-pill.mismatch'));
 assert.ok(document.querySelector('.formula-overlay.viewer-pill > .formula-comparison-pill:not(.viewer-pill)'));
 await act(async()=>document.querySelector('[aria-label="Редактировать формулу"]').click());
 let formula=document.querySelector('[aria-label="Математическая формула"]');
 const expression='x1 + x2 + ';
 await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set.call(formula,expression);formula.setSelectionRange(expression.length,expression.length);formula.dispatchEvent(new window.Event('input',{bubbles:true}));});
 await act(async()=>formula.dispatchEvent(new window.KeyboardEvent('keydown',{key:'/',bubbles:true,cancelable:true})));
 assert.equal(formula.value,expression);assert.ok(document.querySelector('.formula-file-picker'));
 await act(async()=>[...document.querySelectorAll('.formula-file-picker [role="option"]')].find(button=>button.textContent.includes('Первичка.pdf')).click());
 assert.equal(document.querySelector('.formula-file-picker'),null);
 assert.ok(document.querySelector('.viewer-name-pill').textContent.includes('Первичка.pdf'));
 const canvasPick=document.querySelector('.file-page-canvas');canvasPick.getBoundingClientRect=()=>({left:0,top:0,width:1000,height:1000,right:1000,bottom:1000});
 window.Range.prototype.getBoundingClientRect=()=>({left:200,top:200,right:400,bottom:300,width:200,height:100});
 const range=document.createRange();range.selectNodeContents(document.querySelector('.document-text-layer span'));nativeGetSelection().removeAllRanges();nativeGetSelection().addRange(range);
 await act(async()=>{document.querySelector('.original-viewport').dispatchEvent(new window.Event('pointerup',{bubbles:true}));await new Promise(resolve=>setTimeout(resolve,10));});
 const use=[...document.querySelectorAll('.number-selection-popover button')][0];assert.ok(use,'Selection offers a new variable');
 await act(async()=>use.click());
 formula=document.querySelector('[aria-label="Математическая формула"]');assert.equal(formula.value,'x1 + x2 + x3');
 const reason=document.querySelector('[aria-label="Причина изменения формулы"]');
 await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(reason,'Проверено по выбранному числу');reason.dispatchEvent(new window.Event('input',{bubbles:true}));await new Promise(resolve=>setTimeout(resolve,350));});
 const save=[...document.querySelectorAll('.formula-editor-shell button')].find(button=>button.textContent==='Сохранить');assert.ok(!save.disabled);
 await act(async()=>save.click());
 assert.equal(savedRequest.values.variables.length,1);assert.equal(savedRequest.values.variables[0].value,'123');assert.ok(savedRequest.values.variables[0].evidence.rect);
 assert.equal(document.querySelector('.formula-editor-shell'),null);
 await act(async()=>fileButtons()[0].click());
 await act(async()=>fileButtons().find(button=>button.title==='Первичка.pdf').click());
 assert.ok([...document.querySelectorAll('.variable-cell-label')].some(label=>label.textContent.includes('x3')),'Saved variable stays marked after switching files');
 assert.equal(automaticScroll,0);
 await act(async()=>document.querySelector('[aria-label="Скрыть проекты"]').click());assert.equal(document.querySelector('.project-history'),null);
 const sash=()=>document.querySelector('[aria-label="Ширина Explorer"]');
 assert.equal(sash().getAttribute('aria-valuenow'),'0');
 await act(async()=>sash().dispatchEvent(new window.KeyboardEvent('keydown',{key:'Enter',bubbles:true})));assert.ok(document.querySelector('.project-history'));
 const originalWidth=Number(sash().getAttribute('aria-valuenow'));
 const pointer=(type,x)=>new window.MouseEvent(type,{button:0,clientX:x,bubbles:true,cancelable:true});
 await act(async()=>sash().dispatchEvent(pointer('pointerdown',originalWidth)));
 await act(async()=>sash().dispatchEvent(pointer('pointermove',15)));
 assert.equal(document.querySelector('.project-history'),null,'Dragging the divider to the edge fully collapses Explorer');
 assert.ok(sash().classList.contains('collapsed'));
 await act(async()=>sash().dispatchEvent(pointer('pointermove',originalWidth)));
 assert.ok(document.querySelector('.project-history'),'The same drag can pull Explorer back out');
 await act(async()=>sash().dispatchEvent(pointer('pointerup',originalWidth)));
 assert.equal(Number(sash().getAttribute('aria-valuenow')),originalWidth);
 await act(async()=>sash().dispatchEvent(new window.KeyboardEvent('keydown',{key:'Home',bubbles:true})));
 await act(async()=>sash().dispatchEvent(pointer('pointerdown',1)));
 await act(async()=>sash().dispatchEvent(pointer('pointermove',201)));
 await act(async()=>sash().dispatchEvent(pointer('pointerup',201)));
 assert.equal(Number(sash().getAttribute('aria-valuenow')),200,'Collapsed edge handle can be dragged to a new width');
 assert.equal(document.querySelector('.explorer-restore'),null);
 assert.equal(document.querySelector('#template-tab'),null);
 assert.equal(document.querySelector('#template-panel'),null);
 window.history.replaceState(null,'','/?project=case&document=primary');
 await act(async()=>root.render(<App key="linked" />));
 assert.ok(document.querySelector('.viewer-name-pill').textContent.includes('Первичка.pdf'),'A direct link opens the requested document');
 assert.equal(document.querySelector('.unified-source-row[aria-current="page"]').dataset.documentId,'primary');
 await act(async()=>root.unmount());dom.window.close();
};
`,
      },
      bundle: true,
      platform: "node",
      format: "cjs",
      jsx: "automatic",
      outfile: entry,
      logLevel: "silent",
      plugins: [
        {
          name: "test-dependencies",
          setup(build) {
            build.onResolve({ filter: /^[^./]/ }, (args) =>
              isBuiltin(args.path)
                ? { path: args.path, external: true }
                : { path: require.resolve(args.path), external: true },
            );
          },
        },
      ],
    });
    await require(entry)();
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
