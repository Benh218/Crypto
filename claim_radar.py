#!/usr/bin/env python3
import argparse
import json
import os
import sys

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

    args = ap.parse_args()

    if not os.path.exists(SHARDS_DIR):
        sys.exit(
            f"error: data directory not found at {DATA_DIR}. "
            "Set CLAIM_RADAR_DATA to the ForgottenETH repo's data/ folder "
            "(clone https://github.com/q84c6tsm95-create/forgotten-eth)."
        )
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


if __name__ == "__main__":
    main()
