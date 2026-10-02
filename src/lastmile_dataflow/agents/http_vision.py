"""Optional read-only multimodal review via .env; no credentials in output."""
import base64
import json
from pathlib import Path
import re
import time
import urllib.request
import urllib.parse
from ..io import read_json,write_json,file_digest,digest


def read_settings(path):
    allowed={'LLM_API_KEY','LLM_BASE_URL','LLM_MODEL'}; values={}
    for line in Path(path).read_text().splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        name,value=line.split('=',1); name=name.strip().removeprefix('export ')
        if name in allowed: values[name]=value.strip().strip('\"\'')
    if set(values)!=allowed or not all(values.values()): raise ValueError('missing LLM settings')
    url=urllib.parse.urlsplit(values['LLM_BASE_URL'])
    if url.scheme not in ('https','http') or url.scheme=='http' and url.hostname not in ('localhost','127.0.0.1') or url.username or url.password or url.query or url.fragment:
        raise ValueError('unsafe LLM endpoint; use HTTPS without embedded credentials')
    return values


def parse_review(raw,observation_id):
    try: response=json.loads(raw)
    except (ValueError,TypeError) as exc: raise ValueError('invalid_review_json') from exc
    if not isinstance(response,dict) or set(response)!={'observation_id','verdict','reason'}: raise ValueError('invalid_review_schema')
    if response['observation_id']!=observation_id: raise ValueError('stale_review')
    if response['verdict'] not in ('pass','fail','unknown') or not isinstance(response['reason'],str) or not response['reason'] or len(response['reason'])>4000: raise ValueError('invalid_review_verdict')
    return response


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise RuntimeError('credential-bearing API redirects prohibited')


def review_build(build,env_path,*,timeout_s=60,max_images=3):
    build=Path(build); candidate=read_json(build/'task_candidate.json')
    if read_json(build/'result.json')['status']!='candidate_ready': raise ValueError('review requires physically valid settled candidate')
    packets=[read_json(p) for p in sorted((build/'observations').glob('*/observation.json'))]
    packet=next((p for p in reversed(packets) if p['stage']=='settled' and p['images']),None)
    if not packet: raise ValueError('settled_images_unavailable')
    paths={entry['view']:Path(entry['path']) for entry in packet['images']}
    wanted=['diagnostic_top','diagnostic_target','robot_head'][:max_images]
    facts={'observation_id':packet['observation_id'],'case_type':candidate['case_type'],'target':candidate['target'],
           'build_requirements':candidate['requirements'],'regions':packet['regions'],
           'target_state':packet['state']['target'],'robot_base':packet['state']['robot']['base'],
           'hard_checks':'passed program placement checks; cannot override them',
           'operation_result':'unknown; no physical task success claim permitted','views':'diagnostic images are not VLA inputs'}
    image_refs=[{'view':v,'path':str(paths[v]),'sha256':file_digest(paths[v])} for v in wanted if v in paths]
    review_dir=build.parent.parent/'agent_reviews'/('review-'+digest({'id':packet['observation_id'],'time':time.time_ns()})[:12])
    review_dir.mkdir(parents=True,exist_ok=False)
    write_json(review_dir/'request.json',{'facts':facts,'images':image_refs,'authorization':'user requested external Agent API for this phase','role':'semantic_advice_only'})
    settings=read_settings(env_path); base=settings['LLM_BASE_URL'].rstrip('/')
    endpoint=base if base.endswith('/chat/completions') else base+'/chat/completions'
    content=[{'type':'text','text':json.dumps(facts,ensure_ascii=False)}]
    for ref in image_refs:
        data=base64.b64encode(Path(ref['path']).read_bytes()).decode()
        content.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+data}})
    system=('You are a read-only scene semantic reviewer, not a robot executor. Review only the settled images and facts. '
            'Do not infer grasp success, reachability or navigation from images. The diagnostic views are not robot input. '
            'Return exactly JSON with observation_id (copied), verdict (pass/fail/unknown for visible semantic layout ONLY), '
            'reason (short Chinese explanation). No code, coordinates, task_success or permission fields. Use unknown if unclear.')
    body={'model':settings['LLM_MODEL'],'messages':[{'role':'system','content':system},{'role':'user','content':content}],
          'max_tokens':700,'temperature':0}
    req=urllib.request.Request(endpoint,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+settings['LLM_API_KEY']})
    start=time.monotonic(); record={'source':'external_model','role':'semantic_advice_only','observation_id':packet['observation_id'],
                                  'model':settings['LLM_MODEL'],'endpoint_host':urllib.parse.urlsplit(endpoint).hostname,'image_count':len(image_refs)}
    try:
        with urllib.request.build_opener(RejectRedirects()).open(req,timeout=timeout_s) as response: payload=json.load(response)
        raw=payload['choices'][0]['message']['content']
        record.update(status='accepted',response=parse_review(raw,packet['observation_id']),usage=payload.get('usage',{}))
    except Exception as exc:
        # Do not log exception repr/HTTP body which might contain headers or credentials.
        record.update(status='rejected',error_type=type(exc).__name__,http_status=getattr(exc,'code',None),reason='request_or_strict_protocol_failed')
    record['wall_time_s']=time.monotonic()-start
    write_json(review_dir/'response.json',record)
    return review_dir
