"""Additive, download-only dependency plans for the new application targets."""
import hashlib
import json


def install(g):
    original=g['_deps_plan']
    def plan(target,files):
        if target in ('python-app','desktop','ros2'):
            seen={k:v for k,v in files.items() if k in ('requirements.lock','uv.lock','pyproject.toml')}
            if not {'requirements.lock','uv.lock'} & seen.keys():return None
            kind,cache,what='python','wheels','locked Python wheels'
            after='cp -n /deps/wheels/* /cache/ 2>/dev/null; true'
        elif target in ('kotlin-jvm','android') and 'forge-dependencies.lock.json' in files:
            seen={'forge-dependencies.lock.json':files['forge-dependencies.lock.json']}
            kind,cache,what,after='maven','maven','locked Maven jars',None
        else:return original(target,files)
        script='cd /tmp/m && python3 /opt/forge-tools/locked_dependencies.py '+kind
        data=dict(label=target,what=what,files=seen,key=hashlib.sha256(json.dumps(seen,sort_keys=True).encode()).hexdigest(),
                  script=script,offline=lambda s:s.replace('locked_dependencies.py '+kind,'locked_dependencies.py '+kind+' --offline'),
                  cache=cache,env={'PIP_CACHE_DIR':'/tmp/pipcache'})
        if after:data['after']=after
        return data
    g['_deps_plan']=plan
    g['DEPS_TOOLCHAINS']=(*g['DEPS_TOOLCHAINS'],'python-app','desktop','ros2','kotlin-jvm','android')
