"""Parse local document_sets and build a reviewable history of their dependencies."""
import argparse
import ast
import hashlib
import json
import re
import shutil
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from parse_structure import atomic_json, parse_pdf
from structure_semantics import key
from visualize_structure import create_viewer


def load_sets(root):
    result=[]
    root=Path(root).resolve()
    for path in sorted(root.glob('*/set.json')):
        item=json.loads(path.read_text(encoding='utf-8'))
        for doc in [item['итоговый_документ'],*item['первичные_документы']]:
            if not re.fullmatch(r'[A-Za-z0-9_-]+',doc['id']):
                raise ValueError('Invalid document identifier')
            source=(path.parent/doc['локальный_файл']).resolve()
            if not source.is_relative_to(root) or not source.is_file():
                raise ValueError(f"Missing/invalid local document: {source}")
        result.append((path.parent,item))
    return result


def first_code(label):
    match=re.match(r'\s*(\d+(?:\.\d+)*)(?:\.|\s|$)',label)
    return match[1].rstrip('.') if match else None


def column_matches(wanted, actual):
    wanted=key(wanted);actual=key(actual[-1] if actual else '')
    if not actual:
        return True
    for word in ('исполн','план'):
        if word in wanted:
            # "Исполнение плана" is execution, not the plan column.
            if word=='план' and 'исполн' in wanted:
                continue
            if ('план' in actual or 'исполн' in actual) and (word not in actual or word=='план' and 'исполн' in actual):
                return False
            break
    years=re.findall(r'\b(?:19|20)\d{2}\b',wanted)
    actual_years=re.findall(r'\b(?:19|20)\d{2}\b',actual)
    return not years or not actual_years or years[-1] in actual_years


def match_reference(document, reference):
    page_number=reference['страница_PDF']
    label=reference['строка_или_показатель']
    wanted_column=reference['столбец_или_блок']
    code=first_code(label)
    candidates=[f for f in document['facts'] if f['source']['page']==page_number and f['value_type']=='decimal'
                and column_matches(wanted_column,f['column_path'])]
    method='row_code_and_column' if code else 'row_label_and_section'
    if code:
        candidates=[f for f in candidates if any(first_code(part)==code for part in f['row_path'])]
    else:
        section=re.search(r'ЦК\s*№\s*(\d+)',wanted_column,re.I)
        if section:
            candidates=[f for f in candidates if any(re.search(rf'ЦК\s*№\s*{section[1]}(?!\d)',p,re.I) for p in f['row_path'])]
        quarter=re.search(r'([1-4])\s*кварт',label,re.I)
        if quarter:
            candidates=[f for f in candidates if re.search(rf'{quarter[1]}\s*кварт',' '.join(f['row_path']),re.I)]
        stop={'для','на','по','в','и','за','области','строка','первая','вторая'}
        wanted=[t[:5] for t in key(label).split() if len(t)>2 and t not in stop]
        scores=[]
        for fact in candidates:
            tokens=key(' '.join(fact['row_path'])).split()
            score=sum(any(t.startswith(w) for t in tokens) for w in wanted)
            scores.append((score,fact))
        best=max((s for s,_ in scores),default=0)
        candidates=[f for score,f in scores if score==best and score>0]
        if len(candidates)>1 and re.search(r'первая|вторая',label,re.I):
            ordinal=1 if 'вторая' in label else 0
            candidates=sorted(candidates,key=lambda f:f['source']['bbox'][1])[ordinal:ordinal+1]
            method+=' / explicit_occurrence'
    if not candidates:
        # Narrative reports have printed amounts but no table/field labels.
        # A unique value is evidence of presence, not an independent semantic match.
        page=next(p for p in document['pages'] if p['number']==page_number)
        expected=Decimal(reference['значение'])
        candidates=[{'id':n['id'],'value':n['value'],'raw':n['raw'],
                     'source':{'page':page_number,'bbox':n['bbox']},'row_path':[n['context']],'column_path':[]}
                    for n in page.get('numeric_fields',[]) if Decimal(n['value'])==expected]
        method='unique_value_on_declared_page'
    result={'reference':reference,'method':method,'candidate_count':len(candidates),'candidates':candidates,
            'status':'missing' if not candidates else 'ambiguous' if len(candidates)>1 else 'matched'}
    if len(candidates)==1:
        fact=candidates[0]
        actual=Decimal(fact['value'])
        result['source_value']=str(actual)
        if actual<0 and '(' in fact['raw'] and Decimal(reference['значение'])>=0:
            actual=abs(actual)
            result['normalization']='absolute_accounting_expense_as_declared_in_set'
        result.update({'actual':str(actual),'fact_id':fact['id'],'bbox':fact['source']['bbox']})
        if actual!=Decimal(reference['значение']):
            result['status']='mismatch'
    return result


