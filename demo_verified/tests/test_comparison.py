"""Comparison runner tests use a stub; never present stub results as LLM scores."""
import json
from pathlib import Path
import os
import pytest
from app.extract import Commitment
from scripts.compare_extractors import compare, load_cases


def test_comparison_reproduces_offline_baseline(tmp_path, monkeypatch):
    monkeypatch.setenv('DEMO_MODE', 'true')
    result = compare('datasets/extraction_challenge.jsonl',
                     backends=('rules',), output=tmp_path/'report.json')
    m = result['backends']['rules']['metrics']
    assert m['total'] == 8
    assert m['extraction_correct'] == 2
    assert m['covered'] == 3
    assert m['outcomes']['false_booking'] == 1
    assert (tmp_path/'report.md').exists()
    content = (tmp_path/'report.json').read_text()
    assert 'Caller:' not in content
    assert 'Agent:' not in content


def test_uses_same_inputs_labels_not_shared(tmp_path):
    path = tmp_path/'cases.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in [
        {'id': 'one', 'transcript':'Caller: my booking is at 10 AM',
         'timezone':'UTC', 'call_started_at':'2026-10-22T10:00:00Z',
         'commitment':{'confirmed':True,'final_start':'2026-11-03T10:00:00Z'}},
        {'id': 'two', 'transcript':'Caller: I was only asking',
         'timezone':'UTC','call_started_at':'2026-10-22T10:00:00Z',
         'commitment':{'confirmed':False,'final_start':None}},
    ]) + '\n')
    seen = []
    def stub(transcript, timezone, backend=None, reference_time=None):
        seen.append((backend, transcript, timezone, reference_time.isoformat()))
        return Commitment(confirmed=transcript.endswith('10 AM'),
                          final_start='2026-11-03T10:00:00+00:00' if transcript.endswith('10 AM') else None,
                          abstain=False)
    result = compare(path, backends=('rules','openai'),
                     extractor=stub, output=tmp_path/'report.json')
    assert len(seen) == 4
    assert seen[0][1:] == seen[2][1:]
    assert seen[1][1:] == seen[3][1:]
    assert all('commitment' not in str(args) for args in seen)
    assert all(result['backends'][b]['metrics']['extraction_correct'] == 2
               for b in ('rules','openai'))


def test_no_key_fails_explicitly(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    # Prevent any key in a user's .env from altering the deterministic test.
    monkeypatch.setattr('scripts.compare_extractors.load_dotenv', lambda *args, **kwargs: False)
    with pytest.raises(RuntimeError, match='NOT run'):
        compare('datasets/extraction_challenge.jsonl',
                backends=('rules','openai'), output=tmp_path/'absent.json')
    assert not (tmp_path/'absent.json').exists()


def test_rejects_invalid_and_duplicated_gold(tmp_path):
    source = tmp_path/'dupe.jsonl'
    row = {'id':'x', 'transcript':'Caller: hello', 'timezone':'UTC',
           'call_started_at':'2026-10-22T10:00:00Z',
           'commitment':{'confirmed':False,'final_start':None}}
    source.write_text(json.dumps(row)+'\n'+json.dumps(row)+'\n')
    with pytest.raises(ValueError,match='unique'):
        load_cases(source)


def test_comparison_page_only_reads_known_local_report(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        assert client.get('/comparison').status_code == 401
        assert client.get('/comparison',auth=('lane','test-dashboard-password')).status_code == 200


def test_llm_parser_fails_closed_on_inconsistent_json(monkeypatch):
    """Mock the SDK to test validation without sending customer data or API calls."""
    import sys
    import types
    from datetime import datetime, timezone
    from app.extract import extract_commitment
    monkeypatch.setenv('OPENAI_API_KEY','not-a-real-key')
    payload = {'confirmed': True, 'final_start': '2026-11-03T10:00:00',
               'abstain':False, 'evidence':'Booked', 'reason':''}
    def fake_completion(**kwargs):
        # System prompt demands final caller approval; no labels are supplied here.
        assert 'BOTH caller acceptance' in kwargs['messages'][0]['content']
        assert 'commitment' not in kwargs['messages'][1]['content'].lower()
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=json.dumps(payload)))])
    fake = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(
        create=fake_completion)))
    monkeypatch.setitem(sys.modules, 'openai', types.SimpleNamespace(OpenAI=lambda **kwargs:fake))
    result = extract_commitment('Caller: Please book.\nAgent: Confirmed.', 'UTC',
                                backend='openai', reference_time=datetime(2026,10,22,tzinfo=timezone.utc))
    assert result.abstain and not result.confirmed
    payload.update(final_start='2026-11-03T10:00:00+00:00')
    valid = extract_commitment('Caller: Please book.\nAgent: Confirmed.', 'UTC',
                               backend='openai', reference_time=datetime(2026,10,22,tzinfo=timezone.utc))
    assert not valid.abstain and valid.confirmed
    payload.update(confirmed=False)
    inconsistent = extract_commitment('Caller: Was that booked?\nAgent: No.', 'UTC',
                                      backend='openai', reference_time=datetime(2026,10,22,tzinfo=timezone.utc))
    assert inconsistent.abstain and not inconsistent.confirmed


def test_api_failure_never_reported_as_model_accuracy(tmp_path):
    def failing_extractor(transcript, timezone, backend, reference_time):
        return Commitment(reason='Extractor unavailable/invalid: AuthenticationError')
    with pytest.raises(RuntimeError, match='no LLM accuracy report'):
        compare('datasets/extraction_challenge.jsonl', backends=('rules','openai'),
                output=tmp_path/'invalid.json', extractor=failing_extractor)
    assert not (tmp_path/'invalid.json').exists()
