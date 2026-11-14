import copy
import json
import tempfile
import unittest
from pathlib import Path

from structure_edits import EditError, apply_operation, split_boxes
from structure_server import Conflict, EditStore


def fixture():
    def word(text,box):return {'text':text,'bbox':box}
    def node(id,text,box,parent=None,children=None,kind='text'):
        return {'id':id,'kind':kind,'label':text,'bbox':box,'field_bbox':box,'parent':parent,
                'children':children or [],'color':'#2563eb','needs_review':False}
    return {'name':'example.pdf','sha256':'a'*64,'pages':[{'number':1,'size':[200,200],
            'roots':['h'],'row_roots':[{'label':'2026','children':['a','b']}],
            'words':[word('организация',[10,10,50,20]),word('МФТИ',[60,10,90,20]),word('значение',[100,10,150,20])],
            'nodes':[node('h','Заголовок',[10,0,150,8],children=['a','b'],kind='header'),
                     node('a','организация МФТИ',[10,10,90,20],'h'),node('b','значение',[100,10,150,20],'h')]}]}


class EditGeometryTests(unittest.TestCase):
    def test_split_uses_two_actual_word_rectangles(self):
        model=fixture();page=model['pages'][0];node=page['nodes'][1]
        result=split_boxes(page,node,['организация','МФТИ'])
        self.assertEqual(result['boxes'],[[10,10,50,20],[60,10,90,20]])
        self.assertEqual(result['method'],'source_word_boxes')
        self.assertFalse(result['needs_review'])

    def test_corrected_character_keeps_whole_word_and_inside_word_is_approximate(self):
        page=fixture()['pages'][0];node=page['nodes'][1]
        corrected=split_boxes(page,node,['организация','МФТН'])
        self.assertEqual(corrected['boxes'][1],[60,10,90,20])
        partial=split_boxes(page,node,['орга','низация МФТИ'])
        self.assertEqual(partial['method'],'character_alignment')
        self.assertTrue(partial['needs_review'])

    def test_unalignable_text_has_explicit_fallback(self):
        page=fixture()['pages'][0]
        result=split_boxes(page,page['nodes'][1],['zzz','xxx'])
        self.assertEqual(result['method'],'estimated_rectangle')
        self.assertTrue(result['needs_review'])

    def test_merge_replaces_two_leaves_and_split_inserts_two_into_tree(self):
        model=fixture()
        apply_operation(model,{'page':1,'ids':['a','b'],'action':'merge'},1)
        page=model['pages'][0];nodes={n['id']:n for n in page['nodes']}
        self.assertNotIn('b',nodes)
        self.assertEqual(nodes['a']['bbox'],[10,10,150,20])
        self.assertEqual(nodes['h']['children'],['a'])
        self.assertEqual(page['aliases']['b'],'a')
        apply_operation(model,{'page':1,'ids':['a'],'action':'split','parts':['организация','МФТИ значение']},2)
        nodes={n['id']:n for n in page['nodes']}
        self.assertEqual(nodes['h']['children'],['a','a.split2'])
        self.assertEqual(page['row_roots'][0]['children'],['a','a.split2'])

    def test_text_edit_updates_descendant_paths_and_bounds_are_validated(self):
        model=fixture()
        model['pages'][0]['nodes'][1]['column_header_node_ids']=['h']
        apply_operation(model,{'page':1,'ids':['h'],'action':'update','text':'Новый заголовок'},1)
        self.assertEqual(model['pages'][0]['nodes'][1]['column_path'],['Новый заголовок'])
        apply_operation(model,{'page':1,'ids':['h'],'action':'update','bbox':[15,0,155,8]},2)
        self.assertEqual(model['pages'][0]['nodes'][1]['column_header_bboxes'],[[15,0,155,8]])
        apply_operation(model,{'page':1,'ids':['a'],'action':'update','bbox':[12,12,95,22]},2)
        self.assertEqual(model['pages'][0]['nodes'][1]['field_bbox'],[12,12,95,22])
        with self.assertRaises(EditError):
            apply_operation(model,{'page':1,'ids':['a'],'action':'update','bbox':[-1,0,10,10]},3)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.base=fixture()
        (self.root/'example.viewer.model.json').write_text(json.dumps(self.base))
        self.store=EditStore(self.root)

    def test_edits_survive_store_restart_and_undo_preserves_history(self):
        response=self.store.save('a'*64,{'revision':0,'operation':{'page':1,'ids':['a'],'action':'update','text':'Исправленная организация'}})
        self.assertEqual(response['revision'],1)
        restarted=EditStore(self.root)
        loaded=restarted.get('a'*64)
        self.assertEqual(loaded['model']['pages'][0]['nodes'][1]['label'],'Исправленная организация')
        undone=restarted.save('a'*64,{'revision':1,'operation':{'action':'undo'}})
        self.assertEqual(undone['model'],self.base)
        self.assertEqual(undone['revision'],2)
        self.assertEqual(len(undone['events']),2)
        self.assertEqual(json.loads((self.root/'example.viewer.model.json').read_text()),self.base)

    def test_concurrent_revision_and_invalid_operation_do_not_overwrite(self):
        op={'page':1,'ids':['a'],'action':'split','parts':['организация','МФТИ']}
        self.store.save('a'*64,{'revision':0,'operation':op})
        with self.assertRaises(Conflict):self.store.save('a'*64,{'revision':0,'operation':op})
        with self.assertRaises(EditError):self.store.save('a'*64,{'revision':1,'operation':{'page':1,'ids':['a'],'action':'update','bbox':[0,0,999,999]}})
        self.assertEqual(self.store.get('a'*64)['revision'],1)


if __name__=='__main__':unittest.main()
