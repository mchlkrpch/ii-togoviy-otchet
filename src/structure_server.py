"""Serve viewers and revisioned corrections on loopback; source PDFs stay read-only."""
import argparse
import copy
import hashlib
import json
import threading
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from structure_edits import EditError, apply_operation, split_boxes


class Conflict(EditError):
    pass


class EditStore:
    def __init__(self,root):
        self.root=Path(root).resolve();self.lock=threading.RLock()
        self.directory=self.root/'edits';self.directory.mkdir(exist_ok=True)

    def baseline(self,sha):
        if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise EditError('Неверный идентификатор документа')
        for path in self.root.rglob('*.model.json'):
            model=json.loads(path.read_text())
            if model.get('sha256')==sha:return model,path
        raise FileNotFoundError('Документ не найден. Пересоздайте просмотрщик.')

    def state(self,sha):
        baseline,path=self.baseline(sha)
        file=self.directory/f'{sha}.json'
        data=json.loads(file.read_text()) if file.exists() else {'sha256':sha,'revision':0,'operations':[],'events':[]}
        model=copy.deepcopy(baseline)
        for event in data['operations']:
            apply_operation(model,event['operation'],event['serial'])
        return data,model,path

    def response(self,data,model,selected=None):
        return {'revision':data['revision'],'model':model,'selected':selected,
                'operation_count':len(data['operations']),'events':data['events']}

    def get(self,sha):
        with self.lock:
            data,model,_=self.state(sha)
            return self.response(data,model)

    def preview(self,sha,body):
        with self.lock:
            data,model,_=self.state(sha)
            if body.get('revision')!=data['revision']:raise Conflict('Документ изменён в другом окне. Обновите правки.')
            page=next((p for p in model['pages'] if p['number']==body.get('page')),None)
            node=next((n for n in page['nodes'] if n['id']==body.get('id')),None) if page else None
            if node is None:raise EditError('Поле не найдено')
            return split_boxes(page,node,body.get('parts',[]))

    def save(self,sha,body):
        with self.lock:
            data,model,path=self.state(sha)
            if type(body.get('revision')) is not int or body['revision']!=data['revision']:
                raise Conflict('Документ изменён в другом окне. Обновите правки.')
            operation=body.get('operation')
            if not isinstance(operation,dict):raise EditError('Действие не задано')
            selected=None
            serial=data['revision']+1
            if operation.get('action')=='undo':
                if not data['operations']:raise EditError('Нет изменений для отмены')
                removed=data['operations'].pop()
                model,_=self.baseline(sha)
                for event in data['operations']:apply_operation(model,event['operation'],event['serial'])
                affected=removed['operation'].get('ids',[])
            else:
                result=apply_operation(model,operation,serial)
                selected=result['selected'];affected=result['affected_ids']
                data['operations'].append({'serial':serial,'operation':operation})
            data['revision']=serial
            data['events'].append({'revision':serial,'time':datetime.now(timezone.utc).isoformat(),
                                   'action':operation['action'],'affected_ids':affected})
            file=self.directory/f'{sha}.json'
            temporary=file.with_suffix('.tmp')
            temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(file)
            return self.response(data,model,selected)

    def statuses(self):
        result=[]
        with self.lock:
            for file in self.directory.glob('*.json'):
                data=json.loads(file.read_text())
                _,path=self.baseline(data['sha256'])
                result.append({'sha256':data['sha256'],'revision':data['revision'],
                               'operation_count':len(data['operations']),
                               'viewer':str(path.relative_to(self.root)).replace('.model.json','.html')})
        return result


def make_handler(store):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,directory=str(store.root),**kwargs)

        def json_response(self,status,data,download=False):
            encoded=json.dumps(data,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(encoded)))
            if download:self.send_header('Content-Disposition','attachment; filename="corrected-structure.json"')
            self.end_headers();self.wfile.write(encoded)

        def do_GET(self):
            path=urlsplit(self.path).path
            try:
                if path=='/api/edit-status':return self.json_response(200,store.statuses())
                if path.startswith('/api/documents/'):
                    sha=path.split('/')[3]
                    return self.json_response(200,store.get(sha),download=path.endswith('/export'))
                if path.endswith('.html') or path.endswith('.json'):
                    self.headers.replace_header('If-Modified-Since','Thu, 01 Jan 1970 00:00:00 GMT') if self.headers.get('If-Modified-Since') else None
                return super().do_GET()
            except FileNotFoundError as exc:self.json_response(404,{'error':str(exc)})
            except (EditError,ValueError,KeyError) as exc:self.json_response(400,{'error':str(exc)})

        def do_POST(self):
            path=urlsplit(self.path).path
            try:
                origin=self.headers.get('Origin')
                allowed_hosts={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
                if self.headers.get('Host') not in allowed_hosts:
                    return self.json_response(403,{'error':'Неверный адрес локального сервера'})
                if origin and origin!=f'http://{self.headers.get("Host")}':
                    return self.json_response(403,{'error':'Запись разрешена только из локального просмотрщика'})
                if not self.headers.get('Content-Type','').startswith('application/json'):
                    return self.json_response(415,{'error':'Ожидается application/json'})
                parts=path.split('/')
                if len(parts)!=5 or parts[:3]!=['','api','documents'] or parts[4] not in ('save','preview'):
                    return self.json_response(404,{'error':'Маршрут не найден'})
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<500000:return self.json_response(413,{'error':'Некорректный размер запроса'})
                body=json.loads(self.rfile.read(length))
                result=store.preview(parts[3],body) if parts[4]=='preview' else store.save(parts[3],body)
                self.json_response(200,result)
            except Conflict as exc:self.json_response(409,{'error':str(exc)})
            except FileNotFoundError as exc:self.json_response(404,{'error':str(exc)})
            except (EditError,ValueError,KeyError,TypeError) as exc:self.json_response(400,{'error':str(exc)})
    return Handler


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=Path('output/pdf'))
    parser.add_argument('--port',type=int,default=8878)
    args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),make_handler(EditStore(args.directory)))
    print(f'Editor: http://127.0.0.1:{args.port}/document-sets.html',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
