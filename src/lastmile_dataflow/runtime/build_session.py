"""Exclusive construction sandbox. Never changes Simulation.started or calls begin()."""
from dataclasses import asdict
from pathlib import Path
import hashlib
import time

import mujoco
import numpy as np

from ..catalog.assets import AssetPool
from ..catalog.index import dependencies
from ..io import digest, file_digest, write_json, read_json
from ..scenes.geometry import extract_regions, descendants
from ..scenes.initialization import initialize_robot
from ..scenes.source import instance_catalog
from ..validation.placement import inspect_build, poses
from .simulation import Simulation


def model_identity(sim):
    buffer = np.empty(mujoco.mj_sizeModel(sim.model),dtype=np.uint8)
    mujoco.mj_saveModel(sim.model,None,buffer)
    return digest({'compiled_model':hashlib.sha256(buffer.tobytes()).hexdigest(), 'instances':sim.catalog,
                   'robot':asdict(sim.robot.config),'mujoco':mujoco.__version__})


def checkpoint_identity(sim, model_id):
    return digest({'model_id':model_id,'state_spec':int(sim.state_spec),'state':sim.state_vector().tolist(),
                   'fixed_head':sim.robot.fixed_head.tolist(),'clock_remainder':sim._clock_remainder})


def migrate_state(old, new):
    """Named joints/actuators only; never copy old topology-sized vectors blindly."""
    if old.started or new.started or old.closed or new.closed: raise RuntimeError('state migration requires construction/preparation sessions')
    m0,m1,d0,d1=old.model,new.model,old.data,new.data
    if len(d0.plugin_state) or len(d1.plugin_state): raise ValueError('plugin state migration is unsupported')
    for j in range(m1.njnt):
        name=m1.joint(j).name
        try: k=m0.joint(name).id
        except KeyError: continue
        if m1.jnt_type[j] != m0.jnt_type[k]: raise ValueError('joint type changed during migration')
        nq,nv={0:(7,6),1:(4,3),2:(1,1),3:(1,1)}[int(m1.jnt_type[j])]
        a,b=int(m1.jnt_qposadr[j]),int(m0.jnt_qposadr[k]); d1.qpos[a:a+nq]=d0.qpos[b:b+nq]
        a,b=int(m1.jnt_dofadr[j]),int(m0.jnt_dofadr[k]); d1.qvel[a:a+nv]=d0.qvel[b:b+nv]; d1.qacc_warmstart[a:a+nv]=d0.qacc_warmstart[b:b+nv]
        d1.qfrc_applied[a:a+nv]=d0.qfrc_applied[b:b+nv]
    for a in range(m1.nu):
        try: k=m0.actuator(m1.actuator(a).name).id
        except KeyError: continue
        d1.ctrl[a]=d0.ctrl[k]
        n=int(m1.actuator_actnum[a])
        if n != int(m0.actuator_actnum[k]): raise ValueError('actuator state shape changed')
        if n: d1.act[m1.actuator_actadr[a]:m1.actuator_actadr[a]+n]=d0.act[m0.actuator_actadr[k]:m0.actuator_actadr[k]+n]
    for b in range(m1.nbody):
        name=m1.body(b).name
        try: k=m0.body(name).id
        except KeyError: continue
        d1.xfrc_applied[b]=d0.xfrc_applied[k]
        a,c=int(m1.body_mocapid[b]),int(m0.body_mocapid[k])
        if a>=0 and c>=0: d1.mocap_pos[a]=d0.mocap_pos[c]; d1.mocap_quat[a]=d0.mocap_quat[c]
    for e in range(m0.neq):
        name=m0.equality(e).name
        if not name:
            if d0.eq_active[e] != m0.eq_active0[e]: raise ValueError('cannot migrate edited anonymous equality state')
            continue
        try: k=m1.equality(name).id
        except KeyError: continue
        if m0.eq_type[e] != m1.eq_type[k]: raise ValueError('equality type changed during migration')
        d1.eq_active[k]=d0.eq_active[e]
    for a,b in zip(d0.warning,d1.warning): b.number=a.number; b.lastinfo=a.lastinfo
    if d1.userdata.shape != d0.userdata.shape: raise ValueError('userdata migration unsupported')
    d1.userdata[:]=d0.userdata; d1.time=d0.time
    new.robot.fixed_head=old.robot.fixed_head.copy(); new._clock_remainder=old._clock_remainder
    mujoco.mj_forward(m1,d1)


