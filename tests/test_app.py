# Tests for the command-line front end (datagram/app.py).
# "send" and "receive" are the two-terminal mode; here they run as two threads.
import os
import socket
import threading

import pytest

from datagram.app import main, sha256_file


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_demo_runs_all_three_protocols_and_verifies_each(capsys):
    assert main(["demo", "--size", "6000", "--loss", "0.1", "--seed", "3"]) == 0
    out = capsys.readouterr().out
    assert out.count("PASS (sha256 match)") == 3
    assert "FAIL" not in out


def test_demo_with_a_real_file(tmp_path, capsys):
    f = tmp_path / "in.bin"
    f.write_bytes(os.urandom(3000))
    assert main(["demo", "--protocol", "sr", "--file", str(f), "--loss", "0.1"]) == 0
    assert str(sha256_file(str(f))) in capsys.readouterr().out    # fingerprint is printed


def test_demo_with_every_fault_switched_on(capsys):
    argv = ["demo", "--protocol", "sr", "--size", "8000", "--loss", "0.1", "--corrupt", "0.05",
            "--dup", "0.1", "--reorder", "0.2", "--delay", "0.005", "--jitter", "0.002"]
    assert main(argv) == 0


def test_demo_adaptive_with_reset(capsys):
    assert main(["demo", "--protocol", "sr", "--size", "8000", "--loss", "0.1",
                 "--adaptive", "--reset"]) == 0


@pytest.mark.parametrize("protocol", ["sw", "gbn", "sr"])
def test_adaptive_and_reset_work_for_every_protocol(protocol):
    assert main(["demo", "--protocol", protocol, "--size", "6000", "--loss", "0.1",
                 "--adaptive", "--reset"]) == 0


def test_reset_without_adaptive_is_refused():
    with pytest.raises(SystemExit):
        main(["demo", "--protocol", "sr", "--reset"])


@pytest.mark.parametrize("protocol", ["sw", "gbn", "sr"])
def test_send_and_receive_across_two_ports(tmp_path, protocol):
    src, dst = tmp_path / "s.bin", tmp_path / "r.bin"
    src.write_bytes(os.urandom(4000))
    sender_port, receiver_port = free_port(), free_port()
    common = ["--protocol", protocol, "--loss", "0.1"]
    rx = threading.Thread(target=main, args=(
        ["receive", "--port", str(receiver_port), "--peer", f"127.0.0.1:{sender_port}",
         "--out", str(dst)] + common,))
    rx.start()
    rc = main(["send", "--port", str(sender_port), "--peer", f"127.0.0.1:{receiver_port}",
               "--file", str(src)] + common)
    rx.join(timeout=30)
    assert rc == 0
    assert not rx.is_alive()
    assert sha256_file(str(src)) == sha256_file(str(dst))


def test_send_reports_a_missing_file_instead_of_crashing(tmp_path, capsys):
    rc = main(["send", "--port", str(free_port()), "--peer", "127.0.0.1:9",
               "--file", str(tmp_path / "nope.jpg")])
    assert rc == 2
    assert "file not found" in capsys.readouterr().out


def test_demo_reports_a_missing_file(tmp_path, capsys):
    assert main(["demo", "--file", str(tmp_path / "nope.jpg")]) == 2
    assert "file not found" in capsys.readouterr().out