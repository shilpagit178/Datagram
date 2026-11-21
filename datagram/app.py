"""Command-line front end for Datagram.

    python -m datagram.app demo                         # one command, all 3 protocols
    python -m datagram.app demo --protocol sr --loss 0.2 --file photo.jpg
    python -m datagram.app receive --port 9001 --peer 127.0.0.1:9000 --out got.bin
    python -m datagram.app send    --port 9000 --peer 127.0.0.1:9001 --file photo.jpg

The demo runs a sender and a receiver on localhost, pushes a file through the
channel emulator (loss, corruption, ...), and compares SHA-256 of the file that
went in with the file that came out. PASS means every byte arrived, in order.
"""
import argparse
import os
import socket
import sys
import tempfile
import threading

from datagram.channel import Channel
from datagram.protocols.gbn import GoBackN
from datagram.protocols.sr import SelectiveRepeat
from datagram.protocols.stop_wait import StopAndWait
from datagram.rto import RTOEstimator
import hashlib

NAMES = {"sw": "Stop-and-Wait", "gbn": "Go-Back-N", "sr": "Selective Repeat"}


def status(mark, message):
    """Print a compact, terminal-friendly lifecycle message."""
    print(f"[{mark}] {message}")


def sha256_file(path):
    """Return the SHA-256 fingerprint of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def missing_file(path):
    """Friendly message instead of a traceback when the input file isn't found."""
    print(f"error: file not found: {path!r}")
    print(f"       looking in: {os.getcwd()}")
    print("       check the spelling and the extension (Windows can hide it, e.g. photo.jpg.jpg),")
    print("       or give the full path in quotes.")
    return 2


def make_channel(sock, peer, a, seed):
    return Channel(sock, peer, seed=seed, loss=a.loss, corrupt=a.corrupt,
                   dup=a.dup, reorder=a.reorder, reorder_delay=a.reorder_delay,
                   delay=a.delay, jitter=a.jitter)


def build(name, sock, channel, a):
    """Same fixed timeout for every protocol unless --adaptive is given."""
    if a.adaptive:
        est = RTOEstimator(initial_rto=a.rto, min_rto=0.05)
    else:
        est = RTOEstimator(fixed=a.rto)
    common = dict(max_retries=a.retries, idle_timeout=a.timeout, linger=a.linger)
    if name == "sw":
        return StopAndWait(sock, channel, rto=a.rto, est=est,
                           reset_on_retx_ack=a.reset, **common)
    if name == "gbn":
        return GoBackN(sock, channel, window=a.window, rto=a.rto, est=est,
                       reset_on_retx_ack=a.reset, **common)
    return SelectiveRepeat(sock, channel, window=a.window, rto=a.rto, est=est,
                           reset_on_retx_ack=a.reset, **common)


def parse_peer(text):
    host, _, port = text.rpartition(":")
    return host or "127.0.0.1", int(port)


# ------------------------------------------------------------------ demo
def run_one(name, src, dst, a):
    """One real transfer over two localhost UDP sockets. Returns a result dict."""
    s_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    r_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s_sock.bind(("127.0.0.1", 0))
        r_sock.bind(("127.0.0.1", 0))
        sender = build(name, s_sock, make_channel(s_sock, r_sock.getsockname(), a, a.seed), a)
        receiver = build(name, r_sock, make_channel(r_sock, s_sock.getsockname(), a, a.seed + 1000), a)
        out = {}

        def run_receiver():
            try:
                out["rx"] = receiver.receive_file(dst)
            except Exception as e:           # report it, don't hang the demo
                out["err"] = e

        t = threading.Thread(target=run_receiver, daemon=True)
        t.start()
        try:
            tx = sender.send_file(src)
        except Exception as e:
            return {"name": name, "ok": False, "error": repr(e)}
        t.join(timeout=30)
    finally:
        s_sock.close()
        r_sock.close()
    if "err" in out:
        return {"name": name, "ok": False, "error": repr(out["err"])}
    ok = os.path.exists(dst) and sha256_file(src) == sha256_file(dst)
    return {"name": name, "ok": ok, "tx": tx, "hash": sha256_file(dst) if os.path.exists(dst) else ""}


def cmd_demo(a):
    if a.file and not os.path.isfile(a.file):
        return missing_file(a.file)
    names = list(NAMES) if a.protocol == "all" else [a.protocol]
    with tempfile.TemporaryDirectory() as tmp:
        src = a.file or os.path.join(tmp, "source.bin")
        if not a.file:
            with open(src, "wb") as f:
                f.write(os.urandom(a.size))
        size = os.path.getsize(src)
        mode = "adaptive RTO" if a.adaptive else f"fixed RTO {a.rto}s"
        print(f"Sending {size:,} bytes over localhost UDP | loss {a.loss:.0%}, corrupt {a.corrupt:.0%}, "
              f"dup {a.dup:.0%}, reorder {a.reorder:.0%}, delay {a.delay * 1000:.0f} ms | "
              f"window {a.window} | {mode} | seed {a.seed}")
        print(f"source sha256: {sha256_file(src)}\n")
        print(f"{'protocol':<17}{'packets sent':>13}{'retransmits':>13}{'seconds':>9}{'KB/s':>9}   result")
        all_ok = True
        for name in names:
            dst = os.path.join(tmp, f"received_{name}.bin")
            r = run_one(name, src, dst, a)
            if "tx" in r:
                tx = r["tx"]
                kbs = size / 1024 / tx["elapsed_s"] if tx["elapsed_s"] > 0 else 0
                verdict = "PASS (sha256 match)" if r["ok"] else "FAIL (sha256 mismatch)"
                print(f"{NAMES[name]:<17}{tx['packets_sent']:>13}{tx['retransmissions']:>13}"
                      f"{tx['elapsed_s']:>9.2f}{kbs:>9.1f}   {verdict}")
            else:
                print(f"{NAMES[name]:<17}{'-':>13}{'-':>13}{'-':>9}{'-':>9}   FAIL ({r['error']})")
            all_ok = all_ok and r["ok"]
    print("\nAll transfers verified." if all_ok else "\nSOME TRANSFERS FAILED.")
    return 0 if all_ok else 1


