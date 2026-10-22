"""No live API calls; validate persistent checkpoints and no fabricated scores."""
import json
from pathlib import Path
import pytest
from app.extract import Commitment, _safe_provider_failure
from scripts.public_benchmark import stress


def fixture_dataset(tmp_path, n=4):
    p=tmp_path/'sgd.jsonl'
    p.write_text(''.join(json.dumps({'id':f'sgd_{i}','transcript':f'Caller: book {i}'})+'\n' for i in range(1,n+1)))
    return p


def test_failed_midway_creates_checkpoint_and_resume(tmp_path, monkeypatch):
    source=fixture_dataset(tmp_path)
    outfile=tmp_path/'out.json'
    calls=[]
    def fake_extract(transcript, tz, *, backend, reference_time):
        i=int(transcript.split()[-1]); calls.append(i)
        if i==2:
            return Commitment(reason='Extractor unavailable/invalid: BadRequestError; http_status=400; code=invalid_request_error')
        return Commitment(confirmed=bool(i%2), abstain=False)
    monkeypatch.setattr('app.extract.extract_commitment',fake_extract)
    with pytest.raises(RuntimeError,match='Checkpoint saved'):
        stress(source,backend='groq_events',n=4,pace=0,output=outfile,allow_cloud=True)
    early=json.loads(outfile.read_text())
    assert early['evaluated_n']==1
    assert early['provider_error_n']==1
    assert early['unprocessed_n']==2
    assert early['accuracy'] is None
    assert calls==[1,2]
    result=stress(source,backend='groq_events',n=4,pace=0,output=outfile,allow_cloud=True,
                  resume=True,continue_on_bad_request=True)
    assert calls==[1,2,3,4]
    assert result['provider_error_n']==1 and result['evaluated_n']==3
    assert result['unprocessed_n']==0
    assert result['status']=='partial' and result['accuracy'] is None
    assert result['cases'][1]['status']=='provider_error'
    assert 'predicted_booking' not in result['cases'][1]


def test_resume_different_input_rejected(tmp_path, monkeypatch):
    source=fixture_dataset(tmp_path)
    outfile=tmp_path/'out.json'
    monkeypatch.setattr('app.extract.extract_commitment',lambda *args,**kw: Commitment(abstain=False))
    stress(source,backend='rules',n=4,pace=0,output=outfile)
    with source.open('a') as f: f.write('')
    contents=source.read_text().replace('book 4','book changed')
    source.write_text(contents)
    with pytest.raises(ValueError,match='dataset, model, backend or anchor differs'):
        stress(source,backend='rules',n=4,pace=0,output=outfile,resume=True)


def test_start_at_43_does_not_claim_lost_results(tmp_path, monkeypatch):
    source=fixture_dataset(tmp_path,n=4)
    outfile=tmp_path/'out.json'
    calls=[]
    def fake_extract(transcript, *args,**kwargs):
        calls.append(transcript)
        return Commitment(abstain=False)
    monkeypatch.setattr('app.extract.extract_commitment',fake_extract)
    result=stress(source,backend='rules',n=4,start_at=3,output=outfile)
    assert len(calls)==2
    assert result['evaluated_n']==2 and result['unprocessed_n']==2
    result2=stress(source,backend='rules',n=4,output=outfile,resume=True)
    assert result2['evaluated_n']==4 and result2['unprocessed_n']==0


def test_auth_error_halts_and_checkpoints_even_with_continue(tmp_path, monkeypatch):
    source=fixture_dataset(tmp_path)
    outfile=tmp_path/'out.json'
    monkeypatch.setattr('app.extract.extract_commitment',lambda *args,**kwargs: Commitment(reason='Extractor unavailable/invalid: AuthenticationError; http_status=401'))
    with pytest.raises(RuntimeError,match='AuthenticationError'):
        stress(source,backend='groq_events',n=4,output=outfile,allow_cloud=True,pace=0,continue_on_bad_request=True)
    assert json.loads(outfile.read_text())['provider_error_n']==1


def test_safe_provider_error_has_no_raw_message():
    class BadRequestError(Exception):
        status_code=400
        body={'error':{'type':'invalid_request_error','code':'json_validate_failed','message':'Sensitive transcript and key here','param':'response_format'}}
    reason=_safe_provider_failure(BadRequestError('SECRET RAW MSG'))
    assert 'json_validate_failed' in reason and 'http_status=400' in reason
    assert 'Sensitive' not in reason and 'SECRET' not in reason


def test_retry_previous_provider_failure(tmp_path, monkeypatch):
    source = fixture_dataset(tmp_path, n=2)
    outfile = tmp_path / 'out.json'
    attempts = []
    def extractor(text, *args, **kwargs):
        attempts.append(text)
        if text.endswith('1') and attempts.count(text) == 1:
            return Commitment(reason='Extractor unavailable/invalid: BadRequestError; http_status=400')
        return Commitment(confirmed=False, abstain=False)
    monkeypatch.setattr('app.extract.extract_commitment', extractor)
    result1 = stress(source, backend='groq_events', n=2, output=outfile, allow_cloud=True,
                     pace=0, continue_on_bad_request=True)
    assert result1['provider_error_n']==1
    result2 = stress(source, backend='groq_events', n=2, output=outfile, allow_cloud=True,
                     pace=0, resume=True, retry_provider_errors=True)
    assert result2['provider_error_n']==0
    assert result2['evaluated_n']==2
    assert result2['status']=='complete'


