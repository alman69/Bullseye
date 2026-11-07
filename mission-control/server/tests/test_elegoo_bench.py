"""Bench command safety with a simulated TCP peer; never open a real socket."""

from collections import deque
import json
import socket
from unittest.mock import patch

import pytest

from bullseye.elegoo_bench import BenchConnection, main
from bullseye.elegoo_probe import ReplyTimeout


class BenchSocket:
    def __init__(self, *, complete=True, acknowledge_stop=True, motion_error=None):
        self.complete = complete
        self.acknowledge_stop = acknowledge_stop
        self.motion_error = motion_error
        self.replies = deque()
        self.sent = []
        self.commands = []
        self.now = 0.0
        self.closed = False
        self.moving = False
        self.next_heartbeat = 0.0

    def sendall(self, data):
        self.sent.append(data)
        if data == b"{Heartbeat}":
            return
        command = json.loads(data)
        self.commands.append((self.now, command))
        if command["N"] == 100:
            self.moving = False
            if self.acknowledge_stop:
                self.replies.append(b"{ok}")
        elif command["N"] == 2:
            self.moving = True
            self.next_heartbeat = self.now + 1.0
            self.replies.append(b"{Heartbeat}")
            if self.complete:
                self.replies.append(("{" + command["H"] + "_ok}").encode())

    def recv(self, _):
        if self.moving and self.motion_error is not None:
            raise self.motion_error
        if self.replies:
            return self.replies.popleft()
        if self.moving and self.now >= self.next_heartbeat:
            self.next_heartbeat += 1.0
            return b"{Heartbeat}"
        self.now += 0.05
        raise socket.timeout

    def settimeout(self, _):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def peer():
    fake = BenchSocket()
    with patch("bullseye.elegoo_probe.socket.create_connection", return_value=fake), patch(
        "bullseye.elegoo_bench.time.monotonic", side_effect=lambda: fake.now
    ):
        yield fake


@pytest.mark.parametrize("duration_ms", [200, 1000])
def test_exactly_one_timed_forward_then_stop_and_no_unbounded_commands(peer, capsys, duration_ms):
    args = ["forward", "--wheels-raised", "--verbose"]
    if duration_ms != 200:
        args += ["--duration-ms", str(duration_ms)]
    assert main(args) == 0
    commands = [command for _, command in peer.commands]
    assert [command["N"] for command in commands] == [100, 2, 100, 100]
    assert commands[1]["D1"] == 3
    assert commands[1]["D2"] == 80 and commands[1]["T"] == duration_ms
    assert b"{Heartbeat}" in peer.sent
    assert peer.closed
    assert "software only" in capsys.readouterr().out


@pytest.mark.parametrize("duration_ms", [200, 1000])
def test_completion_timeout_requests_stop_without_waiting_full_five_seconds(peer, capsys, duration_ms):
    peer.complete = False
    assert main(["forward", "--wheels-raised", "--timeout", "5", "--duration-ms", str(duration_ms)]) == 0
    pulse_time = next(at for at, command in peer.commands if command["N"] == 2)
    stop_time = next(at for at, command in peer.commands if at > pulse_time and command["N"] == 100)
    deadline = duration_ms / 1000 + 0.1
    assert deadline <= stop_time - pulse_time <= deadline + 0.051
    if duration_ms == 1000:
        assert peer.sent.count(b"{Heartbeat}") >= 2
    assert "No timed-completion reply" in capsys.readouterr().out
    assert peer.closed


def test_no_motion_when_preflight_stop_is_unacknowledged(peer):
    peer.acknowledge_stop = False
    assert main(["forward", "--wheels-raised", "--timeout", "0.1"]) == 1
    assert all(command["N"] == 100 for _, command in peer.commands)
    assert peer.closed


@pytest.mark.parametrize("error, exit_code", [(OSError("link lost"), 1), (KeyboardInterrupt(), 130)])
def test_motion_read_failure_or_ctrl_c_attempts_stop_without_replaying(peer, error, exit_code):
    peer.motion_error = error
    assert main(["forward", "--wheels-raised"]) == exit_code
    assert [command["N"] for _, command in peer.commands] == [100, 2, 100, 100]
    assert peer.closed


@pytest.mark.parametrize("args", [
    ["forward"],
    ["forward", "--wheels-raised", "--duration-ms", "0"],
    ["forward", "--wheels-raised", "--duration-ms", "1001"],
    ["forward", "--wheels-raised", "--speed", "0"],
    ["forward", "--wheels-raised", "--speed", "101"],
    ["forward", "--wheels-raised", "--timeout", "11"],
])
def test_invalid_or_unconfirmed_request_never_opens_connection(args):
    with patch("bullseye.elegoo_probe.socket.create_connection") as connect:
        with pytest.raises(SystemExit) as error:
            main(args)
        assert error.value.code == 2
        connect.assert_not_called()


def test_post_pulse_stop_timeout_is_reported_as_failure(peer, capsys):
    original_stop = BenchConnection.stop
    calls = [0]

    def stop(connection):
        calls[0] += 1
        if calls[0] == 1:
            return original_stop(connection)
        connection.send({"N": 100, "H": "stop"})
        raise ReplyTimeout("STOP response timeout")

    with patch.object(BenchConnection, "stop", stop):
        assert main(["forward", "--wheels-raised"]) == 1
    assert "switch power OFF" in capsys.readouterr().err
    assert [command["N"] for _, command in peer.commands] == [100, 2, 100, 100]
    assert peer.closed
