"""Small qualified asset pool; qualification dimensions are not conflated."""
from pathlib import Path
import mujoco
from ..io import read_json, file_digest


class AssetPool:
    def __init__(self, path=None):
        self.path = Path(path).resolve() if path else None
        value = read_json(self.path) if path else {'schema_version':'2.0','assets':[]}
        if set(value) != {'schema_version','assets'} or value['schema_version'] != '2.0': raise ValueError('invalid asset pool')
        self.assets = {}
        for a in value['assets']:
            if set(a) != {'asset_id','xml_path','root_body','sha256','qualification','parts'}: raise ValueError('invalid asset record')
            if a['asset_id'] in self.assets: raise ValueError('duplicate asset id')
            self.assets[a['asset_id']] = a

    def load(self, asset_id):
        a = self.assets[asset_id]
        q = a['qualification']
        if q.get('load',{}).get('status') != 'verified' or q.get('placement',{}).get('status') != 'verified':
            raise ValueError('asset must have explicit loading and placement qualification')
        if not all(q[k].get('evidence') for k in ('load','placement')): raise ValueError('asset qualification lacks evidence')
        path = (self.path.parent/a['xml_path']).resolve()
        if file_digest(path) != a['sha256']: raise ValueError('asset digest mismatch')
        spec = mujoco.MjSpec.from_file(str(path)); spec.compile()
        body = spec.body(a['root_body'])
        if body is None or body.parent != spec.worldbody or len(list(spec.worldbody.bodies)) != 1:
            raise ValueError('asset must expose exactly one top-level body')
        joints=list(body.joints)
        if len(joints)!=1 or joints[0].type!=mujoco.mjtJoint.mjJNT_FREE:
            raise ValueError('v2 insertion pool only supports one free root joint')
        return spec, a
