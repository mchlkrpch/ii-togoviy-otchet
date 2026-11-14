"""Validated manual corrections and deterministic word-aligned field splitting."""
import copy
import difflib
import re

from native_structure import decimal_text


class EditError(ValueError):
    pass


def union(boxes):
    boxes=list(boxes)
    return [min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]


def validate_box(box, size):
    if not isinstance(box,list) or len(box)!=4 or any(type(v) not in (int,float) for v in box):
        raise EditError('Границы должны содержать четыре числа')
    x0,y0,x1,y1=box
    if not (0<=x0<x1<=size[0] and 0<=y0<y1<=size[1]):
        raise EditError('Прямоугольник должен находиться внутри страницы и иметь положительный размер')
    return [round(v,3) for v in box]


def field_text(node):
    return node.get('value','') if node['kind']=='key_value' else node.get('raw',node['label']) if node['kind']=='value' else node['label']


def field_words(page,node):
    box=node.get('field_bbox',node['bbox'])
    words=[w for w in page.get('words',[]) if box[0] <= (w['bbox'][0]+w['bbox'][2])/2 <= box[2]
           and box[1] <= (w['bbox'][1]+w['bbox'][3])/2 <= box[3]]
    lines=[]
    for word in sorted(words,key=lambda w:(w['bbox'][1],w['bbox'][0])):
        cy=(word['bbox'][1]+word['bbox'][3])/2
        height=word['bbox'][3]-word['bbox'][1]
        line=next((line for line in reversed(lines) if abs(line['cy']-cy)<max(1.5,height*.5)),None)
        if line is None:
            line={'cy':cy,'words':[]};lines.append(line)
        line['words'].append(word)
    return [w for line in lines for w in sorted(line['words'],key=lambda w:w['bbox'][0])]


def split_boxes(page,node,parts):
    """Align both edited parts to source characters; use actual word rectangles.

    Boundaries within one word use proportional character extents and are marked
    approximate. Unalignable text has an explicit geometric fallback for review.
    """
    if len(parts)!=2 or not all(isinstance(p,str) and p.strip() for p in parts):
        raise EditError('Укажите две непустые части текста')
    parts=[' '.join(p.split()) for p in parts]
    words=field_words(page,node)
    source=' '.join(w['text'].casefold() for w in words)
    target=' '.join(parts).casefold()
    start=0;spans=[]
    for word in words:
        end=start+len(word['text'].casefold());spans.append((start,end,word));start=end+1
    matches=difflib.SequenceMatcher(None,source,target,autojunk=False).get_matching_blocks()
    bounds=[(0,len(parts[0])),(len(parts[0])+1,len(target))]
    boxes=[];coverage=[];partial=False
    mapped_parts=[]
    for a,b in bounds:
        mapped=set()
        for match in matches:
            lo,hi=max(a,match.b),min(b,match.b+match.size)
            mapped.update(match.a+i-match.b for i in range(lo,hi) if not target[i].isspace())
        mapped_parts.append(mapped)
    for part_index,(a,b) in enumerate(bounds):
        mapped=mapped_parts[part_index]
        pieces=[]
        for lo,hi,word in spans:
            positions=[i for i in mapped if lo<=i<hi]
            if not positions:continue
            x0,y0,x1,y1=word['bbox']
            shared=any(lo<=i<hi for i in mapped_parts[1-part_index])
            left,right=(min(positions)-lo,max(positions)-lo+1) if shared else (0,hi-lo)
            if shared:partial=True
            pieces.append([x0+(x1-x0)*left/(hi-lo),y0,x0+(x1-x0)*right/(hi-lo),y1])
        count=sum(not c.isspace() for c in target[a:b])
        coverage.append(len(mapped)/max(1,count))
        boxes.append(union(pieces) if pieces else None)
    fallback=any(b is None for b in boxes) or min(coverage)<.45
    if fallback:
        x0,y0,x1,y1=node.get('field_bbox',node['bbox'])
        cut=x0+(x1-x0)*len(parts[0])/max(1,sum(map(len,parts)))
        boxes=[[x0,y0,cut,y1],[cut,y0,x1,y1]]
    boxes=[validate_box([max(0,b[0]),max(0,b[1]),min(page['size'][0],b[2]),min(page['size'][1],b[3])],page['size']) for b in boxes]
    method='estimated_rectangle' if fallback else 'character_alignment' if partial else 'source_word_boxes'
    return {'boxes':boxes,'method':method,'coverage':[round(c,3) for c in coverage],
            'needs_review':fallback or partial or min(coverage)<.9}


