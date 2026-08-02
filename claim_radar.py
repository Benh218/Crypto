#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

try:
    from datetime import datetime, timezone
except ImportError:  # pragma: no cover
    from datetime import datetime, timezone as _tz

    timezone = _tz

DATA_DIR = os.environ.get(
    "CLAIM_RADAR_DATA",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"),
)
SHARDS_DIR = os.path.join(DATA_DIR, "index_shards")
BALANCES_DIR = os.path.join(DATA_DIR, "balances")

_protocols = None
_protocol_info = None


def load_protocols():
    global _protocols, _protocol_info
    if _protocols is None:
        with open(os.path.join(DATA_DIR, "protocols.json")) as f:
            _protocols = {p["key"]: p for p in json.load(f)}
    if _protocol_info is None:
        with open(os.path.join(DATA_DIR, "protocol_info.json")) as f:
            _protocol_info = json.load(f)
    return _protocols, _protocol_info


def normalize(address):
    return address.strip().lower()


def shard_prefix(address):
    return normalize(address).removeprefix("0x")[:2]


def resolve(address):
    addr = normalize(address)
    if len(addr.removeprefix("0x")) != 40:
        raise ValueError(f"invalid address: {address}")
    shard = os.path.join(SHARDS_DIR, shard_prefix(addr) + ".json")
    if not os.path.exists(shard):
        return {}
    with open(shard) as f:
        index = json.load(f)
    return index.get(addr, {})


def protocol_details(key):
    protocols, info = load_protocols()
    p = protocols.get(key, {})
    i = info.get(key, {})
    return {
        "key": key,
        "name": i.get("name") or p.get("name") or key,
        "contract": p.get("contract") or i.get("contract"),
        "category": p.get("category") or i.get("category"),
        "desc": i.get("desc", ""),
        "claim_path": i.get("claim_path") or "",
        "balance_source": p.get("balance_source"),
        "balance_file": p.get("balance_file"),
    }


def balance_detail(key, address):
    protocols, _ = load_protocols()
    p = protocols.get(key, {})
    bf = os.path.join(BALANCES_DIR, p.get("balance_file", ""))
    if not os.path.exists(bf):
        return None
    with open(bf) as f:
        data = json.load(f)
    for entry in data.get("balances", []):
        if normalize(entry.get("address", "")) == normalize(address):
            return {
                "balance_wei": entry.get("balance_wei"),
                "balance_eth": entry.get("balance_eth"),
                "rank": entry.get("rank"),
                "scan_date": data.get("scan_date"),
                "coverage_pct": data.get("coverage_pct"),
                "description": data.get("description"),
            }
    return None


def extract_balance(pv):
    if isinstance(pv, dict):
        e = pv.get("e", 0)
        try:
            return float(e)
        except (TypeError, ValueError):
            return 0
    try:
        return float(pv)
    except (TypeError, ValueError):
        return 0


def check(address, detailed=False):
    mappings = resolve(address)
    results = []
    for key, pv in sorted(mappings.items(), key=lambda kv: -extract_balance(kv[1])):
        entry = {"balance_eth": extract_balance(pv)}
        entry.update(protocol_details(key))
        if detailed:
            d = balance_detail(key, address)
            if d:
                entry.update(d)
        results.append(entry)
    return results


def format_results(address, results):
    if not results:
        return f"{address}: no mapped claimable balances found in the ForgottenETH index."
    lines = [f"{address}: {len(results)} mapped claimable balance(s)"]
    for r in results:
        lines.append(f"\n  {r['name']} [{r['key']}]")
        lines.append(f"    balance: {r['balance_eth']:.6f} ETH")
        if r.get("contract"):
            lines.append(f"    contract: {r['contract']}")
        if r.get("category"):
            lines.append(f"    category: {r['category']}")
        if r.get("rank"):
            lines.append(f"    rank: #{r['rank']} in protocol")
        if r.get("claim_path"):
            lines.append(f"    claim path: {r['claim_path']}")
        elif r.get("description"):
            lines.append(f"    claim path: {r['description'][:200]}")
    return "\n".join(lines)


