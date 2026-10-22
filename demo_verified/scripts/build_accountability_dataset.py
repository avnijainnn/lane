"""Generate deterministic *synthetic* regression variations; never claim independent accuracy."""
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json
ROOT=Path(__file__).resolve().parents[1]
CATEGORIES=['correct','wrong_time','wrong_contact','wrong_calendar','missing',
            'delayed','availability_only','agent_drift','unauthorized_action']

def build(path=ROOT/'datasets/accountability_regression_72.jsonl'):
    output=[]
    for i in range(8):
        day=datetime(2026,11,4)+timedelta(days=i)
        hour=[9,10,11,14,15,16,12,13][i]
        local=day.replace(hour=hour,tzinfo=ZoneInfo('America/Chicago'))
        correct=local.isoformat()
        wrong=local.replace(hour=hour+1).isoformat()
        display=f"{hour%12 or 12} {'PM' if hour>=12 else 'AM'}"
        for cat in CATEGORIES:
            cid=f'reg_{cat}_{i+1:02d}'
            agent='cedar' if cat=='unauthorized_action' else 'bright'
            if cat=='availability_only':
                transcript=(f'Caller: Is {day.strftime("%B")} {day.day}, 2026 at {display} available?\n'
                            'Agent: It may be free, but I have not booked it.\nCaller: I will think about it.')
                snapshots=[];state='none'
            else:
                transcript=(f'Caller: Please book {day.strftime("%B")} {day.day}, 2026 at {display}.\n'
                            f'Agent: You are booked for {day.strftime("%B")} {day.day} at {display}.')
                if cat=='missing': snapshots=[{'minute':2,'record':None},{'minute':60,'record':None}]
                elif cat=='delayed': snapshots=[{'minute':2,'record':None},{'minute':15,'record':{'startTime':correct,'status':'booked'}}]
                else:
                    rec={'startTime':wrong if cat=='wrong_time' else correct, 'status':'booked'}
                    if cat=='wrong_contact': rec['contactId']='other_contact'
                    if cat=='wrong_calendar':rec['calendarId']='other_calendar'
                    snapshots=[{'minute':2,'record':rec}]
                    if cat in {'wrong_time','wrong_contact','wrong_calendar'}:snapshots.append({'minute':60,'record':rec})
                state='contradicted' if cat in {'wrong_time','wrong_contact','wrong_calendar','missing'} else 'verified'
            output.append({'id':cid,'category':cat,'client':agent,
                           'agent_version':'99' if cat=='agent_drift' else ('7' if agent=='cedar' else '14'),
                           'transcript':transcript,'contact_id':f'contact_{cid}',
                           'appointment_id':f'evt_{cid}', 'calendar_events':snapshots,
                           'expected_booking_state':state,
                           'expected_permission':'not_allowed' if cat=='unauthorized_action' else 'allowed',
                           'expected_policy_flag':cat=='unauthorized_action',
                           'expected_agent_drift':cat=='agent_drift',
                           'expected_consent':'not_established' if cat=='availability_only' else 'accepted'})
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(''.join(json.dumps(x,sort_keys=True)+'\n' for x in output),encoding='utf-8')
    return path

if __name__=='__main__': print(build())
