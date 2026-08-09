import csv
import time
from datetime import datetime, timedelta

from corsair_control.core.recorder import Recorder, export_history
from corsair_control.core.sensors import Sensor, SensorHub
from tests.test_alarms import channel, snapshot


def test_recording_is_off_by_default(tmp_path):
    recorder = Recorder(tmp_path)
    assert recorder.record(snapshot([channel()])) is False
    assert recorder.files() == []


def test_records_sensors_and_channels(tmp_path):
    recorder = Recorder(tmp_path, enabled=True, interval=0.0)
    assert recorder.record(snapshot([channel()], sensors={"cpu": 55.0}), {"cpu": "CPU"}) is True

    rows = list(csv.DictReader(recorder.path_for().open(encoding="utf-8")))
    kinds = {row["kind"] for row in rows}
    assert kinds == {"sensor", "speed", "duty"}
    sensor_row = next(row for row in rows if row["kind"] == "sensor")
    assert sensor_row["label"] == "CPU"
    assert sensor_row["value"] == "55.00"


def test_interval_throttles_writes(tmp_path):
    recorder = Recorder(tmp_path, enabled=True, interval=60.0)
    assert recorder.record(snapshot([channel()])) is True
    assert recorder.record(snapshot([channel()])) is False


def test_header_is_written_once(tmp_path):
    recorder = Recorder(tmp_path, enabled=True, interval=0.0)
    recorder.record(snapshot([channel()]))
    recorder.record(snapshot([channel()]))
    lines = recorder.path_for().read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("timestamp")
    assert sum(1 for line in lines if line.startswith("timestamp")) == 1


def test_prune_removes_old_files(tmp_path):
    recorder = Recorder(tmp_path, enabled=True, retention_days=2)
    old = recorder.path_for(datetime.now() - timedelta(days=10))
    recent = recorder.path_for(datetime.now())
    for path in (old, recent):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("timestamp\n", encoding="utf-8")

    assert recorder.prune() == 1
    assert not old.exists()
    assert recent.exists()


def test_unwritable_directory_does_not_raise(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    recorder = Recorder(blocked / "sub", enabled=True, interval=0.0)
    assert recorder.record(snapshot([channel()])) is False


def test_export_writes_the_in_memory_history(tmp_path):
    hub = SensorHub()
    hub.register([Sensor("cpu", "CPU", "cpu", lambda: 42.0)])
    hub.poll()
    hub.poll()

    target = tmp_path / "export.csv"
    assert export_history(hub, target) == 2

    rows = list(csv.DictReader(target.open(encoding="utf-8")))
    assert rows[0]["sensor_id"] == "cpu"
    assert rows[0]["value"] == "42.00"
    # The export must carry wall-clock timestamps, not monotonic ones.
    assert datetime.fromisoformat(rows[0]["iso"]).year == datetime.now().year


def test_export_of_an_empty_hub(tmp_path):
    target = tmp_path / "empty.csv"
    assert export_history(SensorHub(), target) == 0
    assert target.exists()


def test_recorder_uses_a_file_per_day(tmp_path):
    recorder = Recorder(tmp_path, enabled=True, interval=0.0)
    today = recorder.path_for(datetime(2026, 8, 9))
    yesterday = recorder.path_for(datetime(2026, 8, 8))
    assert today != yesterday
    assert today.name == "history-2026-08-09.csv"
    assert time.time() > 0
