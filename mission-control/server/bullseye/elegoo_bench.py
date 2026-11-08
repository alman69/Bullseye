"""One finite, wheels-raised motor test using the original ELEGOO firmware.

Run this file directly with Python 3; no installation or Internet is needed.
The separate elegoo_probe.py diagnostic remains unable to request movement.
"""

import argparse
import json
import sys
import time
import uuid

if __package__:
    from .elegoo_probe import FactoryConnection, ProbeError, ReplyTimeout
else:
    from elegoo_probe import FactoryConnection, ProbeError, ReplyTimeout


DIRECTIONS = {"left": 1, "right": 2, "forward": 3, "backward": 4}
MAX_DURATION_MS = 1000


def validate_pulse(direction, speed, duration_ms):
    if direction not in DIRECTIONS:
        raise ValueError("Unknown direction")
    if type(speed) is not int or not 1 <= speed <= 100:
        raise ValueError("Bench speed must be an integer from 1 to 100 (out of 255)")
    if type(duration_ms) is not int or not 50 <= duration_ms <= MAX_DURATION_MS:
        raise ValueError(f"Bench duration must be an integer from 50 to {MAX_DURATION_MS} milliseconds")


class BenchConnection(FactoryConnection):
    def pulse(self, direction, speed=80, duration_ms=200):
        validate_pulse(direction, speed, duration_ms)
        # Require a STOP acknowledgement before sending any movement. Unlike
        # the diagnostic's sensor query, this must not continue after timeout.
        self.stop()
        print("Preflight STOP acknowledged. Starting ONE bench pulse.", flush=True)
        request_id = "b" + uuid.uuid4().hex[:8]
        command = {"N": 2, "D1": DIRECTIONS[direction], "D2": speed, "T": duration_ms, "H": request_id}
        data = json.dumps(command, separators=(",", ":")).encode("ascii")
        print(f"Requested {direction}: base PWM {speed}/255, firmware duration {duration_ms} ms.", flush=True)
        # Bound the host's reply wait independently of the ordinary five-second
        # response timeout. This is best effort, not a real-time stop guarantee:
        # UART transit and the UNO's blocking sensor reads can delay execution.
        deadline = time.monotonic() + duration_ms / 1000 + 0.1
        completed = False
        try:
            self.sock.settimeout(0.1)
            if self.verbose:
                print(f"TX {data!r}", flush=True)
            self.sock.sendall(data)  # Exactly one N2; never retry a movement.
            try:
                self.receive_matching(
                    lambda reply: reply == "{" + request_id + "_ok}",
                    timeout=max(0, deadline - time.monotonic()),
                )
                completed = True
                print("UNO returned the timed-command completion reply (software only).", flush=True)
            except ReplyTimeout:
                print("No timed-completion reply in the brief wait; requesting STOP now.", flush=True)
        finally:
            # Send STOP on completion, timeout, disconnect, write error, or
            # Ctrl-C. The context manager attempts it again before closing.
            self.stop()
            print("Post-pulse STOP acknowledged (software only; inspect all wheels).", flush=True)
        return completed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", choices=DIRECTIONS)
    parser.add_argument("--wheels-raised", action="store_true", help="Confirm the chassis is securely supported with all four wheels clear")
    parser.add_argument("--speed", type=int, default=80, help="Base PWM 1–100 out of 255; default 80")
    parser.add_argument("--duration-ms", type=int, default=200, help=f"Firmware duration 50–{MAX_DURATION_MS} ms; default 200")
    parser.add_argument("--host", default="192.168.4.1")
    parser.add_argument("--port", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=5, help="Pre/post STOP response timeout; does not extend the motion reply wait")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if not args.wheels_raised:
        parser.error("Support the chassis with all four wheels clear, then supply --wheels-raised")
    try:
        validate_pulse(args.direction, args.speed, args.duration_ms)
    except ValueError as error:
        parser.error(str(error))
    if not 1 <= args.port <= 65535 or not 0.1 <= args.timeout <= 10:
        parser.error("port must be 1–65535 and timeout must be 0.1–10 seconds")

    print("BENCH TEST: keep hands clear and the physical power switch within reach.", flush=True)
    print("One timed command; no automatic retries. Firmware is unchanged.", flush=True)
    try:
        with BenchConnection(args.host, args.port, args.timeout, args.verbose) as connection:
            connection.pulse(args.direction, args.speed, args.duration_ms)
        print("Connection closed. Verify which wheels moved, their direction, and that ALL stopped.")
        return 0
    except KeyboardInterrupt:
        print("Interrupted. STOP was attempted if connected; switch power OFF if any wheel keeps spinning.", file=sys.stderr)
        return 130
    except (OSError, ProbeError) as error:
        print(f"Bench test incomplete: {error}", file=sys.stderr)
        print("Motion may have been requested. Keep wheels raised; switch power OFF if any wheel keeps spinning.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