def refresh(page):
    nodes={n['id']:n for n in page['nodes']}
    for n in page['nodes']:
        n['children']=list(dict.fromkeys(c for c in n['children'] if c in nodes and c!=n['id']))
    for n in page['nodes']:
        parents=[];parent=n.get('parent');seen=set()
        while parent:
            if parent in seen:raise EditError('Цикл в дереве полей')
            seen.add(parent)
            p=nodes.get(parent)
            if not p:break
            if p['kind']=='header':parents.insert(0,p['label'])
            parent=p.get('parent')
        if parents and n['kind'] in ('header','value','text'):
            n['column_path']=[*parents,*([n['label']] if n['kind']=='header' else [])]
        for ids_key,boxes_key in (('column_header_node_ids','column_header_bboxes'),('row_header_node_ids','row_header_bboxes')):
            if ids_key in n:
                resolved=[]
                for id in n[ids_key]:
                    for _ in range(20):
                        alias=page.get('aliases',{}).get(id)
                        if not alias:break
                        id=alias
                    if id in nodes and id not in resolved:resolved.append(id)
                n[ids_key]=resolved
                n[boxes_key]=[nodes[id].get('field_bbox',nodes[id]['bbox']) for id in resolved]
        if n.get('metric') and n.get('column_path'):
            from structure_semantics import column_semantics
            semantic=column_semantics(n['column_path']);n['metric']=semantic['metric'];n['unit']=semantic['unit']
    def visit(n):
        boxes=[n['field_bbox']] if n.get('field_bbox') else []
        for child in n['children']:
            boxes.append(visit(nodes[child]))
        if boxes and n['children']:n['bbox']=union(boxes)
        return n['bbox']
    for n in page['nodes']:
        if not n.get('parent'):visit(n)
    for row in page['row_roots']:
        row['children']=list(dict.fromkeys(c for c in row['children'] if c in nodes))
        boxes=[nodes[c].get('field_bbox',nodes[c]['bbox']) for c in row['children']]
        for id in row['children']:
            if boxes and 'row_bbox' in nodes[id]:nodes[id]['row_bbox']=union(boxes)


