import csv
import json
from pathlib import Path
import pytest
from scripts.public_benchmark import prepare,template,reviewed


def test_sgd_convert_and_no_invented_labels(tmp_path):
    sample=[{'dialogue_id':'1_01','services':['Services_1'],
             'turns':[{'speaker':'USER','utterance':'Book an appointment tomorrow'},
                      {'speaker':'SYSTEM','utterance':'Done, booked.'}]}]
    path=tmp_path/'train_dialogues_001.json';path.write_text(json.dumps(sample))
    out=tmp_path/'out.jsonl'
    result=prepare('sgd',[path],out)
    assert len(result)==1
    assert 'commitment' not in result[0]
    assert result[0]['annotation_status']=='UNLABELED'
    assert 'Caller:' in result[0]['transcript']


def test_taskmaster_convert(tmp_path):
    sample=[{'conversationId':'test','instructionId':'restaurant-reservation',
             'utterances':[{'speaker':'USER','text':'Reserve a table'},
                           {'speaker':'ASSISTANT','text':'I can help'}]}]
    p=tmp_path/'woz-dialogs.json';p.write_text(json.dumps(sample))
    result=prepare('taskmaster1',[p],tmp_path/'a.jsonl')
    assert result[0]['id'].startswith('taskmaster1_')
    assert 'Agent: I can help' in result[0]['transcript']


def test_abcd_convert(tmp_path):
    sample=[{'convo_id':3,'scenario':{'flow':'return_size'},
             'original':[['customer','I need to reschedule'],['agent','Sure'],['action','CRM updated']]}]
    p=tmp_path/'abcd_sample.json';p.write_text(json.dumps(sample))
    result=prepare('abcd',[p],tmp_path/'b.jsonl')
    assert 'Tool: CRM updated' in result[0]['transcript']


def test_review_requires_explicit_human_labels(tmp_path):
    sample=[{'dialogue_id':'1_01','services':['Services_1'],
             'turns':[{'speaker':'USER','utterance':'Book an appointment tomorrow'}]}]
    p=tmp_path/'train_dialogues_001.json';p.write_text(json.dumps(sample))
    candidates=tmp_path/'c.jsonl';prepare('sgd',[p],candidates)
    csv_path=tmp_path/'review.csv';template(candidates,csv_path)
    with pytest.raises(ValueError,match='No reviewed labels'):reviewed(csv_path,tmp_path/'labels.jsonl')
    with csv_path.open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    rows[0].update({'timezone':'America/Chicago','call_started_at':'2026-10-22T14:00:00Z',
                    'confirmed':'no','reviewer':'tester'})
    with csv_path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
    assert reviewed(csv_path,tmp_path/'labels.jsonl')[0]['commitment']=={'confirmed':False,'final_start':None}
