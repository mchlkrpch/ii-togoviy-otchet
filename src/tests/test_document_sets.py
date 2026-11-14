import copy
import json
import unittest
from decimal import Decimal
from pathlib import Path

import pymupdf
from batch_document_sets import calculate_bound, load_sets, match_reference
from native_structure import parse_native_page
from structure_semantics import enrich_document

ROOT=Path(__file__).resolve().parents[2]
MANIFEST=ROOT/'output/pdf/document-sets.manifest.json'


class BoundFormulaTests(unittest.TestCase):
    def test_changed_pdf_operand_changes_calculation(self):
        self.assertEqual(calculate_bound('=7116+593+60+10',['7116','593','60','10'],['7117','593','60','10']),'7780')

    def test_rounding_is_decimal_and_uses_bound_operands(self):
        self.assertEqual(calculate_bound('=ROUND((528+375+1085+347+308+575)/1000,1)',
                         ['528','375','1085','347','308','575'],['528','375','1085','347','308','575']),'3.2')

    def test_executable_and_undeclared_syntax_rejected(self):
        with self.assertRaises(ValueError):
            calculate_bound('__import__("os").getcwd()',[],[])
        with self.assertRaises(ValueError):
            calculate_bound('=10+20',['99'],['1'])


@unittest.skipUnless(MANIFEST.exists(),'Run batch_document_sets.py first')
class DocumentSetIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest=json.loads(MANIFEST.read_text())
        cls.sets={item['id']:(folder,item) for folder,item in load_sets(ROOT/'document_sets')}

    def test_all_sixteen_sets_have_all_dependencies(self):
        reports=self.manifest['sets']
        self.assertEqual([r['id'] for r in reports],[f'S{i:02d}' for i in range(1,17)])
        self.assertEqual(self.manifest['summary']['documents'],6)
        self.assertEqual(self.manifest['summary']['pages'],83)
        for report in reports:
            self.assertTrue(report['arithmetic_matches'],report['id'])
            self.assertTrue(all(m['status']=='matched' and m['candidate_count']==1 for m in report['matches']),report['id'])
            self.assertEqual(Decimal(report['calculated']),Decimal(report['expected']))
            for m in report['matches']:
                self.assertTrue((ROOT/'output/pdf'/m['url'].split('#')[0]).exists())

    def test_narrative_value_lookup_is_not_claimed_as_semantic_verification(self):
        self.assertEqual(self.manifest['summary']['verified_values'],6)
        self.assertEqual(self.manifest['summary']['verified'],10)
        for report in self.manifest['sets'][:6]:
            self.assertEqual(report['matches'][0]['method'],'unique_value_on_declared_page')

    def test_native_budget_cell_reparsed_and_mutation_is_detected(self):
        folder,item=self.sets['S07']
        source=folder/item['первичные_документы'][0]['локальный_файл']
        with pymupdf.open(source) as pdf:
            page=parse_native_page(pdf[4],5)
        document=enrich_document({'pages':[page]})
        reference=item['исходные_ячейки'][0]
        match=match_reference(document,reference)
        self.assertEqual(match['method'],'row_code_and_column')
        self.assertEqual(Decimal(match['actual']),Decimal('7116'))
        changed=copy.deepcopy(document)
        next(f for f in changed['facts'] if f['id']==match['fact_id'])['value']='7117'
        mutation=match_reference(changed,reference)
        self.assertEqual(mutation['status'],'mismatch')
        self.assertEqual(mutation['actual'],'7117')

    def test_borderless_expense_is_signed_and_normalization_recorded(self):
        folder,item=self.sets['S13']
        source=folder/item['итоговый_документ']['локальный_файл']
        with pymupdf.open(source) as pdf:
            page=parse_native_page(pdf[10],11)
        document=enrich_document({'pages':[page]})
        match=match_reference(document,item['итоговая_ячейка'])
        self.assertEqual(match['source_value'],'-15424')
        self.assertEqual(match['actual'],'15424')
        self.assertIn('absolute_accounting',match['normalization'])


if __name__=='__main__':
    unittest.main()
