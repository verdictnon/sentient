"""Streaming v1 sink and reporting over the existing P0 analytics, not a new engine."""

import json
import sqlite3
from pathlib import Path

from app.adapter import ContractAdapter
from app.config import AnalyticsConfig, ReplayConfig
from app.contracts import ContractValidator
from app.geometry import Line, Polygon
from app.storage import SQLiteStore


def analytics_config(raw):
    line=raw['line']
    return ReplayConfig(AnalyticsConfig(Line(tuple(line['start']),tuple(line['end']),line['hysteresis_px']),
                                       Polygon(tuple(tuple(p) for p in raw['zone'])),raw['max_gap_ms']))


def summary(store,read=0,inserted=0,events_before=0):
    """SQL reductions of P0's derived events keep final reporting bounded."""
    db=store.connection
    counts=dict(db.execute('SELECT kind,COUNT(*) FROM events GROUP BY kind').fetchall())
    visits=db.execute("SELECT COALESCE(json_extract(payload,'$.complete'),0) AS complete,COUNT(*),"
                      "COALESCE(SUM(json_extract(payload,'$.observed_dwell_ms')),0) "
                      "FROM events WHERE kind='zone.visit_closed' GROUP BY complete").fetchall()
    values={int(row[0]):(row[1],row[2]) for row in visits}
    complete,dwell=values.get(1,(0,0));censored,partial=values.get(0,(0,0))
    records=db.execute('SELECT COUNT(*) FROM inputs').fetchone()[0]
    event_count=sum(counts.values())
    return {'schema_version':'1.0','records_read':read,'records_inserted':inserted,'records_duplicate':read-inserted,
            'database_records':records,'events_total':event_count,'events_added':event_count-events_before,
            'inward_crossings':counts.get('entrance.inward',0),'outward_crossings':counts.get('entrance.outward',0),
            'complete_visits':complete,'censored_visits':censored,'complete_observed_dwell_ms':dwell,
            'censored_observed_dwell_ms':partial,'complete_observed_dwell_seconds':dwell/1000,
            'confirmed_physical_actions':0,'stored_action_results':0,
            'queue_service_inference':'disabled: person geometry cannot establish service start',
            'queue_completed_visits':0,'queue_abandoned_visits':0,'queue_censored_visits':0,
            'queue_completed_waits':0,'queue_completed_waiting_ms':0,'queue_open_waits':0,'queue_open_service_visits':0}


class StreamingSink:
    def __init__(self,path,config):
        self.store=SQLiteStore(path)
        try:
            self.adapter=ContractAdapter(self.store,analytics_config(config),ContractValidator())
            with self.store.transaction():
                self.store.connection.execute("CREATE INDEX IF NOT EXISTS p2_frame ON inputs(json_extract(payload,'$.frame_index'),track)")
                self.store.connection.execute('CREATE TABLE IF NOT EXISTS p2_frame_info(frame_index INTEGER PRIMARY KEY,detector_ran INTEGER NOT NULL,detector_candidates INTEGER NOT NULL)')
        except BaseException:
            self.store.close();raise
        self.read=self.inserted=0
        self.before=self.store.connection.execute('SELECT COUNT(*) FROM events').fetchone()[0]

    def write_frame(self,records,frame_info=None):
        # One decoded frame and its heartbeat commit atomically.
        added=0
        with self.store.transaction():
            for record in records:added+=self.adapter.ingest(record)
            if frame_info is not None:
                self.store.connection.execute('INSERT INTO p2_frame_info VALUES (?,?,?)',frame_info)
        self.read+=len(records);self.inserted+=added

    def finish(self):
        self.adapter.engine.finish()
        return summary(self.store,self.read,self.inserted,self.before)

    def close(self):self.store.close()


def replay_output(path,db_path,config):
    sink=StreamingSink(db_path,config)
    try:
        with Path(path).open(encoding='utf-8') as stream:
            for text in stream:
                if text.strip():sink.write_frame([json.loads(text)])
        return sink.finish()
    finally:sink.close()


def read_database(path):
    if not Path(path).is_file():raise FileNotFoundError('Tracking database is unavailable')
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
    db.row_factory=sqlite3.Row
    return db
