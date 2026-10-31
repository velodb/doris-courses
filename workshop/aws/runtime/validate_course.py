"""Run the selected rta-main Labs and quizzes in managed kernels on a disposable host."""
from datetime import datetime,timezone
import json,os,subprocess,sys,time
from pathlib import Path
import nbformat
from nbclient import NotebookClient


def main():
    expected=json.loads(Path(sys.argv[1]).read_text())
    root=Path(expected['course_output_dir']);root.mkdir(parents=True,exist_ok=True)
    runtime=json.loads(Path('/etc/doris-workshop/runtime.json').read_text())
    activity=json.loads(Path('/etc/doris-workshop/activity.json').read_text())
    assert os.geteuid()!=0 and runtime['course_ref']==expected['course_ref']
    assert activity['event_id']==expected['event_id'] and activity['learner_id']==expected['learner_id']
    os.environ.update(JUPYTER_PATH='/usr/local/share/doris-workshop/jupyter',
        DORIS_WORKSHOP_EVENT_ID=activity['event_id'],DORIS_WORKSHOP_LEARNER_ID=activity['learner_id'],
        DORIS_BROWSER_ROUTES_FILE='/etc/doris-workshop/browser-urls.json')
    course=next(c for c in runtime['courses'] if c['kernel']=='doris-rta')
    assert course['profiles']==['rta-main']
    source=Path('/opt/doris-courses')/course['course_dir']
    workspace=Path('/var/lib/doris-workshop/workspace')/course['course_dir']
    report={'schema_version':1,'kind':'doris-workshop-course-validation','image_id':expected['ami_id'],
        'instance_id':expected['instance_id'],'course_ref':runtime['course_ref'],'started_at':datetime.now(timezone.utc).isoformat(),
        'modules':[],'notebook_count':0,'code_cells':0,'documented_transitions':[],'done':False,'passed':False}
    def save():
        temp=root/'progress.tmp';temp.write_text(json.dumps(report,indent=2)+'\n');temp.replace(root/'progress.json')
    def execute(path):
        nb=nbformat.read(path,as_version=4)
        client=NotebookClient(nb,timeout=3600,kernel_name=course['kernel'],resources={'metadata':{'path':str(path.parent)}})
        row={'notebook':str(path.relative_to(workspace)),'cells':[],'passed':False}
        try:
            with client.setup_kernel():
                for index,cell in enumerate(nb.cells):
                    if cell.cell_type!='code':continue
                    began=time.monotonic()
                    try:client.execute_cell(cell,index)
                    finally:
                        row['cells'].append({'cell':cell.get('id'),'seconds':round(time.monotonic()-began,2),
                            'errors':sum(o.get('output_type')=='error' for o in cell.get('outputs',[]))})
            row['passed']=True
        except Exception:
            # Notebook exceptions can embed code, passwords and API responses.
            # Executed Notebooks stay private on this disposable validation host.
            row['error']={'type':'cell-execution-failed','message':'execution failed; inspect the private validation Notebook'}
        finally:
            nbformat.write(nb,root/path.name)
        report['notebook_count']+=1;report['code_cells']+=len(row['cells'])
        return row
    save()
    for module in course['modules']:
        if module['id']=='module-9':
            subprocess.run(['docker','stop','doris-rta'],check=True,capture_output=True)
            report['documented_transitions'].append('Stopped single-node sandbox before Lab 9, as required by the course.')
        if module['id']=='module-10':
            subprocess.run(['docker','compose','-f',str(source/'level3/module09-sharding-replication/compose.multinode.yml'),'stop'],check=True,capture_output=True)
            report['documented_transitions'].append('Stopped Module 9 before Lab 10, preserving its volumes as the course directs.')
        row={'module':module['id'],'lab':execute(workspace/module['lab'])}
        quiz=Path(module['quiz']).with_suffix('.ipynb')
        if row['lab']['passed']:row['quiz']=execute(workspace/quiz)
        row['passed']=row['lab']['passed'] and row.get('quiz',{}).get('passed',False)
        report['modules'].append(row);save()
        print(json.dumps({'module':module['id'],'passed':row['passed']}),flush=True)
        if not row['passed']:break
    report['done']=True
    report['passed']=len(report['modules'])==len(course['modules']) and all(m['passed'] for m in report['modules'])
    report['finished_at']=datetime.now(timezone.utc).isoformat();save()
    print('COURSE_REPORT='+json.dumps(report),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':sys.exit(main())