def top_addresses(n=10, min_balance=1.0):
    totals = {}
    for shard_file in sorted(os.listdir(SHARDS_DIR)):
        if not shard_file.endswith(".json"):
            continue
        with open(os.path.join(SHARDS_DIR, shard_file)) as f:
            shard = json.load(f)
        for addr, protos in shard.items():
            total = sum(extract_balance(v) for v in protos.values())
            if total >= min_balance:
                totals[addr] = totals.get(addr, 0) + total
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:n]
    return [(addr, total) for addr, total in ranked]


DEFAULT_RPCS = [
    "https://eth.drpc.org",
    "https://ethereum-rpc.publicnode.com",
    "https://1rpc.io/eth",
]
INDEX_REPO = "q84c6tsm95-create/forgotten-eth"
INDEX_CHANGELOG_URL = "https://raw.githubusercontent.com/q84c6tsm95-create/forgotten-eth/main/CHANGELOG.md"
CONFIG_DIR = os.path.expanduser("~/.claim_radar")
DEFAULT_CONFIG = os.path.join(CONFIG_DIR, "watch_config.json")
DEFAULT_STATE = os.path.join(CONFIG_DIR, "state.json")
ALERT_LOG = os.path.join(CONFIG_DIR, "alerts.log")


def http_json(url, data=None, headers=None, timeout=20):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "claim-radar/0.2 (+https://github.com/Benh218/Crypto)")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def wei_to_eth(hex_wei):
    try:
        return int(hex_wei, 16) / 1e18
    except (TypeError, ValueError):
        return 0.0


def default_config():
    return {
        "rpc": DEFAULT_RPCS[0],
        "fallback_rpcs": DEFAULT_RPCS[1:],
        "contracts": {
            "0xbb9bc244d798123fde783fcc1c72d3bb8c189413": {
                "label": "The DAO WithdrawDAO wrapper",
                "min_delta_eth": 0.01,
            },
            "0x2a0c0DBEcC7E4D658f48E01e3fA353F44050c208": {
                "label": "IDEX v1",
                "min_delta_eth": 0.01,
            },
            "0x8d12A197cB00D4747a1fe03395095ce2A5CC6819": {
                "label": "EtherDelta v2",
                "min_delta_eth": 0.01,
            },
        },
    }


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def rpc_balance(config, address):
    rpcs = [config.get("rpc")] + config.get("fallback_rpcs", [])
    for rpc in rpcs:
        try:
            res = http_json(
                rpc,
                {"jsonrpc": "2.0", "method": "eth_getBalance", "params": [address, "latest"], "id": 1},
            )
            if "result" in res:
                return wei_to_eth(res["result"])
        except Exception:
            continue
    return None


def check_index(state):
    url = f"https://api.github.com/repos/{INDEX_REPO}/commits?path=data&per_page=5"
    try:
        commits = http_json(url, headers={"User-Agent": "claim-radar"})
    except Exception as e:
        return [], f"index check failed: {e}"
    if not isinstance(commits, list) or not commits:
        return [], "index check: no commits returned"
    latest = commits[0]
    sha = latest["sha"]
    alerts = []
    if state.get("index_last_sha") is None:
        alerts.append(
            f"[index] baseline set at commit {sha[:10]} "
            f"({latest['commit']['committer']['date']}): {latest['commit']['message'].splitlines()[0]}"
        )
    elif sha != state.get("index_last_sha"):
        seen = set(state.get("index_seen_sha", []))
        new = [c for c in commits if c["sha"] not in seen][::-1]
        for c in new:
            msg = c["commit"]["message"].splitlines()[0]
            alerts.append(
                f"[index] NEW data commit {c['sha'][:10]} "
                f"({c['commit']['committer']['date']}): {msg}"
            )
        state["index_seen_sha"] = [c["sha"] for c in commits]
    state["index_last_sha"] = sha
    return alerts, None


