import copy
import io
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

import pymupdf
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from backend.edited_files import edited_file, pdf_fields
from backend.engine import calculate, validate_change
from backend.extraction import extract_document, add_fact
from backend.original import workbook_layout
from test_verification import example_case


class WorkspaceTests(unittest.TestCase):
    def scope(self, document, category):
        evidence={"text":"24/03/15-01", "locator":"Реквизиты", "confidence":100}
        for key,value in (("contract","24/03/15-01"),("date","2026-01-15"),("period","2026-01")):
            add_fact(document,key,key,value,evidence,'text')
        document['category']=category
        return document

    def test_multiple_reports_share_sources_without_colliding_fields(self):
        case=example_case()
        second=copy.deepcopy(case['documents'][0]);second.update(id='report2',name='report2.pdf',hash='second')
        case['documents'].append(second)
        result=calculate(case)
        self.assertEqual(result['report_ids'],['report','report2'])
        first=result['nodes']['report::r2026_c15'];other=result['nodes']['report2::r2026_c15']
        self.assertEqual(first['expected'],other['expected'])
        self.assertEqual(first['inputs'][0]['ref'],other['inputs'][0]['ref'])
        changed=validate_change(case,'reported','report2::r2026_c15',{'value':'25'})
        nodes=calculate(changed)['nodes']
        self.assertEqual(nodes['report2::r2026_c15']['reported'],'25.00')
        self.assertEqual(nodes['report::r2026_c15']['reported'],'20.00')
        changed=validate_change(case,'fact','production:amount',{'value':'25'})
        nodes=calculate(changed)['nodes']
        self.assertEqual(nodes['report::r2026_c15']['expected'],'25.00')
        self.assertEqual(nodes['report2::r2026_c15']['expected'],'25.00')

    def test_xlsx_field_edit_recalculates_and_exports_formulas_with_readable_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'payroll.xlsx'
            workbook=Workbook();sheet=workbook.active;sheet.title='Ведомость'
            sheet['A6']='Основная зарплата';sheet['A7']='Доплаты';sheet['A8']='ИТОГО'
            sheet['C6']=100;sheet['C7']=10;sheet['C8']='=SUM(C6:C7)';sheet['C6'].font=Font(color='FFFFFF')
            workbook.save(path);workbook.close();original=path.read_bytes()
            document=self.scope(extract_document(path),'0200')
            add_fact(document,'amount','Итого','110',document['facts']['Ведомость!C8']['evidence'],dependencies=[document['id']+':Ведомость!C8'])
            case=example_case();case['documents'][1]=document
            candidate=validate_change(case,'cell',document['id']+':Ведомость!C6',{'value':'101'})
            result=calculate(candidate)
            self.assertEqual(result['nodes']['r2026_c5']['expected'],'111.00')
            candidate=validate_change(candidate,'cell',document['id']+':Ведомость!C8',{'value':'=C6*2'})
            result=calculate(candidate)
            self.assertEqual(result['nodes']['r2026_c5']['expected'],'202.00')
            effective=next(item for item in result['documents'] if item['id']==document['id'])
            layout=workbook_layout(path,cells=effective['cells'])
            cell=next(cell for row in layout['rows'] for cell in row['cells'] if cell['address']=='C6')
            self.assertEqual(cell['style']['color'],'#111111')
            data,_,_=edited_file(path,effective,document)
            exported=load_workbook(io.BytesIO(data),data_only=False)
            self.assertEqual(exported.active['C8'].value,'=C6*2');self.assertEqual(exported.active['C6'].value,101)
            exported.close();self.assertEqual(path.read_bytes(),original)
            with self.assertRaises(ValueError):validate_change(case,'cell',document['id']+':Ведомость!C8',{'value':'=WEBSERVICE("http://invalid")'})

    def test_word_field_edits_recompute_source_and_export_without_changing_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'act.docx'
            xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Стоимость услуг: 20,00 руб.</w:t></w:r></w:p></w:body></w:document>'
            with ZipFile(path,'w') as archive:archive.writestr('word/document.xml',xml)
            document=self.scope(extract_document(path),'0800')
            case=example_case();case['documents'][3]=document
            candidate=validate_change(case,'text',document['id']+':Абзац 1',{'value':'Стоимость услуг: 30,00 руб.'})
            result=calculate(candidate)
            self.assertEqual(result['nodes']['r2026_c15']['expected'],'30.00')
            effective=next(doc for doc in result['documents'] if doc['id']==document['id'])
            data,_,_=edited_file(path,effective,document)
            with ZipFile(io.BytesIO(data)) as archive:self.assertIn('30,00',archive.read('word/document.xml').decode())
            with ZipFile(path) as archive:self.assertIn('20,00',archive.read('word/document.xml').decode())

    def test_pdf_form_field_recomputes_and_stays_interactive_in_download(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'form.pdf'
            pdf=pymupdf.open();page=pdf.new_page();page.insert_text((40,100),'Service act and source accounting document')
            widget=pymupdf.Widget();widget.field_name='amount';widget.field_type=pymupdf.PDF_WIDGET_TYPE_TEXT;widget.rect=pymupdf.Rect(40,40,180,64);widget.field_value='20.00';page.add_widget(widget);pdf.save(path);pdf.close()
            document=self.scope(extract_document(path),'0800')
            self.assertEqual(document.get('errors'),[])
            form=next(fact for fact in document['facts'].values() if fact['key'].startswith('pdf_form_') and fact['evidence'].get('pdf_field')=='amount')
            add_fact(document,'amount','Сумма','20',form['evidence'],dependencies=[form['id']])
            case=example_case();case['documents'][3]=document
            candidate=validate_change(case,'pdf_field',document['id']+':amount',{'value':'30.00'})
            result=calculate(candidate);self.assertEqual(result['nodes']['r2026_c15']['expected'],'30.00')
            effective=next(doc for doc in result['documents'] if doc['id']==document['id'])
            data,_,_=edited_file(path,effective,document)
            with pymupdf.open(stream=data,filetype='pdf') as exported:
                page=exported[0];widget=next(page.widgets())
                self.assertEqual(widget.field_value,'30.00')
                self.assertEqual(exported.xref_get_key(widget.xref,'V')[1],'30.00')
                self.assertIn(str(widget.xref),exported.xref_get_key(exported.pdf_catalog(),'AcroForm/Fields')[1])
            self.assertEqual(pdf_fields(path)[0]['value'],'20.00')

    def test_numeric_pdf_edit_exports_the_new_number_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'act.pdf'
            pdf=pymupdf.open();page=pdf.new_page();page.insert_text((40,80),'Service accounting document cost: 20.00 RUB');pdf.save(path);pdf.close()
            document=self.scope(extract_document(path),'0800')
            case=example_case();case['documents'][3]=document
            candidate=validate_change(case,'fact',document['id']+':amount',{'value':'30.00'})
            effective=next(doc for doc in calculate(candidate)['documents'] if doc['id']==document['id'])
            data,_,_=edited_file(path,effective,document)
            with pymupdf.open(stream=data,filetype='pdf') as exported:
                self.assertIn('30.00',exported[0].get_text());self.assertNotIn('20.00',exported[0].get_text())
            with pymupdf.open(path) as original:self.assertIn('20.00',original[0].get_text())