def test_gemini_http_status_is_preserved_in_checkpoint_and_can_skip_one_400(tmp_path, monkeypatch):
    source=fixture_dataset(tmp_path,n=3)
    out=tmp_path/'gemini.json'
    calls=[]
    def fake_extract(text,*args,**kwargs):
        calls.append(text)
        if text.endswith('1'):
            return Commitment(reason='Extractor unavailable/invalid: GeminiProviderError; http_status=400')
        return Commitment(confirmed=False,abstain=False)
    monkeypatch.setattr('app.extract.extract_commitment',fake_extract)
    result=stress(source,backend='gemini_events',n=3,pace=0,allow_cloud=True,
                  continue_on_bad_request=True,output=out)
    assert result['evaluated_n']==2
    assert result['provider_error_n']==1
    assert result['cases'][0]['diagnostic']=='GeminiProviderError; http_status=400'
    assert len(calls)==3


def test_gemini_429_safely_stops_with_status_visible(tmp_path,monkeypatch):
    source=fixture_dataset(tmp_path,n=3)
    out=tmp_path/'gemini.json'
    def fake_extract(text,*args,**kwargs):
        return Commitment(reason='Extractor unavailable/invalid: GeminiProviderError; http_status=429')
    monkeypatch.setattr('app.extract.extract_commitment',fake_extract)
    with pytest.raises(RuntimeError,match='http_status=429'):
        stress(source,backend='gemini_events',n=3,pace=0,allow_cloud=True,
               continue_on_bad_request=True,output=out)
    report=json.loads(out.read_text())
    assert report['provider_error_n']==1
    assert report['cases'][0]['diagnostic']=='GeminiProviderError; http_status=429'
    assert report['unprocessed_n']==2


def test_transient_503_exponential_retry_recovers_without_extra_errors(tmp_path,monkeypatch):
    source=fixture_dataset(tmp_path,n=2)
    out=tmp_path/'out.json'
    calls=[]
    delays=[]
    def fake_extractor(text,*args,**kwargs):
        calls.append(text)
        if len(calls)==1:
            return Commitment(reason='Extractor unavailable/invalid: GeminiProviderError; http_status=503')
        return Commitment(confirmed=False,abstain=False)
    monkeypatch.setattr('app.extract.extract_commitment',fake_extractor)
    monkeypatch.setattr('scripts.public_benchmark.random.uniform',lambda a,b:0)
    monkeypatch.setattr('scripts.public_benchmark.time.sleep',delays.append)
    result=stress(source,backend='gemini_events',n=2,output=out,allow_cloud=True,
                  pace=0,transient_retries=2,transient_backoff=2)
    assert [r['status'] for r in result['cases']]==['evaluated','evaluated']
    assert result['evaluated_n']==2 and result['provider_error_n']==0
    assert calls==['Caller: book 1','Caller: book 1','Caller: book 2']
    assert delays==[2.0,0]


def test_transient_503_skip_and_resume_retry_failed_only(tmp_path,monkeypatch):
    source=fixture_dataset(tmp_path,n=3)
    out=tmp_path/'out.json'
    calls=[]
    fail=True
    def fake_extractor(text,*args,**kwargs):
        nonlocal fail
        calls.append(text)
        if text.endswith('2') and fail:
            return Commitment(reason='Extractor unavailable/invalid: GeminiProviderError; http_status=503')
        return Commitment(confirmed=False,abstain=False)
    monkeypatch.setattr('app.extract.extract_commitment',fake_extractor)
    monkeypatch.setattr('scripts.public_benchmark.time.sleep',lambda seconds:None)
    result=stress(source,backend='gemini_events',n=3,output=out,allow_cloud=True,
                  pace=0,transient_retries=1,transient_backoff=0,skip_transient_errors=True)
    assert result['evaluated_n']==2 and result['provider_error_n']==1
    assert result['cases'][1]['diagnostic']=='GeminiProviderError; http_status=503'
    assert result['status']=='partial'
    fail=False
    result2=stress(source,backend='gemini_events',n=3,output=out,allow_cloud=True,
                   pace=0,resume=True,retry_provider_errors=True,
                   transient_retries=1,transient_backoff=0,skip_transient_errors=True)
    assert result2['evaluated_n']==3 and result2['provider_error_n']==0
    assert calls.count('Caller: book 1')==1
    assert calls.count('Caller: book 3')==1


def test_transient_option_never_retries_or_skips_http429(tmp_path,monkeypatch):
    source=fixture_dataset(tmp_path,n=2)
    out=tmp_path/'out.json'
    seen=[]
    def fake_extractor(text,*args,**kwargs):
        seen.append(text)
        return Commitment(reason='Extractor unavailable/invalid: GeminiProviderError; http_status=429')
    monkeypatch.setattr('app.extract.extract_commitment',fake_extractor)
    with pytest.raises(RuntimeError,match='http_status=429'):
        stress(source,backend='gemini_events',n=2,output=out,allow_cloud=True,
               pace=0,transient_retries=2,transient_backoff=0,skip_transient_errors=True)
    assert len(seen)==1
    report=json.loads(out.read_text())
    assert report['provider_error_n']==1 and report['unprocessed_n']==1


def test_three_persistent_503_cases_trigger_circuit_breaker(tmp_path,monkeypatch):
    source=fixture_dataset(tmp_path,n=4)
    out=tmp_path/'out.json'
    def fake_extractor(text,*args,**kwargs):
        return Commitment(reason='Extractor unavailable/invalid: GeminiProviderError; http_status=503')
    monkeypatch.setattr('app.extract.extract_commitment',fake_extractor)
    with pytest.raises(RuntimeError,match='Provider failure at 3'):
        stress(source,backend='gemini_events',n=4,output=out,allow_cloud=True,
               pace=0,transient_retries=0,skip_transient_errors=True)
    report=json.loads(out.read_text())
    assert report['provider_error_n']==3 and report['unprocessed_n']==1
