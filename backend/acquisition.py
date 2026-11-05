"""Read-only acquisition. No imported executable is ever invoked."""
import os, re, stat, zipfile, shutil
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse, quote
import httpx
from .store import DATA, ROOT

MAX_FILE = int(os.getenv('ONTO_MAX_FILE_BYTES', '2097152'))
MAX_ARCHIVE = int(os.getenv('ONTO_MAX_ARCHIVE_BYTES', '268435456'))
MAX_ARCHIVE_ENTRIES = int(os.getenv('ONTO_MAX_ARCHIVE_ENTRIES', '100000'))
MAX_EXTRACT = int(os.getenv('ONTO_MAX_EXTRACT_BYTES', '1073741824'))
TOKENS: dict[str,str] = {}
class AcquisitionError(ValueError): pass

def safe_relative(value):
    p=PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or '\\' in value or '\x00' in value or (p.parts and ':' in p.parts[0]):
        raise AcquisitionError('Unsafe relative path')
    return p

def allowed_local(value):
    p=Path(value).expanduser().resolve()
    roots=[Path(x).expanduser().resolve() for x in os.getenv('ONTO_LOCAL_ROOTS',str(ROOT)).split(os.pathsep)]
    if not any(p.is_relative_to(r) for r in roots):
        raise AcquisitionError('Folder is outside ONTO_LOCAL_ROOTS. Configure an allowed backend directory.')
    if not p.is_dir(): raise AcquisitionError('Folder does not exist on the backend machine')
    return p

def github_repo(url):
    p=urlparse(url)
    if p.scheme!='https' or p.netloc!='github.com' or p.query or p.fragment or p.username:
        raise AcquisitionError('Use an https://github.com/owner/repository URL')
    parts=p.path.strip('/').removesuffix('.git').split('/')
    if len(parts)!=2 or not all(re.fullmatch(r'[A-Za-z0-9_.-]+',x) and x not in ['.','..'] for x in parts):
        raise AcquisitionError('Use a repository URL without a branch or file path')
    return '/'.join(parts)

def extract_zip(archive, dest, strip_root=False):
    dest=Path(dest); dest.mkdir(parents=True,exist_ok=True)
    total=0; blocked=[]
    with zipfile.ZipFile(archive) as z:
        for index,info in enumerate(z.infolist()):
            target=None
            try:
                if index >= MAX_ARCHIVE_ENTRIES: raise AcquisitionError('Archive entry resource budget exceeded')
                p=safe_relative(info.filename)
                if strip_root: p=PurePosixPath(*p.parts[1:])
                if not p.parts or info.is_dir(): continue
                if stat.S_ISLNK(info.external_attr >> 16): raise AcquisitionError('Symlink blocked')
                if info.file_size > MAX_FILE: raise AcquisitionError('File exceeds configured byte budget')
                if total+info.file_size > MAX_EXTRACT: raise AcquisitionError('Extraction byte budget exceeded')
                target=dest/str(p)
                if not target.resolve().is_relative_to(dest.resolve()): raise AcquisitionError('Path escape blocked')
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, target.open('wb') as out:
                    actual=0
                    while chunk:=src.read(65536):
                        actual+=len(chunk)
                        if actual>MAX_FILE or total+actual>MAX_EXTRACT: raise AcquisitionError('Expanded data exceeds budget')
                        out.write(chunk)
                total+=actual
            except (ValueError, OSError) as e:
                blocked.append({'path':info.filename,'reason':str(e)})
                if target is not None and target.is_file(): target.unlink()
    return blocked

def github_snapshot(source, dest):
    repo=github_repo(source['location']); token=TOKENS.get(source['id']) or os.getenv('GITHUB_TOKEN')
    headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
    if token: headers['Authorization']='Bearer '+token
    with httpx.Client(timeout=60, follow_redirects=False) as client:
        ref=quote(source.get('reference') or 'HEAD',safe='')
        r=client.get(f'https://api.github.com/repos/{repo}/commits/{ref}',headers=headers)
        if r.status_code in (401,403,404,429):
            raise AcquisitionError(f'GitHub returned {r.status_code}. Check credentials, reference, access, or rate limit (remaining: {r.headers.get("x-ratelimit-remaining","unknown")}; reset: {r.headers.get("x-ratelimit-reset","unknown")}).')
        if r.status_code!=200: raise AcquisitionError(f'GitHub acquisition failed ({r.status_code})')
        sha=r.json()['sha']
        # The archive API returns a redirect; credentials are never forwarded across hosts.
        r=client.get(f'https://api.github.com/repos/{repo}/zipball/{sha}',headers=headers)
        if r.status_code!=302: raise AcquisitionError(f'GitHub archive unavailable ({r.status_code})')
        location=r.headers['location']; parsed=urlparse(location)
        if parsed.scheme!='https' or parsed.hostname!='codeload.github.com': raise AcquisitionError('Unexpected GitHub archive host')
        archive=dest.parent/(dest.name+'.zip')
        try:
            with client.stream('GET',location) as stream:
                if stream.status_code!=200: raise AcquisitionError(f'Archive download failed ({stream.status_code})')
                size=0
                with archive.open('wb') as out:
                    for chunk in stream.iter_bytes(65536):
                        size+=len(chunk)
                        if size>MAX_ARCHIVE: raise AcquisitionError('Archive transfer byte budget exceeded')
                        out.write(chunk)
            blocked=extract_zip(archive,dest,True)
        finally: archive.unlink(missing_ok=True)
    sub=safe_relative(source.get('subdirectory',''))
    root=dest/str(sub)
    if not root.is_dir(): raise AcquisitionError('Selected repository subdirectory does not exist')
    return root,sha,blocked