class BuildSession:
    def __init__(self, source, robot_config, config, collection, *, path=None, initial_frozen_dir=None):
        self.config,self.collection,self.path=config,collection,Path(path) if path else None
        self.edits_used=0; self.record_images=False; self.simulated_s=0.
        self.closed=False; self.revision=0; self.transactions=[]; self.placements={}; self.extra_instances={}
        self.last_check=None; self.history=[]; self._undo=[]
        self.deadline=time.monotonic()+config.budget.timeout_s
        self.pool=AssetPool(config.asset_pool)
        if initial_frozen_dir:
            version=read_json(Path(initial_frozen_dir)/'version.json')
            if version.get('schema_version') not in (None,'1.0') or version.get('restoration') is not None:
                raise ValueError('cached build initialization requires an unedited v1 foundation model')
            if any(version['source'][k] != v for k,v in asdict(source).items()): raise ValueError('cached source descriptor mismatch')
            self.sim=Simulation.from_snapshot(initial_frozen_dir,robot_config,target=config.target)
            self.spec=None  # lazy description; state-only edits need no remote mesh recompilation
        else:
            self.sim=Simulation.from_source(source,robot_config,target=config.target)
            self.spec=self.sim.spec
        try:
            self.initialization=initialize_robot(self.sim,collection,base=config.robot_base)
            self.initial_robot_base=self.sim.robot.group('base').copy()
            region=self.select_region(config.support,config.region_geom)
            self.placements[config.target]=region
            if config.case_type=='case3':
                try: self.sim.model.body(config.parameters['obstacle'])
                except KeyError: pass
                else: self.placements[config.parameters['obstacle']]=region
            for name in config.protected: self.sim.model.body(name)
            self.initial_poses=poses(self.sim)
            if initial_frozen_dir:
                self.dependencies=[{'path':source.xml_path,'exists':None,'sha256':version['source']['xml_sha256'],'kind':'mjcf','resolution':'frozen_manifest'},
                                   {'path':robot_config.model_path,'exists':None,'sha256':None,'kind':'robot','resolution':'embedded_in_verified_mjb'}]
                self.source_digest=version['source']
            else:
                self.dependencies=dependencies(source.xml_path)+dependencies(robot_config.model_path)
                self.source_digest=source.provenance()
        except BaseException:
            self.sim.close(); raise

    def _guard(self):
        if self.closed or self.sim.closed or self.sim.started or self.sim.robot.execution_started:
            raise RuntimeError('construction is closed or continuous execution has begun')

    def active(self):
        self._guard()
        if time.monotonic()>self.deadline: raise TimeoutError('build wall-clock budget exhausted')

    def select_region(self, support, geom=None):
        regions=extract_regions(self.sim,support,geom)
        if not regions: raise ValueError('no reliable planar support region')
        return regions[0]

    @property
    def model_id(self): return model_identity(self.sim)
    @property
    def checkpoint_id(self): return checkpoint_identity(self.sim,self.model_id)

    def snapshot(self):
        self.active()
        return {'spec':self.spec.copy() if self.spec is not None else None,'sim':self.sim,'state':self.sim.state_vector().copy(),
                'head':self.sim.robot.fixed_head.copy(),'clock':self.sim._clock_remainder,
                'warnings':[(int(w.number),float(w.lastinfo)) for w in self.sim.data.warning],
                'placements':self.placements.copy(),'extra_instances':self.extra_instances.copy()}

    def restore(self, checkpoint):
        self._guard()  # Rollback remains legal after timeout, never after begin()/close().
        if checkpoint['sim'].started or checkpoint['sim'].closed: raise RuntimeError('checkpoint is not a construction session')
        if self.sim is not checkpoint['sim']: self.sim.close()
        self.sim=checkpoint['sim']; self.spec=checkpoint['spec']
        mujoco.mj_setState(self.sim.model,self.sim.data,checkpoint['state'],self.sim.state_spec)
        self.sim.robot.fixed_head=checkpoint['head'].copy(); self.sim._clock_remainder=checkpoint['clock']
        self.placements=checkpoint['placements']; self.extra_instances=checkpoint['extra_instances']
        for w,(n,info) in zip(self.sim.data.warning,checkpoint['warnings']): w.number=n; w.lastinfo=int(info)
        mujoco.mj_forward(self.sim.model,self.sim.data)
        self.revision+=1; self.last_check=None; self.history=[]

    def settle(self):
        self.active(); self.history=[]
        self.revision+=1; self.last_check=None
        names=list(self.placements)
        def sample():
            objects={}
            for name in names:
                b=self.sim.model.body(name).id; v=np.zeros(6)
                mujoco.mj_objectVelocity(self.sim.model,self.sim.data,mujoco.mjtObj.mjOBJ_BODY,b,v,0)
                objects[name]={'pose':np.r_[self.sim.data.xpos[b],self.sim.data.xquat[b]].copy(),'velocity':v.copy()}
            anomalies=[]
            for contact in self.sim.data.contact:
                if contact.dist < -self.config.protocol.penetration_m and self.sim.model.geom_bodyid[contact.geom1] != self.sim.model.geom_bodyid[contact.geom2]:
                    anomalies.append({'code':'severe_penetration_during_settle','distance_m':float(contact.dist),'geoms':[self.sim.model.geom(int(contact.geom1)).name,self.sim.model.geom(int(contact.geom2)).name]})
            self.history.append({'time':float(self.sim.data.time),'objects':objects,'anomalies':anomalies})
        mujoco.mj_forward(self.sim.model,self.sim.data); sample()
        p=self.config.protocol
        start=float(self.sim.data.time)
        while self.sim.data.time-start < p.max_settle_s-1e-10:
            self.active()
            # Only mj_step; robot ctrl remains constant, never teleport or reset each frame.
            mujoco.mj_step(self.sim.model,self.sim.data); self.simulated_s+=self.sim.model.opt.timestep
            mujoco.mj_forward(self.sim.model,self.sim.data); sample()
            if self.sim.data.time-start >= p.settle_s-1e-10:
                check=inspect_build(self,self.history,{},include_requirements=False)
                if not any(x['code'] in ('unstable_or_flight','stability_window_missing') for x in check['issues']): break
        return self.history

    def validate(self, baseline=None, *, include_requirements=True):
        self.active()
        self.last_check=inspect_build(self,self.history,baseline if baseline is not None else self.protected_baseline(),include_requirements=include_requirements)
        self.last_check['revision']=self.revision
        return self.last_check

    def protected_baseline(self):
        return {n:p for n,p in self.initial_poses.items() if n not in self.config.editable or n in self.config.protected}

    def _precheck(self, operations, revision):
        self.active()
        if revision != self.revision: raise ValueError('stale operation revision')
        if not isinstance(operations,list) or not operations: raise ValueError('empty transaction')
        names={o.get('instance') for o in operations if isinstance(o,dict)}
        for o in operations:
            if not isinstance(o,dict) or set(o)-{'op','instance','pose','asset_id','region_id','reason'} or not {'op','instance'} <= set(o):
                raise ValueError('invalid operation schema')
            op,name=o['op'],o['instance']
            if op not in self.config.allowed_operations or name not in self.config.editable or name in self.config.protected or name.startswith('robot_0/'):
                raise PermissionError('edit not allowed/protected')
            if op=='delete' and name in (self.config.target,self.config.parameters.get('obstacle')):
                raise PermissionError('required target/obstacle cannot be deleted')
            if op in ('move','rotate','add'):
                pose=np.asarray(o.get('pose'),dtype=float)
                if pose.shape!=(7,) or not np.isfinite(pose).all() or abs(np.linalg.norm(pose[3:])-1)>1e-6: raise ValueError('pose must be finite xyz + normalized wxyz')
            if op=='add':
                self._ensure_spec()
                if self.spec.body(name) is not None: raise ValueError('instance id conflict')
                self.pool.load(o.get('asset_id'))
            else:
                body=self.sim.model.body(name).id
                if self.sim.model.body_parentid[body]!=0: raise ValueError('only top-level stable instances editable')
                if op=='delete' or self.sim.model.body_jntnum[body]==0:
                    # Metadata dependency is only a conservative rejection hint. Contacts add physical evidence.
                    dependencies_set={x['instance_id'] for x in self.sim.catalog if x.get('parent_instance_id')==name}
                    dependencies_set |= {n for n,r in self.placements.items() if r.support==name}
                    bodies=descendants(self.sim.model,body)
                    for c in self.sim.data.contact:
                        ba,bb=int(self.sim.model.geom_bodyid[c.geom1]),int(self.sim.model.geom_bodyid[c.geom2])
                        other=bb if ba in bodies and bb not in bodies else ba if bb in bodies and ba not in bodies else None
                        if other is not None and other!=0 and self.sim.data.xpos[other,2] > self.sim.data.xpos[body,2]+.005:
                            for x in self.sim.catalog:
                                if other in descendants(self.sim.model,x['body_id']) and x['instance_id']!=name:
                                    dependencies_set.add(x['instance_id'])
                    if dependencies_set-names: raise ValueError(f'unhandled support dependents: {sorted(dependencies_set-names)}')
                if self.sim.model.body_jntnum[body]==0 and op in ('move','rotate'):
                    rotation=np.zeros(9); mujoco.mju_quat2Mat(rotation,np.asarray(o['pose'][3:],dtype=float))
                    relative=rotation.reshape(3,3) @ self.sim.data.xmat[body].reshape(3,3).T
                    if abs(o['pose'][2]-self.sim.data.xpos[body,2])>1e-8 or not np.allclose(relative[:,2],[0,0,1],atol=1e-6):
                        raise ValueError('fixed furniture edits only support horizontal translation/world yaw')
                if op=='rotate' and not np.allclose(o['pose'][:3],self.sim.data.xpos[body],atol=1e-8): raise ValueError('rotate cannot translate')
            if o.get('region_id') and o['region_id'] not in {r.region_id for r in self.placements.values()}:
                raise ValueError('unknown region id')

    def _ensure_spec(self):
        if self.spec is None:
            if self.sim.source.provenance()!=self.source_digest: raise ValueError('live source changed; cannot rebuild cached foundation')
            self.spec=Simulation.prepare_spec(self.sim.source,self.sim.robot.config)

    def _rebuild(self):
        model=self.spec.compile()
        catalog=instance_catalog(model,self.sim.source)
        for name,a in self.extra_instances.items():
            catalog=[x for x in catalog if x['instance_id']!=name]
            catalog.append({'instance_id':name,'mjcf_body':name,'body_id':model.body(name).id,'asset_id':a['asset_id'],
                            'category':None,'name_map':{},'pose_frame':'world','pose_order':'xyz_wxyz'})
        new=Simulation(model,self.sim.robot.config,catalog,self.config.target)
        try:
            new.source=self.sim.source; new.restoration=self.sim.restoration; new.spec=self.spec
            if hasattr(self.sim,"frozen_provenance"): new.frozen_provenance=self.sim.frozen_provenance
            migrate_state(self.sim,new)
        except Exception:
            new.close(); raise
        previous=self.sim; self.sim=new
        return previous

    def _pose(self, name, pose):
        b=self.sim.model.body(name).id
        count=int(self.sim.model.body_jntnum[b]); adr=int(self.sim.model.body_jntadr[b])
        if count==1 and self.sim.model.jnt_type[adr]==mujoco.mjtJoint.mjJNT_FREE:
            q=int(self.sim.model.jnt_qposadr[adr]); v=int(self.sim.model.jnt_dofadr[adr])
            self.sim.data.qpos[q:q+7]=pose; self.sim.data.qvel[v:v+6]=0
            if name not in self.placements: self.placements[name]=self.select_region(self.config.support,self.config.region_geom)
        elif count==0:
            self._ensure_spec()
            body=self.spec.body(name); body.pos=pose[:3]; body.quat=pose[3:]
            return self._rebuild()
        else: raise ValueError('articulated root pose/joint edits unsupported')
        mujoco.mj_forward(self.sim.model,self.sim.data)
        return None

    def transact(self, operations, *, revision, reason='rule_candidate', source='rule'):
        self.active()
        if source not in ('rule','model'): raise ValueError('invalid decision source')
        if not isinstance(operations,list): raise ValueError('invalid operations')
        if self.edits_used+len(operations)>self.config.budget.edits: raise RuntimeError('build_edit_budget_exhausted')
        self.edits_used+=len(operations)
        # Rejected NaN/malformed requests must still leave serializable failure evidence.
        from ..io import canonical
        try: canonical(operations)
        except (ValueError,TypeError) as exc:
            entry={'transaction':len(self.transactions),'status':'rejected','source':source,'revision_before':self.revision,
                   'operations_repr':repr(operations),'error_type':type(exc).__name__,'error':'non_json_or_nonfinite_operation'}
            self.transactions.append(entry)
            if self.path: write_json(self.path/'transactions'/f'{entry["transaction"]:04d}.json',entry)
            return entry
        checkpoint=self.snapshot(); before_id=self.checkpoint_id
        entry={'transaction':len(self.transactions),'revision_before':self.revision,'reason':reason,'source':source,
               'operations':operations,'checkpoint_before':before_id,'status':'rejected','state_before':self.sim.observe_state()}
        intermediates=[]
        try:
            self._precheck(operations,revision)
            affected={o['instance'] for o in operations}
            baseline={n:p for n,p in poses(self.sim).items() if n not in affected}
            # Permanently protected objects and prior requirements are checked even in grouped edits.
            for o in operations:
                name=o['instance']; op=o['op']
                if op=='delete':
                    self._ensure_spec()
                    self.spec.delete(self.spec.body(name)); self.placements.pop(name,None); self.extra_instances.pop(name,None)
                    intermediates.append(self._rebuild())
                elif op=='add':
                    asset,a=self.pool.load(o['asset_id']); prefix=name+'/'
                    self.spec.attach(asset,prefix=prefix,frame=self.spec.worldbody.add_frame())
                    self.spec.body(prefix+a['root_body']).name=name
                    self.extra_instances[name]=a
                    intermediates.append(self._rebuild())
                    previous=self._pose(name,o['pose'])
                    if previous: intermediates.append(previous)
                    self.placements[name]=self.select_region(self.config.support,self.config.region_geom)
                else:
                    previous=self._pose(name,o['pose'])
                    if previous: intermediates.append(previous)
            self.revision+=1; self.last_check=None
            entry['state_after_unsettled']=self.sim.observe_state()
            if self.path:
                from ..agents.protocol import observe
                observe(self,[],self.path/'transaction_observations'/f'{entry["transaction"]:04d}-unsettled',stage='after_edit_unsettled',images=self.record_images)
            self.settle(); check=self.validate(baseline)
            entry['state_after_settle']=self.sim.observe_state()
            if self.path:
                from ..agents.protocol import observe
                observe(self,[],self.path/'transaction_observations'/f'{entry["transaction"]:04d}-settled',images=self.record_images)
            entry['check']=check
            if not check['valid']: raise ValueError('post-edit physical/build requirements failed')
            entry.update(status='committed',revision_after=self.revision,checkpoint_after=self.checkpoint_id)
            self._undo.append(checkpoint)
        except Exception as exc:
            entry.update(status='rolled_back',error_type=type(exc).__name__,error=str(exc))
            self.restore(checkpoint)
            entry['revision_after']=self.revision; entry['checkpoint_restored']=self.checkpoint_id
        finally:
            for previous in intermediates:
                if previous is not checkpoint['sim'] and previous is not self.sim: previous.close()
            self.transactions.append(entry)
            if self.path:
                write_json(self.path/'transactions'/f'{entry["transaction"]:04d}.json',entry)
        return entry

    def undo(self):
        self.active()
        if not self._undo: raise ValueError('nothing to undo')
        before=self.checkpoint_id; self.restore(self._undo.pop())
        entry={'transaction':len(self.transactions),'status':'undone','source':'rule','checkpoint_before':before,'checkpoint_after':self.checkpoint_id,'revision_after':self.revision}
        self.transactions.append(entry)
        if self.path: write_json(self.path/'transactions'/f'{entry["transaction"]:04d}.json',entry)
        return entry

    def freeze(self, path, *, build_id):
        self.active()
        check=self.validate()
        if not check['valid']: raise ValueError('freeze requires fresh valid physics and all required checks')
        if not hasattr(self.sim,'frozen_provenance') and self.sim.source.provenance()!=self.source_digest: raise ValueError('source changed during build')
        path=Path(path)
        if path.exists(): raise FileExistsError(path)
        model_id=self.model_id; checkpoint_id=self.checkpoint_id
        version=self.sim.freeze(path)
        version.pop('version_id')
        frozen_dependencies=list(self.dependencies)
        for asset in self.extra_instances.values():
            frozen_dependencies.extend(dependencies((self.pool.path.parent/asset['xml_path']).resolve()))
        version.update(schema_version='2.0',model_id=model_id,checkpoint_id=checkpoint_id,build_id=build_id,
                       mapping_id=digest(self.sim.catalog), dependencies=frozen_dependencies,
                       build_protocol=asdict(self.config.protocol))
        version['version_id']=digest(version)
        write_json(path/'version.json',version)
        write_json(path/'checksums.json',{n:file_digest(path/n) for n in ('model.mjb','initial.npz','version.json','instances.json')})
        self.close()
        return version

    def close(self):
        if not self.closed:
            self.sim.close()
            for cp in self._undo: cp['sim'].close()
            self.closed=True
