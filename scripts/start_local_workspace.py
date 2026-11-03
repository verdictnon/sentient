"""Start the verified local integration workspaces as transient Linux user services."""
import argparse
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PYTHON=ROOT/'.venv-app/bin/python'
APP=ROOT
MODELS=ROOT/'runs/action_worker_v1/p2_models'


def start(name,description,working_directory,arguments,environment=()):
    active=subprocess.run(['systemctl','--user','is-active','--quiet',name],check=False)
    if active.returncode==0:
        print(name+': already running');return
    command=['systemd-run','--user','--unit='+name,'--description='+description,'--collect',
        '--working-directory='+str(working_directory),'--property=Restart=on-failure']
    command.extend('--setenv='+value for value in environment)
    subprocess.run(command+[str(PYTHON),*arguments],check=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    args=parser.parse_args()
    if not PYTHON.exists() or not (APP/'app/p1/server.py').exists():
        raise SystemExit('Install the app environment; see README.md')
    start('sentient-review-workspace','Sentient recorded video analysis',APP,
        ['-m','app.p1.server','--port','8766','--data-dir',str(ROOT/'runs/review_workspace')],
        ['SENTIENT_MODEL_DIR='+str(MODELS)])
    print('Sentient: http://127.0.0.1:8766 — open the dashboard, select a recording, then Start live analysis.')



if __name__=='__main__':main()
