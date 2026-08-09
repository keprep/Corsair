"""Forced driver bindings for devices liquidctl does not recognise."""

from typing import Any

import pytest

from corsair_control.core import experimental
from corsair_control.core.experimental import (
    PROTOCOLS,
    Binding,
    ProbeResult,
    looks_like_a_cooler,
    parse_bindings,
    probe,
)


# ----------------------------------------------------------------------
# parsing
# ----------------------------------------------------------------------
def test_parse_full_spec():
    binding = Binding.parse("1b1c:0c40=commander-core-aio")
    assert binding.vendor_id == 0x1B1C
    assert binding.product_id == 0x0C40
    assert binding.protocol == "commander-core-aio"
    assert str(binding) == "1b1c:0c40=commander-core-aio"


def test_vendor_defaults_to_corsair():
    assert Binding.parse("0c40=commander-core-aio").vendor_id == 0x1B1C


def test_parse_is_case_insensitive_and_tolerates_spaces():
    assert Binding.parse("  1B1C:0C40 = Commander-Core-AIO ").product_id == 0x0C40


def test_unknown_protocol_is_rejected():
    with pytest.raises(ValueError, match="unknown protocol"):
        Binding.parse("1b1c:0c40=telepathy")


def test_malformed_spec_is_rejected():
    for text in ("", "nonsense", "1b1c:0c40", "=commander-core-aio", "zzzz=commander-core-aio"):
        with pytest.raises(ValueError):
            Binding.parse(text)


def test_parse_many_collects_errors_instead_of_raising():
    bindings, errors = parse_bindings(["0c40=commander-core-aio", "broken", "0c2a=nope"])
    assert [b.product_id for b in bindings] == [0x0C40]
    assert len(errors) == 2


def test_every_protocol_names_a_real_liquidctl_driver():
    for protocol in PROTOCOLS.values():
        cls = experimental._driver_class(protocol)
        assert hasattr(cls, "set_fixed_speed")
        assert hasattr(cls, "get_status")


# ----------------------------------------------------------------------
# the safety gate
# ----------------------------------------------------------------------
def test_a_cooler_is_recognised():
    ok, _reason, speeds, temps = looks_like_a_cooler(
        [("Pump speed", 2400, "rpm"), ("Water temperature", 31.0, "°C")]
    )
    assert ok and speeds == 1 and temps == 1


def test_a_keyboard_is_not_mistaken_for_a_cooler():
    """The gate that stops fan packets being written to a Corsair keyboard."""
    ok, reason, _s, _t = looks_like_a_cooler(
        [("Firmware version", "1.2.3", ""), ("Layout", "DE", "")]
    )
    assert not ok
    assert "no speed and no temperature" in reason


def test_empty_status_is_not_a_cooler():
    assert looks_like_a_cooler([])[0] is False
    assert looks_like_a_cooler(None)[0] is False


def test_temperature_only_device_still_counts():
    # A Commander Pro with probes but every fan unplugged.
    assert looks_like_a_cooler([("Temperature 1", 30.0, "°C")])[0] is True


def test_garbage_entries_are_ignored():
    assert looks_like_a_cooler([("x",), None, ("Fan speed 1", 900, "rpm")])[0] is True


# ----------------------------------------------------------------------
# probing
# ----------------------------------------------------------------------
class _Backend:
    def __init__(self, status, fail_on=None):
        self._status = status
        self._fail_on = fail_on
        self.connected = False
        self.disconnected = False

    def connect(self, **_: Any):
        if self._fail_on == "connect":
            raise OSError("busy")
        self.connected = True

    def disconnect(self, **_: Any):
        self.disconnected = True

    def initialize(self, **_: Any):
        if self._fail_on == "initialize":
            raise OSError("rejected")
        return []

    def get_status(self, **_: Any):
        return self._status


def test_probe_accepts_a_plausible_device(monkeypatch):
    backend = _Backend([("Pump speed", 2400, "rpm")])
    monkeypatch.setattr(experimental, "build_backends", lambda b: [backend])

    found, outcome = probe(Binding.parse("0c40=commander-core-aio"))
    assert found is backend
    assert outcome.ok and outcome.speeds == 1
    assert backend.connected


def test_probe_rejects_and_closes_an_implausible_device(monkeypatch):
    backend = _Backend([("Firmware version", "1.0", "")])
    monkeypatch.setattr(experimental, "build_backends", lambda b: [backend])

    found, outcome = probe(Binding.parse("0c40=commander-core-aio"))
    assert found is None
    assert not outcome.ok
    # It must be released again, not left open.
    assert backend.disconnected


def test_probe_tries_every_hid_interface(monkeypatch):
    silent = _Backend([], fail_on="initialize")
    good = _Backend([("Fan speed 1", 900, "rpm")])
    monkeypatch.setattr(experimental, "build_backends", lambda b: [silent, good])

    found, outcome = probe(Binding.parse("0c40=commander-core-aio"))
    assert found is good and outcome.ok


def test_probe_without_any_device(monkeypatch):
    monkeypatch.setattr(experimental, "build_backends", lambda b: [])
    found, outcome = probe(Binding.parse("0c40=commander-core-aio"))
    assert found is None
    assert "no device found" in outcome.reason


def test_probe_reports_a_rejected_protocol(monkeypatch):
    monkeypatch.setattr(
        experimental, "build_backends", lambda b: [_Backend([], fail_on="initialize")]
    )
    found, outcome = probe(Binding.parse("0c40=commander-core-aio"))
    assert found is None
    assert "rejected the protocol" in outcome.reason


def test_probe_result_describes_itself():
    assert "2 speed" in ProbeResult(True, "", [], 2, 1).describe()
    assert ProbeResult(False, "nope").describe() == "nope"


# ----------------------------------------------------------------------
# discovery integration
# ----------------------------------------------------------------------
def test_bound_device_joins_discovery(monkeypatch, tmp_path):
    from corsair_control.core import manager
    from tests.test_commander_core import _CommanderCoreLike

    backend = _CommanderCoreLike(has_pump=True, description="forced", address="x")
    monkeypatch.setattr(manager, "probe", lambda b: (backend, ProbeResult(True, "", [], 7, 1)))

    result = manager.DiscoveryResult()
    devices = manager._apply_bindings(["0c40=commander-core-aio"], result)

    assert len(devices) == 1
    assert devices[0].experimental is True
    assert {c.channel_id for c in devices[0].channels} >= {"pump", "fan1"}


def test_failed_binding_is_reported_not_silent(monkeypatch):
    from corsair_control.core import manager

    monkeypatch.setattr(manager, "probe", lambda b: (None, ProbeResult(False, "no answer")))
    result = manager.DiscoveryResult()

    assert manager._apply_bindings(["0c40=commander-core-aio"], result) == []
    assert any("did not work" in e for e in result.errors)


def test_binding_is_skipped_when_a_real_driver_already_claims_the_id(monkeypatch):
    from corsair_control.core import manager

    called = []
    monkeypatch.setattr(manager, "probe", lambda b: called.append(b) or (None, ProbeResult(False, "")))

    result = manager.DiscoveryResult()
    result.devices.append(type("D", (), {"_dev": type("B", (), {"product_id": 0x0C40})()})())
    manager._apply_bindings(["0c40=commander-core-aio"], result)

    assert called == []


def test_no_bindings_means_no_work(monkeypatch):
    from corsair_control.core import manager

    result = manager.DiscoveryResult()
    assert manager._apply_bindings(None, result) == []
    assert manager._apply_bindings([], result) == []
    assert result.errors == []
