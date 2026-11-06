"""F55 R5 CI evidence checks. Never contacts a Fanda or a production database."""
import hashlib,json,os,pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
MANIFEST=ROOT/'iqfanda-agent/f55-r5-source-manifest.json'
def sources():
    m=json.loads(MANIFEST.read_text())
    assert m['r4r1_manifest_sha256']=='0645fc726dd088fe3da226ba150dd4a45d35b3cbe5fa7d118d607e763370ceea'
    assert m['version']=='0.1.171'
    for rel,pin in m['files'].items():
        p=ROOT/'iqfanda-agent'/rel
        assert not p.is_symlink() and p.is_file(), 'missing_source:'+rel
        assert hashlib.sha256(p.read_bytes()).hexdigest()==pin,'source_drift:'+rel
    config=(ROOT/'iqfanda-agent/config.yaml').read_text()
    assert '\n  local_thermostat_mode: disabled\n' in config or '\n  local_thermostat_mode: "disabled"\n' in config
    print('F55_R5_EXACT_R4R1_SOURCE=PASS',flush=True)
def result(log):
    lines=pathlib.Path(log).read_text().splitlines()
    native=[json.loads(x.split('=',1)[1]) for x in lines if x.startswith('F55_R5_NATIVE_RUNTIME=')]
    summaries=[json.loads(x.split('=',1)[1]) for x in lines if x.startswith('F55_R4_ADDON_QA_SUMMARY=')]
    assert len(native)==1 and len(summaries)==1,'missing_or_duplicate_evidence'
    n,s=native[0],summaries[0]
    assert n['architecture'].lower() in ('arm64','aarch64')
    assert n['source_manifest_sha256']==hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    assert n['verified_image_files']>=100
    assert s['total']==125 and s['unit_tests']==81 and s['integration_tests']==18
    assert s['read_only_preflight_tests']==22 and s['worker_routing_tests']==4
    assert s['failures']==s['errors']==s['skipped']==0
    assert s['real_imports'] is True and s['network_attempts']==[]
    assert s['hardware_tested'] is False and s['physical_io']=='SIMULATED'
    assert lines.count('F55_R4_ADDON_QA_PASS=YES')==1
    evidence={'architecture':n['architecture'],'addon_tests':125,'failures':0,'errors':0,'skipped':0,
              'exact_R4R1_sources':True,'physical_hardware_tested':False,'installed':False,
              'stable_release':False,'source_commit':os.environ.get('SOURCE_SHA')}
    print('F55_R5_NATIVE_ARM64_PASS='+json.dumps(evidence),flush=True)
    summary=os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a') as f:
            f.write('## F55 R5 native ARM64\n125 addon tests passed. Exact R4R1 source hashes verified.\n\nNo hardware, no installation, no stable release.\n')
if __name__=='__main__':
    if sys.argv[1]=='sources':sources()
    elif sys.argv[1]=='result':result(sys.argv[2])
    else:raise SystemExit('unknown operation')
