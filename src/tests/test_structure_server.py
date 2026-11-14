import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from structure_server import EditStore, make_handler
from test_structure_edits import fixture


class HTTPPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'example.viewer.model.json').write_text(json.dumps(fixture()))
        self.start()

    def start(self):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(EditStore(self.root)))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def tearDown(self):self.stop()

    def request(self,path,body=None,origin=None):
        headers={'Content-Type':'application/json'}
        if origin:headers['Origin']=origin
        request=Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urlopen(request,timeout=5) as response:return response.status,json.load(response)
        except HTTPError as error:
            try:return error.code,json.load(error)
            finally:error.close()

    def test_save_reload_export_and_server_restart(self):
        path='/api/documents/'+'a'*64
        status,preview=self.request(path+'/preview',{'revision':0,'page':1,'id':'a','parts':['организация','МФТИ']})
        self.assertEqual(status,200);self.assertEqual(preview['boxes'][1],[60,10,90,20])
        status,result=self.request(path+'/save',{'revision':0,'operation':{'action':'split','page':1,'ids':['a'],'parts':['организация','МФТИ']}})
        self.assertEqual(status,200);self.assertEqual(result['revision'],1)
        self.stop();self.start()
        status,loaded=self.request(path+'/export')
        self.assertEqual(status,200)
        self.assertEqual(loaded['model']['pages'][0]['nodes'][0]['children'],['a','a.split1','b'])
        status,_=self.request(path+'/save',{'revision':0,'operation':{'action':'undo'}})
        self.assertEqual(status,409)
        status,states=self.request('/api/edit-status')
        self.assertEqual(status,200);self.assertEqual(states[0]['operation_count'],1)

    def test_cross_origin_write_rejected(self):
        status,_=self.request('/api/documents/'+'a'*64+'/save',{'revision':0,'operation':{'action':'undo'}},origin='http://example.org')
        self.assertEqual(status,403)


if __name__=='__main__':unittest.main()
