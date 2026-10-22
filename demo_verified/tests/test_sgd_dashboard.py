"""Offline, no-provider regression tests for the SGD paired benchmark UI."""
import json
from pathlib import Path
from app.public_report import public_sgd_view


def write_sample(base, n=4):
    p = base / 'datasets/public/sgd_new_relevant_100.jsonl'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(''.join(json.dumps({'id':f'sgd_{i}', 'domain':'Restaurants_1',
                                     'transcript':f'Caller: Test {i}\nAgent: Booked.'})+'\n'
                          for i in range(1, n+1)), encoding='utf8')


def write_report(base, backend, cases, source='datasets/public/sgd_new_relevant_100.jsonl'):
    p = base / f'datasets/reports/sgd_new_relevant_{"gemini" if backend=="gemini_events" else "rules"}.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({'kind':'unlabelled_public_corpus_stress','backend':backend,
                             'source':source,'cases':cases}), encoding='utf8')


def test_join_by_id_and_distinguish_absence_from_mistake(tmp_path):
    write_sample(tmp_path)
    write_report(tmp_path,'gemini_events',[
        {'id':'sgd_1','status':'evaluated','predicted_booking':True,'has_time':True,'abstain':False},
        {'id':'sgd_2','status':'evaluated','predicted_booking':True,'has_time':True,'abstain':False},
        {'id':'sgd_3','status':'evaluated','predicted_booking':False,'has_time':False,'abstain':True},
        {'id':'sgd_4','status':'provider_error','diagnostic':'GeminiProviderError; http_status=503'},
    ])
    write_report(tmp_path,'rules',[
        {'id':'sgd_2','status':'evaluated','predicted_booking':False,'has_time':False,'abstain':False},
        {'id':'sgd_1','status':'evaluated','predicted_booking':True,'has_time':False,'abstain':False},
        {'id':'sgd_3','status':'evaluated','predicted_booking':False,'has_time':False,'abstain':False},
        {'id':'sgd_4','status':'evaluated','predicted_booking':True,'has_time':True,'abstain':False},
    ])
    view = public_sgd_view(tmp_path)
    assert view['paired'] == 3 and view['comparable']==2
    assert view['disagreements']==1 and view['agreements']==1
    assert view['time_presence_disagreements']==2
    assert view['abstention_affected']==1
    assert [c['compare_status'] for c in view['cases']]==['disagreement','disagreement','abstention','provider_error']
    assert view['reports']['gemini']['evaluated']==3
    assert view['reports']['gemini']['errors']==1
    assert view['cases'][0]['id']=='sgd_1'


def test_missing_rules_does_not_count_as_disagreement(tmp_path):
    write_sample(tmp_path)
    write_report(tmp_path,'gemini_events',[{'id':'sgd_2','status':'evaluated','abstain':False,
                                         'predicted_booking':True,'has_time':True}])
    view=public_sgd_view(tmp_path)
    assert view['paired']==0 and view['comparable']==0 and view['disagreements']==0
    assert view['cases'][1]['compare_status']=='unpaired'


def test_unrelated_or_untrusted_reports_are_not_joined(tmp_path):
    write_sample(tmp_path)
    write_report(tmp_path,'gemini_events',[{'id':'sgd_1','status':'evaluated'}], source='other.jsonl')
    write_report(tmp_path,'rules',[{'id':'sgd_99999','status':'evaluated'}])
    view=public_sgd_view(tmp_path)
    assert view['reports']=={}
    assert len(view['cases'])==4


def test_route_requires_auth_and_shows_existing_saved_results(tmp_path, monkeypatch):
    import os
    os.environ['DATABASE_URL']='sqlite:///./lane_phase0_pytest.db'
    os.environ['DASHBOARD_USER']='lane'
    os.environ['DASHBOARD_PASSWORD']='test-dashboard-password'
    from fastapi.testclient import TestClient
    from app.main import app
    import app.main as main_module
    # This test uses the real templates and route, substituting only the report lookup.
    original=main_module.public_sgd_view
    monkeypatch.setattr(main_module,'public_sgd_view',lambda _: {'total':0,'reports':{},'cases':[],
                      'paired':0,'comparable':0,'agreements':0,'disagreements':0,
                      'time_presence_disagreements':0,'abstention_affected':0})
    with TestClient(app) as c:
        assert c.get('/comparison').status_code==401
        result=c.get('/comparison',auth=('lane','test-dashboard-password'))
        assert result.status_code==200
        assert 'SGD 100' in result.text
    monkeypatch.setattr(main_module,'public_sgd_view',original)


def test_route_renders_joined_predictions_and_escapes_transcripts(tmp_path, monkeypatch):
    import os
    os.environ['DASHBOARD_USER']='lane'
    os.environ['DASHBOARD_PASSWORD']='test-dashboard-password'
    write_sample(tmp_path, n=2)
    data_file=tmp_path/'datasets/public/sgd_new_relevant_100.jsonl'
    lines=data_file.read_text(encoding='utf8').splitlines()
    row=json.loads(lines[0])
    row['transcript']='<script>alert("not-safe")</script>'
    lines[0]=json.dumps(row)
    data_file.write_text('\n'.join(lines)+'\n',encoding='utf8')
    write_report(tmp_path,'gemini_events',[{'id':'sgd_1','status':'evaluated',
                 'predicted_booking':True,'abstain':False,'has_time':True},
                {'id':'sgd_2','status':'provider_error','diagnostic':'GeminiProviderError; http_status=503'}])
    write_report(tmp_path,'rules',[{'id':'sgd_1','status':'evaluated',
                 'predicted_booking':False,'abstain':False,'has_time':False},
                {'id':'sgd_2','status':'evaluated','predicted_booking':False,'abstain':False,'has_time':False}])
    from fastapi.testclient import TestClient
    from app.main import app
    import app.main as main_module
    monkeypatch.setattr(main_module,'public_sgd_view',lambda _: public_sgd_view(tmp_path))
    with TestClient(app) as c:
        page=c.get('/comparison',auth=('lane','test-dashboard-password'))
    assert page.status_code==200
    assert 'sgd_1' in page.text and 'sgd_2' in page.text
    assert 'Booking predicted' in page.text and 'No booking predicted' in page.text
    assert 'GeminiProviderError; http_status=503' in page.text
    assert 'data-group="disagreement"' in page.text
    assert '<script>alert("not-safe")</script>' not in page.text
    assert '&lt;script&gt;' in page.text


def test_expression_hints_are_diagnostic_not_gold_labels(tmp_path):
    write_sample(tmp_path, n=1)
    path=tmp_path/'datasets/public/sgd_new_relevant_100.jsonl'
    item=json.loads(path.read_text().strip())
    item['transcript']='Caller: Half past 5, or 17:00, on March 12th.\nAgent: Booked.'
    path.write_text(json.dumps(item)+'\n', encoding='utf-8')
    view=public_sgd_view(tmp_path)
    assert set(view['cases'][0]['expression_hints'])=={'24-hour clock','ordinal date','half-past time'}
    assert view['reports']=={} and view['paired']==0  # hints are not predictions


def test_benchmark_page_prioritizes_public_result_and_old_synthetic_is_collapsed():
    from pathlib import Path
    page=(Path(__file__).parents[1]/'app/templates/comparison.html').read_text(encoding='utf8')
    assert page.index('SGD 100 · Gemini vs Rules') < page.index('Older eight-conversation synthetic')
    assert 'not saved in this checkpoint' in page
    dash=(Path(__file__).parents[1]/'app/templates/dashboard.html').read_text(encoding='utf8')
    assert 'Compare extractors →</a><a href="/evaluation">' in dash