def check_funding(config, state):
    contracts = config.get("contracts", {})
    if not contracts:
        return [], "no contracts configured in watch config"
    prior = state.setdefault("funding", {})
    alerts = []
    for address, meta in contracts.items():
        addr = address.strip().lower()
        label = meta.get("label", addr)
        min_delta = float(meta.get("min_delta_eth", 0.01))
        bal = rpc_balance(config, addr)
        if bal is None:
            alerts.append(f"[funding] {label}: RPC unreachable, skipped")
            continue
        prev = prior.get(addr)
        if prev is None:
            alerts.append(f"[funding] {label}: baseline balance {bal:.6f} ETH")
        else:
            delta = bal - prev
            if delta > min_delta:
                alerts.append(
                    f"[funding] {label}: +{delta:.6f} ETH inbound funding "
                    f"({prev:.6f} -> {bal:.6f})"
                )
            elif delta < -min_delta:
                alerts.append(
                    f"[funding] {label}: -{abs(delta):.6f} ETH (claim/sweep?) "
                    f"({prev:.6f} -> {bal:.6f})"
                )
        prior[addr] = bal
    return alerts, None


def notify(alerts, notify_cmd=None, quiet=False):
    if not alerts:
        return
    os.makedirs(CONFIG_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(ALERT_LOG, "a") as f:
        for a in alerts:
            f.write(f"{ts} {a}\n")
    if quiet:
        return
    for a in alerts:
        print(f"{ts}  {a}")
    if notify_cmd:
        text = "\n".join(alerts)
        cmd = notify_cmd.replace("{message}", text).replace("{ts}", ts)
        try:
            subprocess.run(cmd, shell=True, timeout=30)
        except Exception as e:
            print(f"[notify] command failed: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        description="Claim Radar — check Ethereum addresses against the ForgottenETH public recovery index."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="check one or more addresses")
    c.add_argument("addresses", nargs="+")
    c.add_argument("--detail", action="store_true", help="include per-protocol balance detail")
    c.add_argument("--json", action="store_true", help="output JSON")

    t = sub.add_parser("top", help="list addresses with the largest total mapped balances")
    t.add_argument("-n", type=int, default=10)
    t.add_argument("--min", type=float, default=1.0)
    t.add_argument("--json", action="store_true")

    w = sub.add_parser("watch", help="notifier: watch index updates and/or contract funding")
    w.add_argument("--init", action="store_true", help="write a default watch config and exit")
    w.add_argument("--index", action="store_true", help="check ForgottenETH data index for updates")
    w.add_argument("--funding", action="store_true", help="check watched contract balances for changes")
    w.add_argument("--interval", type=int, default=0, help="loop every N seconds (0 = run once)")
    w.add_argument("--config", default=DEFAULT_CONFIG)
    w.add_argument("--state", default=DEFAULT_STATE)
    w.add_argument("--notify-cmd", default="", help="shell command template; {message} and {ts} are substituted")
    w.add_argument("--quiet", action="store_true", help="only log, don't print alerts")

    args = ap.parse_args()

    if args.cmd == "check":
        for addr in args.addresses:
            results = check(addr, detailed=args.detail)
            if args.json:
                print(json.dumps({"address": addr, "results": results}, indent=2))
            else:
                print(format_results(addr, results))
    elif args.cmd == "top":
        ranked = top_addresses(n=args.n, min_balance=args.min)
        if args.json:
            print(json.dumps([{"address": a, "total_eth": t} for a, t in ranked], indent=2))
        else:
            for addr, total in ranked:
                print(f"{addr}  {total:.4f} ETH")
    elif args.cmd == "watch":
        if args.init:
            save_json(args.config, default_config())
            print(f"wrote default watch config to {args.config}")
            return
        if not args.index and not args.funding:
            ap.error("watch requires --index and/or --funding")
        if not os.path.exists(SHARDS_DIR):
            print(
                f"warning: data not found at {DATA_DIR}; set CLAIM_RADAR_DATA to the "
                "forgotten-eth data/ folder for --detail lookups",
                file=sys.stderr,
            )
        state = load_json(args.state, {})
        config = load_json(args.config, default_config())
        while True:
            alerts, err = [], None
            if args.index:
                a, err = check_index(state)
                alerts += a
            if args.funding:
                a, err2 = check_funding(config, state)
                alerts += a
                err = err or err2
            notify(alerts, notify_cmd=args.notify_cmd, quiet=args.quiet)
            if err and not alerts:
                if args.quiet:
                    with open(ALERT_LOG, "a") as f:
                        f.write(f"{datetime.now(timezone.utc)} {err}\n")
                else:
                    print(err, file=sys.stderr)
            save_json(args.state, state)
            if not args.interval:
                break
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
