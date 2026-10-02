"""Cheap SQLite discovery; parsed is not loaded, metadata parent is not support proof."""
from pathlib import Path
import sqlite3
import os
import xml.etree.ElementTree as ET
from ..io import read_json, write_json, file_digest, digest


def dependencies(xml_path, seen=None, *, verify_resources=False):
    path = Path(os.path.abspath(xml_path)); seen = set() if seen is None else seen
    if path in seen: return []
    seen.add(path)
    root = ET.parse(path).getroot()
    compiler = root.find('compiler')
    result = [{'path':str(path),'exists':True,'sha256':file_digest(path),'kind':'mjcf'}]
    for node in root.iter():
        name = node.get('file')
        if not name: continue
        directory = ''
        if compiler is not None and node.tag != 'include':
            directory = compiler.get('assetdir','') or compiler.get('meshdir' if node.tag=='mesh' else 'texturedir','')
        dep = Path(os.path.abspath(path.parent/directory/name))
        if node.tag == 'include' and dep.is_file(): result.extend(dependencies(dep,seen,verify_resources=verify_resources))
        else:
            if dep in seen: continue
            seen.add(dep)
            # Coarse discovery must not read/hash thousands of mesh files on remote NAS.
            # Actual compilation supplies loading evidence; per-resource verification is opt-in.
            exists=dep.is_file() if verify_resources or node.tag=='include' else None
            result.append({'path':str(dep),'exists':exists,'sha256':file_digest(dep) if exists else None,
                           'kind':node.tag,'resolution':'verified' if exists else 'missing' if exists is False else 'not_checked'})
    return sorted(result,key=lambda x:(x['path'],x['kind']))


def create_index(sources, output):
    output = Path(output); output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists() or output.with_suffix('.json').exists(): raise FileExistsError(output)
    records=[]
    with sqlite3.connect(output) as db:
        db.executescript('CREATE TABLE scenes(scene_id TEXT PRIMARY KEY, xml_path TEXT, metadata_path TEXT, status TEXT, digest TEXT);'
                         'CREATE TABLE objects(scene_id TEXT, instance_id TEXT, category TEXT, asset_id TEXT, parent_id TEXT, is_static INTEGER, room_id TEXT, PRIMARY KEY(scene_id,instance_id));')
        for source in sources:
            record={'source':source.__dict__,'status':'discovered','objects':[]}
            try:
                deps=dependencies(source.xml_path)
                meta=read_json(source.metadata_path).get('objects',{}) if source.metadata_path else {}
                record.update(status='parsed' if all(x['exists'] is not False for x in deps) else 'missing_resources', dependencies=deps, load_qualification='unknown', resource_verification='references_only')
                tree=ET.parse(source.xml_path)
                # Generic fixtures without metadata still searchable without guessed categories.
                if not meta:
                    for b in tree.findall('./worldbody/body'):
                        meta[b.get('name')]={'is_static': b.find('freejoint') is None and b.find('joint') is None}
                for name,obj in sorted(meta.items()):
                    entry={'instance_id':name,'category':obj.get('category'),'asset_id':obj.get('asset_id'),
                           'parent_hint':obj.get('parent'),'is_static':obj.get('is_static'),'room_id':obj.get('room_id'),
                           'joints':obj.get('name_map',{}).get('joints',{}),'grasp_reference':obj.get('grasp_path'),
                           'grasp_qualification':'unknown','support_verified':False}
                    record['objects'].append(entry)
                    db.execute('INSERT INTO objects VALUES(?,?,?,?,?,?,?)',(source.scene_id,name,entry['category'],entry['asset_id'],entry['parent_hint'],entry['is_static'],str(entry['room_id']) if entry['room_id'] is not None else None))
            except (OSError, ValueError, ET.ParseError) as exc:
                record.update(status='parse_error',error=str(exc))
            record['record_id']=digest(record)
            db.execute('INSERT INTO scenes VALUES(?,?,?,?,?)',(source.scene_id,source.xml_path,source.metadata_path,record['status'],record['record_id']))
            records.append(record)
    write_json(output.with_suffix('.json'),{'schema_version':'2.0','records':records})
    return records


def search_index(path, *, category=None, asset_id=None, dynamic=None, limit=20):
    if type(limit) is not int or limit <= 0: raise ValueError('invalid query limit')
    where=["s.status='parsed'"]; args=[]
    for key,value in [('o.category',category),('o.asset_id',asset_id),('o.is_static',None if dynamic is None else int(not dynamic))]:
        if value is not None: where.append(f'{key}=?'); args.append(value)
    with sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        return [dict(x) for x in db.execute('SELECT s.scene_id,s.xml_path,s.metadata_path,o.* FROM scenes s JOIN objects o USING(scene_id) WHERE '+' AND '.join(where)+' ORDER BY o.is_static ASC,s.scene_id,o.instance_id LIMIT ?',[*args,limit])]
