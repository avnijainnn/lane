"""Provider wiring tests: no real API requests or paid calls."""
import json
import types
from datetime import datetime, timezone
import pytest
from app.extract import extract_commitment
from scripts.compare_extractors import compare


def test_groq_uses_groq_key_endpoint_schema_and_model(monkeypatch):
    import sys
    monkeypatch.setenv('GROQ_API_KEY', 'fake-groq-key')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('GROQ_EXTRACTION_MODEL', 'openai/gpt-oss-120b')
    clients = []
    def fake_openai(**options):
        clients.append(options)
        def create(**kwargs):
            assert kwargs['model'] == 'openai/gpt-oss-120b'
            assert kwargs['response_format']['json_schema']['strict'] is True
            assert 'Transcript:' in kwargs['messages'][1]['content']
            payload = {'confirmed': False, 'final_start': None,
                       'evidence': 'No booking.', 'abstain': False, 'reason': ''}
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=json.dumps(payload)))])
        return types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create)))
    monkeypatch.setitem(sys.modules, 'openai', types.SimpleNamespace(OpenAI=fake_openai))
    output = extract_commitment('Caller: Any appointments?\nAgent: Not yet booked.',
                                'UTC', backend='groq',
                                reference_time=datetime(2026,10,22,tzinfo=timezone.utc))
    assert not output.abstain and not output.confirmed
    assert clients[0]['api_key']=='fake-groq-key'
    assert clients[0]['base_url']=='https://api.groq.com/openai/v1'


def test_groq_missing_key_does_not_call_api(monkeypatch, tmp_path):
    monkeypatch.delenv('GROQ_API_KEY', raising=False)
    monkeypatch.setattr('scripts.compare_extractors.load_dotenv', lambda *args,**kwargs:False)
    with pytest.raises(RuntimeError, match='GROQ_API_KEY'):
        compare('datasets/extraction_challenge.jsonl',
                backends=('rules','groq'), output=tmp_path/'missing.json')
    assert not (tmp_path/'missing.json').exists()


def test_groq_error_not_counted_as_model_abstention(tmp_path):
    from app.extract import Commitment
    def fake_failure(transcript, timezone, *, backend=None, reference_time=None):
        return Commitment(reason='Extractor unavailable/invalid: RateLimitError')
    with pytest.raises(RuntimeError, match='groq backend failed'):
        compare('datasets/extraction_challenge.jsonl', backends=('rules','groq'),
                extractor=fake_failure, output=tmp_path/'bad.json')
    assert not (tmp_path/'bad.json').exists()


def test_groq_mock_scores_use_same_labels(tmp_path):
    from app.extract import Commitment
    calls=[]
    def fake(transcript, timezone, *, backend=None, reference_time=None):
        calls.append((backend,transcript,timezone,reference_time))
        return Commitment(reason='mock abstention')
    result=compare('datasets/extraction_challenge.jsonl', backends=('rules','groq'),
                   extractor=fake, output=tmp_path/'comparison.json')
    assert len(calls)==16
    for i in range(8):
        assert calls[i][1:]==calls[i+8][1:]
    assert result['backends']['groq']['model']=='openai/gpt-oss-120b'