# --------------------------------------------------------- two terminals
def cmd_receive(a):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((a.host, a.port))
    peer = parse_peer(a.peer)
    proto = build(a.protocol, sock, make_channel(sock, peer, a, a.seed + 1000), a)
    status("~", f"{NAMES[a.protocol]} listening on {a.host}:{a.port}")
    print(f"    output: {a.out}")
    try:
        stats = proto.receive_file(a.out)
    except (OSError, TimeoutError, ValueError) as e:
        status("x", f"receive failed: {e}")
        return 1
    finally:
        sock.close()
    status("+", f"received {stats['bytes_delivered']:,} bytes in {stats['elapsed_s']:.2f}s")
    print(f"    sha256: {sha256_file(a.out)}")
    return 0


def cmd_send(a):
    if not os.path.isfile(a.file):
        return missing_file(a.file)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((a.host, a.port))
    peer = parse_peer(a.peer)
    proto = build(a.protocol, sock, make_channel(sock, peer, a, a.seed), a)
    status(">", f"{NAMES[a.protocol]} sending {a.file}")
    print(f"    peer: {a.peer}")
    print(f"    source sha256: {sha256_file(a.file)}")
    try:
        stats = proto.send_file(a.file)
    except (OSError, TimeoutError, ValueError) as e:
        status("x", f"send failed: {e}")
        return 1
    finally:
        sock.close()
    status("+", f"sent {stats['bytes_delivered']:,} bytes in {stats['elapsed_s']:.2f}s")
    print(f"    packets: {stats['packets_sent']} | retransmissions: {stats['retransmissions']}")
    return 0


# ------------------------------------------------------------------ main
def build_parser():
    p = argparse.ArgumentParser(prog="python -m datagram.app",
                                description="Reliable file transfer over UDP (Datagram).")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, choices=("sw", "gbn", "sr"), default="sr"):
        sp.add_argument("--protocol", choices=list(choices), default=default,
                        help="sw = Stop-and-Wait, gbn = Go-Back-N, sr = Selective Repeat")
        sp.add_argument("--window", type=int, default=8)
        sp.add_argument("--rto", type=float, default=0.1, help="timeout in seconds (fixed unless --adaptive)")
        sp.add_argument("--adaptive", action="store_true",
                        help="adaptive RTO. Can be slow at high loss: try --reset too")
        sp.add_argument("--reset", action="store_true",
                        help="with --adaptive: clear the backoff once a resent packet is ACKed")
        sp.add_argument("--retries", type=int, default=200)
        sp.add_argument("--timeout", type=float, default=30.0, help="receiver gives up after this much silence")
        sp.add_argument("--linger", type=float, default=0.5)
        sp.add_argument("--seed", type=int, default=42)
        for name, default in (("loss", 0.0), ("corrupt", 0.0), ("dup", 0.0), ("reorder", 0.0),
                              ("reorder-delay", 0.05), ("delay", 0.0), ("jitter", 0.0)):
            sp.add_argument(f"--{name}", type=float, default=default)

    d = sub.add_parser("demo", help="send a file through a bad network and verify it")
    common(d, choices=("all", "sw", "gbn", "sr"), default="all")
    d.set_defaults(loss=0.0)          # clean transfer by default; faults are opt-in
    d.add_argument("--file", help="file to send (default: random bytes)")
    d.add_argument("--size", type=int, default=50 * 1024, help="random file size in bytes")
    d.set_defaults(func=cmd_demo)

    r = sub.add_parser("receive", help="receive one file (start this first)")
    common(r)
    r.add_argument("--host", default="127.0.0.1")
    r.add_argument("--port", type=int, required=True)
    r.add_argument("--peer", required=True, help="sender's HOST:PORT, where ACKs are sent")
    r.add_argument("--out", required=True)
    r.set_defaults(func=cmd_receive)

    s = sub.add_parser("send", help="send one file")
    common(s)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, required=True)
    s.add_argument("--peer", required=True, help="receiver's HOST:PORT")
    s.add_argument("--file", required=True)
    s.set_defaults(func=cmd_send)
    return p


def main(argv=None):
    parser = build_parser()
    a = parser.parse_args(argv)
    if getattr(a, "reset", False) and not getattr(a, "adaptive", False):
        parser.error("--reset only makes sense together with --adaptive")

    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())