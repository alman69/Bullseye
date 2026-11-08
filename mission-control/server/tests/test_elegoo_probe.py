"""Protocol and cleanup checks without opening a network or serial port."""

from collections import deque
import json
import socket
from unittest.mock import patch

import pytest

from bullseye.elegoo_probe import FactoryConnection, FrameParser, ProbeError, ReplyTimeout, main


class FakeSocket:
    def __init__(self, replies):
        self.replies = deque(replies)
        self.sent = []
        self.closed = False

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, _):
        return self.replies.popleft() if self.replies else b""

    def settimeout(self, _):
        pass

    def close(self):
        self.closed = True


def connect(fake):
    return patch("bullseye.elegoo_probe.socket.create_connection", return_value=fake)


def test_split_and_combined_frames_ignore_serial_noise():
    parser = FrameParser()
    assert parser.feed(b"\r\nboot log{Heart") == []
    assert parser.feed(b"beat}{p123_27}{ok}\r\n") == ["{Heartbeat}", "{p123_27}", "{ok}"]
    assert parser.feed(b"{broken{ok}") == ["{ok}"]
    with pytest.raises(ProbeError, match="Oversized"):
        parser.feed(b"{" + b"a" * 1024)


def test_probe_correlates_reply_echoes_heartbeat_and_only_sends_safe_commands(capsys):
    fake = FakeSocket([b"{Heart", b"beat}{ok}", b"{old_42}{p12345678_", b"27}{Heartbeat}"])
    with connect(fake), patch("bullseye.elegoo_probe.uuid.uuid4") as request_id:
        request_id.return_value.hex = "12345678rest"
        assert main(["probe"]) == 0
    output = capsys.readouterr().out
    assert "Distance: 27 cm" in output
    assert "heartbeat messages echoed: 2" in output
    assert fake.sent.count(b"{Heartbeat}") == 2
    commands = [json.loads(frame) for frame in fake.sent if frame != b"{Heartbeat}"]
    assert [command["N"] for command in commands] == [100, 21, 100]
    assert commands[1] == {"N": 21, "D1": 2, "H": "p12345678"}
    assert fake.closed


@pytest.mark.parametrize("reading, expected", [("0", "INVALID / no echo"), ("150", "vendor-clamped")])
def test_zero_and_clamped_distances_are_not_reported_as_clear(reading, expected, capsys):
    fake = FakeSocket([b"{ok}", ("{p12345678_" + reading + "}").encode()])
    with connect(fake), patch("bullseye.elegoo_probe.uuid.uuid4") as request_id:
        request_id.return_value.hex = "12345678rest"
        assert main([]) == 0
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize("reply", [b"{p12345678_true}", b"{p12345678_999}", b"{wrong_15}", b""])
def test_bad_or_uncorrelated_reply_fails_and_still_attempts_stop(reply):
    fake = FakeSocket([b"{ok}", reply])
    with connect(fake), patch("bullseye.elegoo_probe.uuid.uuid4") as request_id:
        request_id.return_value.hex = "12345678rest"
        assert main([]) == 1
    assert json.loads(fake.sent[-1])["N"] == 100
    assert fake.closed


def test_heartbeat_is_not_a_uno_acknowledgement():
    fake = FakeSocket([b"{Heartbeat}", b""])
    with connect(fake):
        assert main(["stop"]) == 1
    assert b"{Heartbeat}" in fake.sent
    assert fake.closed


def test_heartbeat_only_failure_still_reports_received_traffic(capsys):
    fake = FakeSocket([b"{Heart", b"beat}", b""])
    with connect(fake):
        assert main(["probe", "--verbose"]) == 1
    output = capsys.readouterr().out
    assert "RX b'{Heart'" in output
    assert "heartbeat messages echoed: 1" in output
    assert "no UNO reply was observed" in output
    assert fake.closed


def test_probe_still_asks_sensor_after_missing_stop_ack(capsys):
    fake = FakeSocket([b"{p12345678_30}"])
    with connect(fake), patch.object(FactoryConnection, "stop", side_effect=ReplyTimeout), patch(
        "bullseye.elegoo_probe.uuid.uuid4"
    ) as request_id:
        request_id.return_value.hex = "12345678rest"
        assert main(["probe"]) == 0
    output = capsys.readouterr().out
    assert "Distance: 30 cm" in output
    assert "STOP acknowledgement is still UNVERIFIED" in output
    assert [json.loads(frame)["N"] for frame in fake.sent] == [21, 100]


def test_unexpected_stop_reply_is_visible_and_does_not_claim_acknowledgement(capsys):
    fake = FakeSocket([b"{stop_ok}", b""])
    with connect(fake):
        assert main(["stop", "--verbose"]) == 1
    output = capsys.readouterr().out
    assert "Unmatched replies: ['{stop_ok}']" in output
    assert "UNO returned {ok}" not in output


def test_closed_socket_is_not_followed_by_another_sensor_query():
    fake = FakeSocket([b""])
    with connect(fake):
        assert main(["probe"]) == 1
    assert [json.loads(frame)["N"] for frame in fake.sent] == [100, 100]


def test_probe_continues_sensor_query_on_real_stop_ack_timeout(capsys):
    fake = FakeSocket([b"{Heartbeat}"])
    fake_clock = [0.0]

    def receive(_):
        if any(b'"N":21' in command for command in fake.sent):
            return b"{p12345678_30}"
        if fake.replies:
            return fake.replies.popleft()
        fake_clock[0] += 0.2
        raise socket.timeout

    with connect(fake), patch.object(fake, "recv", side_effect=receive), patch(
        "bullseye.elegoo_probe.time.monotonic", side_effect=lambda: fake_clock[0]
    ), patch("bullseye.elegoo_probe.uuid.uuid4") as request_id:
        request_id.return_value.hex = "12345678rest"
        assert main(["probe", "--timeout", "0.1"]) == 0
    assert "STOP was sent but not acknowledged" in capsys.readouterr().out
    assert [json.loads(frame)["N"] for frame in fake.sent if frame != b"{Heartbeat}"] == [100, 21, 100]
    assert fake.closed


def test_response_deadline_is_bounded_even_if_socket_keeps_timing_out():
    fake = FakeSocket([])
    with connect(fake), patch.object(fake, "recv", side_effect=socket.timeout), patch(
        "bullseye.elegoo_probe.time.monotonic", side_effect=[0, 0.05, 0.15]
    ):
        with FactoryConnection("192.168.4.1", 100, 0.1) as connection:
            with pytest.raises(ProbeError, match="timeout"):
                connection.stop()
    assert json.loads(fake.sent[-1])["N"] == 100
    assert fake.closed


def test_motion_commands_are_rejected_before_any_send():
    fake = FakeSocket([])
    with connect(fake):
        with FactoryConnection("192.168.4.1", 100, 3) as connection:
            with pytest.raises(ValueError, match="only permits"):
                connection.send({"N": 3, "D1": 3, "D2": 100})
    assert [json.loads(frame)["N"] for frame in fake.sent] == [100]