def calculate_bound(formula, expected_operands, actual_operands):
    """Evaluate a restricted arithmetic AST, replacing operand literals by PDF values."""
    tree=ast.parse(formula.lstrip('='),mode='eval')
    cursor=0
    def evaluate(node):
        nonlocal cursor
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):
            literal=Decimal(ast.get_source_segment(formula.lstrip('='),node))
            if cursor<len(expected_operands):
                if literal!=Decimal(expected_operands[cursor]):
                    raise ValueError('Formula operand order differs from declared references')
                literal=Decimal(actual_operands[cursor]);cursor+=1
            return literal
        if isinstance(node,ast.BinOp):
            left,right=evaluate(node.left),evaluate(node.right)
            if isinstance(node.op,ast.Add):return left+right
            if isinstance(node.op,ast.Sub):return left-right
            if isinstance(node.op,ast.Mult):return left*right
            if isinstance(node.op,ast.Div):return left/right
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='ROUND' and len(node.args)==2 and not node.keywords:
            value,digits=evaluate(node.args[0]),evaluate(node.args[1])
            if digits!=int(digits) or abs(digits)>10:raise ValueError('Invalid rounding precision')
            return value.quantize(Decimal(1).scaleb(-int(digits)),rounding=ROUND_HALF_UP)
        raise ValueError('Unsupported formula syntax')
    result=evaluate(tree.body)
    if cursor!=len(actual_operands):raise ValueError('Not all source references were consumed')
    return format(result,'f')


def build(root, output, reparse=False):
    output=Path(output);dest=output/'sets';dest.mkdir(parents=True,exist_ok=True)
    sets=load_sets(root);sources={};documents={};paths={};audit=[]
    for folder,item in sets:
        for doc in [item['итоговый_документ'],*item['первичные_документы']]:
            sources.setdefault(doc['id'],(folder/doc['локальный_файл'],doc))
    for identifier,(source,description) in sources.items():
        print(f'Parsing/opening {identifier}: {source}',flush=True)
        structure=dest/f'{identifier}.structure.json'
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        document=json.loads(structure.read_text()) if structure.exists() and not reparse else None
        if document is None or document['source']['sha256']!=digest:
            document=parse_pdf(source,cache_dir='tmp/pdfs/ocr-cache');atomic_json(structure,document)
        documents[identifier]=document;paths[identifier]=source
        create_viewer(source,document,dest/f'{identifier}.viewer.html',dpi=110)
        shutil.copyfile(source,dest/f'{identifier}.pdf')
        audit.append({'document':identifier,'action':'read_and_parsed_local_pdf','sha256':digest,
                      'pages':len(document['pages']),'facts':len(document['facts'])})
    reports=[]
    for folder,item in sets:
        references=[item['итоговая_ячейка'],*item['исходные_ячейки']]
        matches=[match_reference(documents[r['документ']],r) for r in references]
        computed=None
        if all('actual' in m for m in matches):
            computed=calculate_bound(item['формула'],[r['значение'] for r in references[1:]],
                                     [m['actual'] for m in matches[1:]])
        numeric_ok=computed is not None and Decimal(computed)==Decimal(matches[0]['actual'])
        all_matched=all(m['status']=='matched' for m in matches)
        weak=any(m['method']=='unique_value_on_declared_page' for m in matches)
        status='verified_values' if numeric_ok and all_matched and weak else 'verified' if numeric_ok and all_matched else 'needs_review'
        report={'id':item['id'],'title':item['тема'],'folder':folder.name,'formula':item['формула'],
                'expected':item['расчетный_результат'],'calculated':computed,'status':status,
                'arithmetic_matches':numeric_ok,'matches':matches,'note':item.get('примечание',''),
                'documents':list(dict.fromkeys(r['документ'] for r in references))}
        for match in matches:
            ref=match['reference'];identifier=ref['документ']
            match['url']=f"sets/{identifier}.viewer.html#page={ref['страница_PDF']}"
            if match.get('fact_id'):
                from urllib.parse import quote
                match['url']+='&focus='+quote(match['fact_id'],safe='')
        reports.append(report)
        print(item['id'],status,'computed',computed,[(m['status'],m.get('actual'),m['candidate_count']) for m in matches],flush=True)
    manifest={'schema_version':'document-sets/1','sets':reports,'documents':[
        {'id':id,'name':description['название'],'pages':len(documents[id]['pages']),
         'viewer':f'sets/{id}.viewer.html','original':f'sets/{id}.pdf','structure':f'sets/{id}.structure.json'}
        for id,(_,description) in sources.items()], 'processing_history':audit,
        'summary':{'sets':len(reports),'documents':len(documents),'pages':sum(len(d['pages']) for d in documents.values()),
                   'verified':sum(r['status']=='verified' for r in reports),
                   'verified_values':sum(r['status']=='verified_values' for r in reports),
                   'needs_review':sum(r['status']=='needs_review' for r in reports)}}
    atomic_json(output/'document-sets.manifest.json',manifest)
    template=Path(__file__).with_name('document_sets_viewer.html').read_text()
    payload=json.dumps(manifest,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
    (output/'document-sets.html').write_text(template.replace('__SETS_DATA__',payload),encoding='utf-8')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('document_sets'))
    parser.add_argument('--output',type=Path,default=Path('output/pdf'))
    parser.add_argument('--reparse',action='store_true')
    args=parser.parse_args()
    result=build(args.root,args.output,args.reparse)
    print(json.dumps(result['summary'],ensure_ascii=False))
