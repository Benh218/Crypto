#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

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
            if not addr.startswith("0x"):
                continue
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


# ---------------------------------------------------------------------------
# Phase 3 — Claim executor (read-only simulation + unsigned tx generation)
# ---------------------------------------------------------------------------

KECCAK_ROUNDS = 24
KECCAK_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808a, 0x8000000080008000,
    0x000000000000808b, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008a, 0x0000000000000088, 0x0000000080008009, 0x000000008000000a,
    0x000000008000808b, 0x800000000000008b, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800a, 0x800000008000000a,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
KECCAK_RHO = [
    [0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56], [27, 20, 39, 8, 14],
]
KECCAK_MASK64 = (1 << 64) - 1
KECCAK_RATE = 136


def _keccak_rol(v, n):
    n %= 64
    return ((v << n) | (v >> (64 - n))) & KECCAK_MASK64


def _keccak_f(state):
    for rc in KECCAK_RC:
        c = [state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _keccak_rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _keccak_rol(state[x][y], KECCAK_RHO[x][y])
        for x in range(5):
            for y in range(5):
                state[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
        state[0][0] ^= rc
    return state


def keccak256(data):
    rate_bits = KECCAK_RATE * 8
    padded = bytearray(data)
    padded.append(0x01)
    while (len(padded) * 8) % rate_bits != rate_bits - 8:
        padded.append(0x00)
    padded.append(0x80)
    state = [[0] * 5 for _ in range(5)]
    for off in range(0, len(padded), KECCAK_RATE):
        block = padded[off:off + KECCAK_RATE]
        for i in range(KECCAK_RATE // 8):
            lane = int.from_bytes(block[i * 8:i * 8 + 8], "little")
            state[i % 5][i // 5] ^= lane
        _keccak_f(state)
    out = bytearray()
    for i in range(4):
        lane = state[i % 5][i // 5]
        out += lane.to_bytes(8, "little")
    return bytes(out[:32])


def selector(sig):
    return keccak256(sig.encode())[:4]


def encode_uint256(value):
    return int(value).to_bytes(32, "big")


def encode_address(addr):
    clean = addr.lower().removeprefix("0x")
    if len(clean) != 40:
        raise ValueError(f"invalid address: {addr}")
    return bytes.fromhex(clean).rjust(32, b"\x00")


def encode_args(argtypes, args):
    out = b""
    for typ, arg in zip(argtypes, args):
        if typ == "uint256":
            out += encode_uint256(arg)
        elif typ == "address":
            out += encode_address(arg)
        else:
            raise ValueError(f"unsupported arg type: {typ}")
    return out


# Verified claim paths. 'amount_from' is a read path used to auto-resolve the
# amount argument when --amount is not supplied.
CLAIM_PATHS = {
    "aave_v1": {
        "name": "Aave v1 (aETH redeem)",
        "contract": "0x3a3A65aAb0dd2A17E3F1947bA16138cd37d08c04",
        "method": "redeem(uint256)",
        "amount_from": {
            "method": "balanceOf(address)",
            "contract": "0x3a3A65aAb0dd2A17E3F1947bA16138cd37d08c04",
        },
        "note": "Burn aETH shares for ETH. Users call aETH.redeem(amount) to unwrap.",
    },
    "etherdelta": {
        "name": "EtherDelta v2 (ETH withdraw)",
        "contract": "0x8d12A197cB00D4747a1fe03395095ce2A5CC6819",
        "method": "withdraw(uint256)",
        "amount_from": {
            "method": "balanceOf(address,address)",
            "contract": "0x8d12A197cB00D4747a1fe03395095ce2A5CC6819",
            "args": ["0x0000000000000000000000000000000000000000", "<address>"],
        },
        "note": "Withdraw ETH deposit balance from the defunct EtherDelta v2 contract.",
    },
    "idex_v1": {
        "name": "IDEX v1 (ETH withdraw)",
        "contract": "0x2a0c0DBEcC7E4D658f48E01e3fA353F44050c208",
        "method": "withdraw(address,uint256)",
        "fixed_args": ["0x0000000000000000000000000000000000000000"],
        "amount_from": {
            "method": "balanceOf(address,address)",
            "contract": "0x2a0c0DBEcC7E4D658f48E01e3fA353F44050c208",
            "args": ["0x0000000000000000000000000000000000000000", "<address>"],
        },
        "note": "Withdraw ETH deposit balance from the defunct IDEX v1 Exchange contract.",
    },
}

ABI_CACHE_DIR = os.path.join(CONFIG_DIR, "abi_cache")


def fetch_abi(address):
    os.makedirs(ABI_CACHE_DIR, exist_ok=True)
    addr = address.lower()
    cache = os.path.join(ABI_CACHE_DIR, addr + ".json")
    if os.path.exists(cache):
        with open(cache) as f:
            return json.load(f)
    url = f"https://eth.blockscout.com/api/v2/smart-contracts/{addr}"
    data = http_json(url, headers={"User-Agent": "claim-radar/0.3"})
    abi = data.get("abi") or []
    with open(cache, "w") as f:
        json.dump(abi, f, indent=1)
    return abi


class RevertError(RuntimeError):
    pass


def decode_revert(data):
    if not (isinstance(data, str) and data.startswith("0x") and len(data) >= 10 and data[2:10] == "08c379a0"):
        return None
    body = bytes.fromhex(data[10:])
    if len(body) < 64:
        return None
    length = int.from_bytes(body[32:64], "big")
    return body[64:64 + length].decode(errors="replace")


def rpc_eth(rpc, method, params):
    last = None
    for cand in [rpc] + DEFAULT_RPCS:
        try:
            res = http_json(cand, {"jsonrpc": "2.0", "method": method, "params": params, "id": 1})
        except Exception as e:
            last = e
            continue
        if "error" in res:
            err = res["error"]
            msg = err.get("data") or err.get("message", "rpc error")
            reason = decode_revert(msg)
            if reason is not None or (isinstance(err.get("data"), str) and "revert" in msg.lower()):
                raise RevertError(f"execution reverted{': ' + reason if reason else ''}")
            last = RuntimeError(f"{method} error: {err.get('message', 'unknown')}")
            continue
        return res["result"]
    raise RuntimeError(f"{method} failed on all RPCs: {last}")


def _hex(data):
    return data if data.startswith("0x") else "0x" + data


def eth_call(rpc, to, data, from_addr="0x0000000000000000000000000000000000000000"):
    return rpc_eth(rpc, "eth_call", [{"to": to, "data": _hex(data), "from": from_addr}, "latest"])


def eth_estimate_gas(rpc, to, data, from_addr):
    return rpc_eth(rpc, "eth_estimateGas", [{"to": to, "data": _hex(data), "from": from_addr}])


def path_data(method, args):
    argtypes = [t.strip() for t in method[method.index("(") + 1:method.index(")")].split(",") if t.strip()]
    return selector(method) + encode_args(argtypes, args)


def resolve_amount(config, path, user, amount_eth):
    if amount_eth is not None:
        return int(amount_eth * 1e18)
    af = path.get("amount_from")
    if not af:
        return None
    args = [user if a == "<address>" else a for a in af.get("args", ["<address>"])]
    data = path_data(af["method"], args)
    res = eth_call(config.get("rpc"), af["contract"], data.hex())
    return int(res, 16)


def make_unsigned_tx(config, path, user, amount_wei):
    contract = path["contract"]
    method = path["method"]
    fixed = [a for a in path.get("fixed_args", [])]
    data = path_data(method, fixed + [amount_wei])
    from_addr = user.lower()
    rpc = config.get("rpc")
    nonce = int(rpc_eth(rpc, "eth_getTransactionCount", [from_addr, "pending"]), 16)
    try:
        gas = int(eth_estimate_gas(rpc, contract, data.hex(), from_addr), 16)
    except (RevertError, RuntimeError):
        # state reverts (or RPC refuses): fall back to a calldata-size estimate
        calldata = data
        nz = sum(1 for b in calldata if b != 0)
        gas = 21000 + 16 * nz + 4 * (len(calldata) - nz) + 30000
    block = rpc_eth(rpc, "eth_getBlockByNumber", ["latest", False]) or {}
    base_fee = int(block.get("baseFeePerGas", "0x0"), 16)
    priority = 2 * 10**9  # 2 gwei default tip
    max_fee = 2 * base_fee + priority
    return {
        "type": "0x2",
        "chainId": "0x1",
        "nonce": hex(nonce),
        "to": contract,
        "value": "0x0",
        "data": "0x" + data.hex(),
        "maxPriorityFeePerGas": hex(priority),
        "maxFeePerGas": hex(max_fee),
        "gas": hex(gas),
        "from": from_addr,
    }


def cmd_claim(args, config):
    if args.list_paths:
        for k, p in sorted(CLAIM_PATHS.items()):
            print(f"{k}: {p['name']}\n    contract: {p['contract']}\n    method:   {p['method']}\n    {p['note']}\n")
        return 0
    if not args.protocol or not args.address:
        print("claim requires --protocol and --address (or --list-paths)")
        return 1
    path = CLAIM_PATHS.get(args.protocol)
    if not path:
        print(f"unknown protocol '{args.protocol}'. Known: {', '.join(sorted(CLAIM_PATHS))}")
        return 1
    user = args.address.lower()
    amount_wei = resolve_amount(config, path, user, args.amount)
    if amount_wei is None:
        print(f"'{args.protocol}' needs --amount (no auto-resolution path registered)")
        return 1
    if amount_wei <= 0:
        print("amount is 0; nothing to claim")
        return 0
    print(f"claim path: {path['name']}")
    print(f"  contract: {path['contract']}")
    print(f"  method:   {path['method']}")
    print(f"  amount:   {amount_wei} wei ({amount_wei / 1e18:.6f} ETH)")
    print("  simulating eth_call...")
    data = path_data(path["method"], [a for a in path.get("fixed_args", [])] + [amount_wei])
    try:
        eth_call(config.get("rpc"), path["contract"], data.hex(), from_addr=user)
        print("  eth_call OK (no revert)")
    except RuntimeError as e:
        print(f"  WARNING: {e}")
        print("  still generating the unsigned tx; verify contract state manually")
    tx = make_unsigned_tx(config, path, user, amount_wei)
    print()
    print("unsigned EIP-1559 transaction (sign offline; never expose your private key):")
    print(json.dumps(tx, indent=2))
    return 0


BLOCKSCOUT = "https://eth.blockscout.com"


def last_activity(address):
    url = f"{BLOCKSCOUT}/api/v2/addresses/{address}/transactions?items_count=1"
    data = http_json(url, headers={"User-Agent": "claim-radar/0.4"})
    items = data.get("items") or []
    if not items:
        return None
    ts = items[0].get("timestamp")
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def mapped_for(address):
    addr = address.lower()
    total = 0.0
    for shard_file in sorted(os.listdir(SHARDS_DIR)):
        if not shard_file.endswith(".json"):
            continue
        with open(os.path.join(SHARDS_DIR, shard_file)) as f:
            shard = json.load(f)
        entry = shard.get(addr)
        if entry:
            total += sum(extract_balance(v) for v in entry.values())
    return total


def cmd_dormant(args, config):
    population = []
    if args.address:
        population = [(args.address, mapped_for(args.address))]
    elif args.top:
        population = top_addresses(n=args.top, min_balance=0.0)
    elif args.addr_file:
        with open(args.addr_file) as f:
            population = [(ln.strip(), 0.0) for ln in f if ln.strip() and not ln.startswith("#")]
    if not population:
        print("dormant needs --address, --top N, or --addr-file")
        return 1

    cutoff = args.inactive_years * 365.25 * 86400

    def check(item):
        addr, mapped = item
        bal = rpc_balance(config, addr) or 0.0
        value = max(bal, mapped)
        if value < args.min_eth:
            return None
        last = last_activity(addr)
        if last is None:
            age_days = None
        else:
            age_sec = (datetime.now(timezone.utc) - last).total_seconds()
            age_days = age_sec / 86400
            if age_sec < cutoff:
                return None
        hits = {}
        if args.sweep:
            for k, p in sorted(SWEEP_PATHS.items()):
                try:
                    wei = sweep_check_one(config, p, addr)
                except (RevertError, RuntimeError):
                    continue
                eth = wei / 1e18
                if eth > 0:
                    hits[k] = eth
        return (value, addr, age_days, last, hits)

    rows = []
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(check, population):
            done += 1
            if res:
                rows.append(res)
            if done % 100 == 0:
                print(f"  checked {done}/{len(population)}", file=sys.stderr)
    rows.sort(key=lambda r: -r[0])
    if not rows:
        print("no dormant addresses with matching balances found")
        return 0
    print(f"{'value ETH':>10}  {'address':<42}  last-activity{'':<8}  sweep hits")
    for value, addr, age_days, last, hits in rows:
        when = "never" if last is None else last.strftime("%Y-%m-%d")
        sweep_str = "; ".join(f"{k}={v:.3f}ETH" for k, v in hits.items()) if hits else "-"
        print(f"{value:>10.4f}  {addr:<42}  {when:<24}  {sweep_str}")
    return 0


SWEEP_PATHS = {
    "aave_v1": {
        "name": "Aave v1 (aETH redeemable)",
        "contract": "0x3a3A65aAb0dd2A17E3F1947bA16138cd37d08c04",
        "read": "balanceOf(address)",
        "read_args": ["<address>"],
        "unit": "aETH",
        "claim": "aave_v1",
    },
    "etherdelta": {
        "name": "EtherDelta v2 (ETH balance)",
        "contract": "0x8d12A197cB00D4747a1fe03395095ce2A5CC6819",
        "read": "balanceOf(address,address)",
        "read_args": ["0x0000000000000000000000000000000000000000", "<address>"],
        "unit": "ETH",
        "claim": "etherdelta",
    },
    "idex_v1": {
        "name": "IDEX v1 (ETH balance)",
        "contract": "0x2a0c0DBEcC7E4D658f48E01e3fA353F44050c208",
        "read": "balanceOf(address,address)",
        "read_args": ["0x0000000000000000000000000000000000000000", "<address>"],
        "unit": "ETH",
        "claim": "idex_v1",
    },
}

SWEEP_CACHE = os.path.join(CONFIG_DIR, "sweep_cache.json")
SWEEP_TTL = 6 * 3600
SWEEP_LOCK = threading.Lock()


def read_claimable(rpc, path, user):
    args = [user if a == "<address>" else a for a in path["read_args"]]
    data = path_data(path["read"], args)
    res = eth_call(rpc, path["contract"], data.hex(), from_addr=user)
    return int(res, 16)


def sweep_check_one(config, path, user):
    with SWEEP_LOCK:
        cache = load_json(SWEEP_CACHE, {})
        key = f"{user}:{path['claim']}"
        hit = cache.get(key)
        now = time.time()
        if hit and now - hit.get("ts", 0) < SWEEP_TTL:
            return hit["wei"]
    wei = read_claimable(config.get("rpc"), path, user)
    with SWEEP_LOCK:
        cache = load_json(SWEEP_CACHE, {})
        cache[key] = {"wei": wei, "ts": now}
        save_json(SWEEP_CACHE, cache)
    return wei


def cmd_sweep(args, config):
    if args.list:
        print(f"{'protocol':<12} {'contract':<44} {'read':<28} unit")
        for k, p in sorted(SWEEP_PATHS.items()):
            print(f"{k:<12} {p['contract']:<44} {p['read']:<28} {p['unit']}")
        return 0
    if args.address:
        user = args.address.lower()
        total_eth = 0.0
        for k, p in sorted(SWEEP_PATHS.items()):
            try:
                wei = sweep_check_one(config, p, user)
            except (RevertError, RuntimeError) as e:
                print(f"{k}: read failed ({e})")
                continue
            eth = wei / 1e18
            if p["unit"] == "ETH":
                total_eth += eth
            if eth > 0 or args.detail:
                print(f"{k}: {eth:.6f} {p['unit']}  ({p['name']})")
                if eth > 0:
                    print(f"    claim via: claimradar.py claim --protocol {p['claim']} --address {user}")
        print(f"total claimable ETH (excl. token units): {total_eth:.6f}")
        return 0
    if args.top:
        ranked = top_addresses(n=args.top, min_balance=args.min)

        def scan_one(addr):
            total = 0.0
            details = []
            for k, p in sorted(SWEEP_PATHS.items()):
                try:
                    wei = sweep_check_one(config, p, addr)
                except (RevertError, RuntimeError):
                    continue
                eth = wei / 1e18
                if eth > 0:
                    total += eth
                    details.append(f"{k}={eth:.4f}{p['unit']}")
            return total, addr, details

        rows = []
        done = 0
        with ThreadPoolExecutor(max_workers=8) as ex:
            for total, addr, details in ex.map(scan_one, [a for a, _ in ranked]):
                done += 1
                if details:
                    rows.append((total, addr, details))
                if done % 100 == 0:
                    print(f"  scanned {done}/{len(ranked)}", file=sys.stderr)
        rows.sort(reverse=True)
        print(f"{'claimable ETH':>13}  {'address':<42}  sweep hits")
        for total, addr, details in rows:
            print(f"{total:>13.4f}  {addr:<42}  {'; '.join(details)}")
        return 0
    return 1


def main():
    ap = argparse.ArgumentParser(
        description="Claim Radar — check Ethereum addresses against the ForgottenETH public recovery index."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="check addresses against the recovery index")
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

    cl = sub.add_parser("claim", help="simulate + generate an unsigned claim transaction")
    cl.add_argument("--protocol", help="protocol key from CLAIM_PATHS (see --list-paths)")
    cl.add_argument("--address", help="your wallet address (the claimant)")
    cl.add_argument("--amount", type=float, default=None, help="claim amount in ETH (auto-resolved if omitted)")
    cl.add_argument("--config", default=DEFAULT_CONFIG)
    cl.add_argument("--list-paths", action="store_true", help="list registered claim paths and exit")

    sw = sub.add_parser("sweep", help="live on-chain sweep of claimable balances")
    sw.add_argument("--list", action="store_true", help="list registered sweep contracts and exit")
    sw.add_argument("--address", help="check one address against all sweep contracts")
    sw.add_argument("--top", type=int, default=0, help="scan the top-N addresses by mapped balance")
    sw.add_argument("--min", type=float, default=0.0, help="min mapped balance to include in scan")
    sw.add_argument("--detail", action="store_true", help="show zero balances too")
    sw.add_argument("--config", default=DEFAULT_CONFIG)

    dr = sub.add_parser("dormant", help="find dormant high-balance addresses (dead-man's switch)")
    dr.add_argument("--address", help="check a single address")
    dr.add_argument("--top", type=int, default=0, help="scan the top-N addresses by mapped balance")
    dr.add_argument("--addr-file", help="scan addresses from a newline-separated file")
    dr.add_argument("--min-eth", type=float, default=1.0, help="min current ETH balance to report")
    dr.add_argument("--inactive-years", type=float, default=3.0, help="min years since last tx")
    dr.add_argument("--sweep", action="store_true", help="also check each dormant hit against sweep contracts")
    dr.add_argument("--config", default=DEFAULT_CONFIG)

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
    elif args.cmd == "claim":
        config = load_json(args.config, default_config())
        sys.exit(cmd_claim(args, config))
    elif args.cmd == "sweep":
        config = load_json(args.config, default_config())
        sys.exit(cmd_sweep(args, config))
    elif args.cmd == "dormant":
        config = load_json(args.config, default_config())
        sys.exit(cmd_dormant(args, config))


if __name__ == "__main__":
    main()