def apply_operation(model,operation,serial):
    page=next((p for p in model['pages'] if p['number']==operation.get('page')),None)
    if page is None:raise EditError('Страница не найдена')
    nodes={n['id']:n for n in page['nodes']}
    ids=operation.get('ids',[])
    if not isinstance(ids,list) or not 1<=len(ids)<=2 or len(set(ids))!=len(ids):raise EditError('Выберите одно или два разных поля')
    if any(id not in nodes for id in ids):raise EditError('Поле больше не существует')
    selected=[nodes[id] for id in ids]
    action=operation.get('action');node=selected[0]
    if action not in ('update','merge','split'):raise EditError('Неизвестное действие')
    if action!='merge' and len(ids)!=1:raise EditError('Выберите одно поле')
    if action in ('merge','split') and any(n['children'] for n in selected):
        raise EditError('Объединять и разделять можно конечные поля, а не целые ветки')
    if not node.get('field_bbox'):raise EditError('Выберите поле с собственным прямоугольником')
    node.setdefault('original',{'text':field_text(node),'bbox':copy.deepcopy(node['field_bbox'])})
    if action=='update':
        if 'bbox' in operation:
            box=validate_box(operation['bbox'],page['size'])
            if node['kind']=='key_value':
                old=node['field_bbox'];sx=(box[2]-box[0])/(old[2]-old[0]);sy=(box[3]-box[1])/(old[3]-old[1])
                for field in ('key_bbox','value_bbox'):
                    b=node[field];node[field]=[box[0]+(b[0]-old[0])*sx,box[1]+(b[1]-old[1])*sy,
                                              box[0]+(b[2]-old[0])*sx,box[1]+(b[3]-old[1])*sy]
            node['field_bbox']=box
            node['bbox']=box
        if 'text' in operation:
            text=operation['text']
            if not isinstance(text,str) or not text.strip() or len(text)>20000:raise EditError('Введите текст длиной от 1 до 20000 символов')
            text=text.strip()
            node['text_modified']=True
            if node['kind']=='key_value':
                node['value']=text;node['raw']=node['label']+': '+text
            else:
                node['raw']=text
                if node['kind']=='value':
                    value=decimal_text(text);node['label']=value if value is not None else text
                    node['value_type']='decimal' if value is not None else 'string'
                else:node['label']=text
            for other in page['nodes']:
                if node['id'] in other.get('row_header_node_ids',[]):
                    other['row_path']=[nodes[i]['label'] for i in other['row_header_node_ids'] if i in nodes]
                    other['row_hierarchy']=other['row_path'];other['needs_review']=True
        if 'label' in operation:
            label=operation['label']
            if node['kind']!='key_value' or not isinstance(label,str) or not label.strip():raise EditError('Название ключа не задано')
            from structure_semantics import key
            node['label']=label.strip();node['key']=key(label)
            node['raw']=node['label']+': '+node['value'];node['column_path']=[node['label']]
    elif action=='merge':
        if len(selected)!=2 or any(not n.get('field_bbox') for n in selected):raise EditError('Нужны два конечных поля')
        other=selected[1]
        def ancestors(n):
            result=[];p=n.get('parent')
            while p:
                result.append(p);p=nodes[p].get('parent')
            return result
        other_ancestors=set(ancestors(other))
        parent=next((p for p in ancestors(node) if p in other_ancestors),None)
        box=union(n['field_bbox'] for n in selected)
        text=operation.get('text',' '.join(field_text(n) for n in selected))
        if not isinstance(text,str) or not text.strip() or len(text)>20000:raise EditError('Введите текст объединенного поля')
        merged={'id':node['id'],'kind':'text','label':text.strip(),'raw':text.strip(),
                'bbox':box,'field_bbox':box,'parent':parent,'children':[], 'color':node['color'],
                'manual':True,'needs_review':True,'merged_from':copy.deepcopy(selected)}
        merged['text_modified']=True
        page['nodes']=[n for n in page['nodes'] if n['id'] not in ids]+[merged]
        for n in page['nodes']:
            n['children']=[c for c in n['children'] if c not in ids]
        if parent:nodes[parent]['children'].append(merged['id'])
        for row in page['row_roots']:
            row['children']=[merged['id'] if c in ids else c for c in row['children']]
        page.setdefault('aliases',{})[other['id']]=merged['id']
        page['roots']=[r for r in page['roots'] if r not in ids]
        if parent is None:page['roots'].append(merged['id'])
        node=merged
    elif action=='split':
        parts=operation.get('parts',[])
        result=split_boxes(page,node,parts)
        if 'boxes' in operation:
            if not isinstance(operation['boxes'],list) or len(operation['boxes'])!=2:raise EditError('Нужны два прямоугольника')
            result['boxes']=[validate_box(b,page['size']) for b in operation['boxes']]
            result['method']='manual_preview_bounds'
        children=[]
        for index,(part,box) in enumerate(zip(parts,result['boxes'])):
            child=copy.deepcopy(node)
            child.update({'id':node['id'] if index==0 else f"{node['id']}.split{serial}",
                          'kind':'text','label':part.strip(),'raw':part.strip(),'bbox':box,'field_bbox':box,
                          'manual':True,'children':[],'split_from':node['id'],'needs_review':result['needs_review'],
                          'text_modified':True,
                          'split_method':result['method']})
            for stale in ('value','key','key_bbox','value_bbox','metric','unit','selector','value_type'):
                child.pop(stale,None)
            children.append(child)
        position=page['nodes'].index(node);page['nodes'][position:position+1]=children
        second=children[1]['id']
        for n in page['nodes']:
            if node['id'] in n['children']:
                position=n['children'].index(node['id']);n['children'].insert(position+1,second)
        for row in page['row_roots']:
            if node['id'] in row['children']:row['children'].insert(row['children'].index(node['id'])+1,second)
        if node['id'] in page['roots']:page['roots'].insert(page['roots'].index(node['id'])+1,second)
        node=children[0]
    node['manual']=True
    node['needs_review']=True
    refresh(page)
    return {'selected':node['id'],'affected_ids':ids,'page':page['number']}
