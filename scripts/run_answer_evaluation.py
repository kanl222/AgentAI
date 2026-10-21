"""Run the local evaluation script in the current API container and save its report.

Uses standard-library Python only. Reads the container's configured credentials
internally; no credentials are passed through command-line arguments or reports.
"""
from datetime import datetime
from pathlib import Path
import subprocess
import uuid


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root/'work'/('answer-evaluation-'+datetime.now().strftime('%Y%m%d')+'-'+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    output = '/tmp/'+folder.name+'.json'
    source = (root/'scripts/evaluate_answers.py').read_text(encoding='utf-8')
    code = ('import sys; sys.path.insert(0,"/app/scripts"); '
            'sys.argv=["evaluate_answers.py","--output",'+repr(output)+'];\n'+source)
    process = subprocess.Popen(['docker','compose','exec','-T','api','python','-c',code],cwd=root,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    print('Report: '+str(folder),flush=True)
    for line in process.stdout:
        print(line.decode('utf-8',errors='replace').strip(),flush=True)
    error = process.stderr.read().decode('utf-8',errors='replace')
    status = process.wait()
    container = subprocess.run(['docker','compose','ps','-q','api'],cwd=root,
                               capture_output=True,check=True).stdout.decode().strip()
    copy = subprocess.run(['docker','cp',container+':'+output,str(folder/'responses.json')],cwd=root,capture_output=True)
    if copy.returncode:
        print(copy.stderr.decode(errors='replace')[-800:])
        return copy.returncode
    print('Saved: '+str(folder/'responses.json'),flush=True)
    if status:
        print(error[-1800:])
    return status


if __name__ == '__main__':
    raise SystemExit(main())
