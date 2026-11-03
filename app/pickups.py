"""Demo pickup predictions from actual model scores; never human labels."""
from collections import defaultdict
from app.storage import stable_id


def merge_pickups(records, policy, analysis_id):
    if not policy:return []
    threshold=float(policy['threshold']);gap=float(policy.get('merge_gap_ms',500))
    groups=defaultdict(list)
    for row in records:
        scores=row.get('scores')
        if row.get('type')!='action.scored' or not scores or scores.get('take',0)<threshold:continue
        key=tuple(row[k] for k in ('store_id','camera_id','stream_session_id','local_track_id'))
        groups[key].append(row)
    events=[]
    for key,rows in sorted(groups.items()):
        current=None
        for row in sorted(rows,key=lambda x:(x['start_media_ms'],x['end_media_ms'])):
            if current is None or row['start_media_ms']>current['end_media_ms']+gap:
                current={'track_key':list(key),'local_track_id':key[-1],
                    'start_media_ms':row['start_media_ms'],'end_media_ms':row['end_media_ms'],
                    'score':row['scores']['take'],'score_record_ids':[row['record_id']],
                    'model_id':row['model_id'],'action':'take','status':'model_prediction','human_verified':False}
                events.append(current)
            else:
                current['end_media_ms']=max(current['end_media_ms'],row['end_media_ms'])
                current['score']=max(current['score'],row['scores']['take'])
                current['score_record_ids'].append(row['record_id'])
    for event in events:
        event['id']='pickup:'+stable_id([analysis_id,event['track_key'],event['score_record_ids'],threshold])
    return sorted(events,key=lambda x:x['start_media_ms'])
