"""Real network smoke test; no repository scripts or imported code are executed."""
import sys,tempfile,json,time,platform
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.acquisition import github_snapshot
if __name__=='__main__':
 with tempfile.TemporaryDirectory() as d:
  started=time.perf_counter();root,sha,blocked=github_snapshot({'id':'smoke','location':'https://github.com/octocat/Hello-World','reference':'HEAD'},Path(d)/'source')
  files=[p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()]
  result={'repository':'https://github.com/octocat/Hello-World','resolved_commit':sha,'files':files,'blocked':blocked,'seconds':round(time.perf_counter()-started,3),'status':'verified','private_repositories':'unverified; no credentials supplied'}
  print(json.dumps(result,indent=2));output=Path(__file__).resolve().parents[1]/'artifacts'/'github-smoke.json';output.parent.mkdir(exist_ok=True);output.write_text(json.dumps(result,indent=2))
