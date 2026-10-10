"""Generation wall time and monotonic active-session accounting, including resume."""
from datetime import datetime,timezone
import time
from ..io import read_json,write_json


class GenerationTimer:
    def __init__(self,path, *, resume=False,started_at=None,started_monotonic=None):
        self.path=path
        self.start=started_at or datetime.now(timezone.utc)
        self.monotonic=time.monotonic() if started_monotonic is None else started_monotonic
        self.data=read_json(path) if resume and path.exists() else {
            'schema_version':'generation-timing-v1','started_at_utc':self.start.isoformat(),'sessions':[]}
        for session in self.data['sessions']:
            if session.get('status')=='running':
                session['status']='interrupted';session['termination_time_known']=False
        self.prior=sum(s.get('elapsed_s',0.) for s in self.data['sessions'])
        self.session={'started_at_utc':self.start.isoformat(),'ended_at_utc':None,'status':'running','elapsed_s':0.}
        self.data['sessions'].append(self.session)
        self.update()

    def update(self,status='running', *, final=False):
        now=datetime.now(timezone.utc)
        self.session.update(elapsed_s=max(0.,time.monotonic()-self.monotonic),status=status,
                            ended_at_utc=now.isoformat() if final else None)
        compact={'started_at_utc':self.data['started_at_utc'],'ended_at_utc':now.isoformat() if final else None,
                 'elapsed_wall_s':max(0.,(now-datetime.fromisoformat(self.data['started_at_utc'])).total_seconds()),
                 'elapsed_active_s':self.prior+self.session['elapsed_s'],'status':status,
                 'updated_at_utc':now.isoformat(),'session_count':len(self.data['sessions'])}
        self.data.update(compact,wall_time_scope='includes GPU waits, setup and pauses between resumes',
                         active_time_scope='sum of monotonic process-session elapsed times; not sum of parallel workers')
        write_json(self.path,self.data)
        return compact
