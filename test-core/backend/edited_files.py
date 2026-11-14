import io
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pymupdf
from openpyxl import load_workbook
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .money import decimal_value
from .numeric_regions import numeric_regions

def pdf_fields(path):
    fields=[]
    with pymupdf.open(path) as pdf:
        for page in pdf:
            for widget in page.widgets() or []:
                rect=widget.rect
                fields.append({"name":widget.field_name,"value":str(widget.field_value) if widget.field_value is not None else "","type":widget.field_type_string,
                               "page":page.number+1,"rect":[rect.x0/page.rect.width,rect.y0/page.rect.height,rect.x1/page.rect.width,rect.y1/page.rect.height]})
    return fields

def edited_file(path, document, original_document):
    path=Path(path)
    if document["format"]=="xlsx":
        if document.get("workbook_snapshot"):
            from .spreadsheet_editor import snapshot_bytes, workbook_snapshot
            return snapshot_bytes(workbook_snapshot(path,document)),"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet","xlsx"
        workbook=load_workbook(path)
        try:
            for cell in document["cells"].values():
                raw=cell["raw"]
                if str(workbook[cell["sheet"]][cell["address"]].value)==raw:continue
                try:value=decimal_value(raw) if not raw.startswith("=") else raw
                except ValueError:value=raw
                workbook[cell["sheet"]][cell["address"]]=value
            for field in document["report_fields"]:
                evidence=field["evidence"]
                if field.get("reported_override") and evidence.get("cell"):
                    workbook[evidence["sheet"]][evidence["cell"]]=decimal_value(field["value"])
            buffer=io.BytesIO();workbook.save(buffer);return buffer.getvalue(),"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet","xlsx"
        finally:workbook.close()
    if document["format"]=="docx":
        if document.get("rich_document_snapshot"):
            from .rich_document import docx_bytes, snapshot_from_docx
            return docx_bytes(path, snapshot_from_docx(path, document)), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
        namespace={"w":"http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        edits={block["locator"]:block["text"] for block in document["blocks"] if block.get("manual")}
        with ZipFile(path) as archive:
            root=ET.fromstring(archive.read("word/document.xml"));body=root.find("w:body",namespace)
            paragraph=table=0
            def replace(element,text):
                nodes=element.findall('.//w:t',namespace)
                if nodes:nodes[0].text=text;[setattr(node,'text','') for node in nodes[1:]]
                else:
                    paragraph=element if element.tag.endswith('}p') else element.find('w:p',namespace)
                    if paragraph is None:paragraph=ET.SubElement(element,'{'+namespace['w']+'}p')
                    run=ET.SubElement(paragraph,'{'+namespace['w']+'}r');ET.SubElement(run,'{'+namespace['w']+'}t').text=text
            for element in body:
                if element.tag.endswith('}p'):
                    paragraph+=1;key=f"Абзац {paragraph}"
                    if key in edits:replace(element,edits[key])
                elif element.tag.endswith('}tbl'):
                    table+=1
                    for index,row in enumerate(element.findall('w:tr',namespace),1):
                        key=f"Таблица {table}, строка {index}"
                        if key in edits:
                            for cell,text in zip(row.findall('w:tc',namespace),edits[key].split(' | ')):replace(cell,text)
            buffer=io.BytesIO()
            with ZipFile(buffer,'w') as output:
                for entry in archive.infolist():output.writestr(entry,ET.tostring(root,encoding='utf-8',xml_declaration=True) if entry.filename=='word/document.xml' else archive.read(entry.filename))
            return buffer.getvalue(),"application/vnd.openxmlformats-officedocument.wordprocessingml.document","docx"
    if document["format"]=="txt":
        return '\n'.join(block['text'] for block in document['blocks']).encode('utf-8'),"text/plain; charset=utf-8","txt"
    regions=numeric_regions(original_document,path)
    patches=[]
    for fact in document["facts"].values():
        if fact.get('manual') and fact.get('original_value') is not None and fact.get('value') is not None and fact['kind']=='money':
            region=next((region for region in regions if region['evidence']==fact['evidence']),None)
            rect=region['rect'] if region else fact['evidence'].get('rect') if fact['evidence'].get('method')=='manual-selection' else None
            if rect:patches.append((fact['evidence'].get('page',1),rect,str(fact['value'])))
    for field in document['report_fields']:
        if field.get('manual') and field['evidence'].get('rect') and field.get('value') is not None:
            patches.append((field['evidence'].get('page',1),field['evidence']['rect'],str(field['value'])))
    if document['format']=='pdf':
        with pymupdf.open(path) as pdf:
            for field in document.get('pdf_fields',[]):
                if field.get('manual'):
                    page=pdf[field['page']-1]
                    for widget in page.widgets() or []:
                        if widget.field_name==field['name']:widget.field_value=field['value'];widget.update()
            for number,normalized,text in patches:
                page=pdf[number-1];rect=pymupdf.Rect(normalized[0]*page.rect.width,normalized[1]*page.rect.height,normalized[2]*page.rect.width,normalized[3]*page.rect.height)
                page.add_redact_annot(rect,fill=(1,1,1));page.apply_redactions(images=0,graphics=0)
                size=max(4,min(12,rect.height*.7))
                inserted=False
                while size>=3:
                    if page.insert_textbox(rect+(-1,-1,1,3),text,fontsize=size,fontname='helv',align=2)>=0:inserted=True;break
                    size-=.5
                if not inserted:raise ValueError("Число не помещается в поле PDF. Уточните область перед выгрузкой.")
            return pdf.tobytes(garbage=4,deflate=True),'application/pdf','pdf'
    with Image.open(path) as original:
        image=ImageOps.exif_transpose(original).convert('RGB')
        draw=ImageDraw.Draw(image)
        for _,rect,text in patches:
            box=[round(rect[0]*image.width),round(rect[1]*image.height),round(rect[2]*image.width),round(rect[3]*image.height)]
            draw.rectangle(box,fill='white')
            size=max(8,box[3]-box[1])
            font=ImageFont.load_default(size=size)
            while draw.textlength(text,font=font)>max(1,box[2]-box[0]) and size>5:size-=1;font=ImageFont.load_default(size=size)
            draw.text((box[0],box[1]),text,font=font,fill='black')
        buffer=io.BytesIO();image.save(buffer,format='PNG');return buffer.getvalue(),'image/png','png'
