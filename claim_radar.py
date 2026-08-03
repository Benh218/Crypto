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
    if not os.path.isdir(SHARDS_DIR):
        return []
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
    req.add_header("User-Agent", "claim-radar/0.5 (+https://github.com/Benh218/Crypto)")
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


USAGE_GUIDE = """Claim Radar — plain-English usage guide
========================================

Claim Radar finds crypto stuck in broken/abandoned/shut-down Ethereum projects
(2016-2018). It reads the public ForgottenETH index and the live blockchain. It
NEVER touches private keys; every transaction it outputs is unsigned — you sign
it in your own wallet.

Commands (run: python claimradar.py <command> --help):
  check <addr>    search the index for your address -> amount per project, or
                  "no mapped balances found"
  top -n 10       biggest stuck balances overall (--min 1 ignores < 1 ETH)
  watch           background alarm on funding + index updates; --init writes a
                  default config; run every 10 min via cron
  sweep           LIVE read of dead contracts (Aave v1 aETH, EtherDelta, IDEX v1)
                  --top 300 scans the biggest addresses; --address checks one
  dormant         find high-value long-inactive addresses (dead-man's switch)
                  --top 100 --min-eth 100 --inactive-years 3 --sweep
  migrate         find unmigrated SAI/ANTv1 (still swappable 1:1)
                  --top 300 --min 1, or --address X --tx for both unsigned txs
  claim           build a simulated, unsigned recovery tx
                  claim --protocol etherdelta --address 0x... ; --list-paths
                  shows all supported recovery methods
  registry        track donation/declared-recovery addresses (public offers)
                  registry --add 0x... --label 'vault' --source <url>; --live
                  shows current balances; watch mode alerts on funding
  open            free-unclaimed / open public claim pools: scan, check, claim
                  open (default scan) live-reads each pool's balance;
                  open check <key> shows terms + eligibility;
                  open claim --claim-pool <key> --claim-address 0x... builds
                  an unsigned claim tx for pools tagged eligibility=open;
                  open add registers a new pool you found with public terms

Quick start:
  1. python claimradar.py check 0xYourAddress --detail   # do you have anything?
  2. python claimradar.py top -n 20                      # what's worth looking at
  3. python claimradar.py sweep --top 300 --min 1        # claimable right now
  4. python claimradar.py claim --protocol <name> --address 0x...  # build tx
  5. Sign the JSON in your wallet and broadcast.
  Optional: python claimradar.py watch --init && cron every 10 min.

Safety:
  - Reads public data only; never stores private keys.
  - "Mapped" balances are from a public research index — confirm with a live
    sweep/claim simulation before trusting a number.
  - Claimable != free money: recovery still needs the right contract call + gas.
  - Nothing here grants access to anyone else's funds; it only surfaces which
    addresses hold stuck value.

Full docs: USAGE.md in the repo.
"""


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


def build_unsigned(config, to, method, args, from_addr, value_wei=0, nonce=None):
    data = path_data(method, args)
    rpc = config.get("rpc")
    if nonce is None:
        nonce = int(rpc_eth(rpc, "eth_getTransactionCount", [from_addr, "pending"]), 16)
    try:
        gas = int(eth_estimate_gas(rpc, to, data.hex(), from_addr), 16)
    except (RevertError, RuntimeError):
        nz = sum(1 for b in data if b != 0)
        gas = 21000 + 16 * nz + 4 * (len(data) - nz) + 30000
    block = rpc_eth(rpc, "eth_getBlockByNumber", ["latest", False]) or {}
    base_fee = int(block.get("baseFeePerGas", "0x0"), 16)
    priority = 2 * 10**9  # 2 gwei default tip
    max_fee = 2 * base_fee + priority
    return {
        "type": "0x2",
        "chainId": "0x1",
        "nonce": hex(nonce),
        "to": to,
        "value": hex(value_wei),
        "data": "0x" + data.hex(),
        "maxPriorityFeePerGas": hex(priority),
        "maxFeePerGas": hex(max_fee),
        "gas": hex(gas),
        "from": from_addr,
    }


def make_unsigned_tx(config, path, user, amount_wei):
    contract = path["contract"]
    method = path["method"]
    fixed = [a for a in path.get("fixed_args", [])]
    return build_unsigned(config, contract, method, fixed + [amount_wei], user)


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
    if not os.path.isdir(SHARDS_DIR):
        return 0.0
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


MIGRATE_PATHS = {
    "sai": {
        "name": "SAI (old single-collateral DAI) -> DAI 1:1",
        "token": "0x89d24a6b4ccb1b6faa2625fe562bdd9a23260359",
        "decimals": 18,
        "swap_contract": "0xc73e0383f3aff3215e6f04b0331d58cecf0ab849",
        "swap_method": "swapSaiToDai(uint256)",
        "note": "approve ScdMcdMigration on SAI, then swapSaiToDai(amount).",
    },
    "ant_v1": {
        "name": "ANTv1 -> ANTv2 (no deadline)",
        "token": "0x960b236A07cf122663c4303350609A66A7B288C0",
        "decimals": 18,
        "swap_contract": "0x078BEbC744B819657e1927bF41aB8C74cBBF912D",
        "swap_method": "migrate(uint256)",
        "note": "approve ANTv2Migrator on ANTv1, then migrate(amount).",
    },
}


def migrate_check(config, path, user, want_tx=False):
    token = path["token"]
    data = path_data("balanceOf(address)", [user])
    res = eth_call(config.get("rpc"), token, data.hex(), from_addr=user)
    wei = int(res, 16)
    out = {"path": path.get("swap_method", "").split("(")[0].replace("swap", "migrate"), "wei": wei}
    if want_tx and wei > 0:
        approve_tx = build_unsigned(config, token, "approve(address,uint256)",
                                    [path["swap_contract"], wei], user)
        swap_tx = build_unsigned(config, path["swap_contract"], path["swap_method"],
                                 [wei], user, nonce=int(approve_tx["nonce"], 16) + 1)
        out["approve_tx"] = approve_tx
        out["swap_tx"] = swap_tx
    return out


def cmd_migrate(args, config):
    if args.list:
        print(f"{'path':<12} {'token':<44} {'swap contract':<44} method")
        for k, p in sorted(MIGRATE_PATHS.items()):
            print(f"{k:<12} {p['token']:<44} {p['swap_contract']:<44} {p['swap_method']}")
            print(f"    {p['name']}")
            print(f"    {p['note']}")
        return 0
    if args.address:
        user = args.address.lower()
        for k, p in sorted(MIGRATE_PATHS.items()):
            try:
                r = migrate_check(config, p, user, want_tx=args.tx)
            except (RevertError, RuntimeError) as e:
                print(f"{k}: read failed ({e})")
                continue
            amt = r["wei"] / 10 ** p["decimals"]
            print(f"{k}: {amt:.6f} unmigrated  ({p['name']})")
            if amt > 0 and args.tx:
                print("  step 1 approve:")
                print(json.dumps(r["approve_tx"], indent=2))
                print("  step 2 swap/migrate:")
                print(json.dumps(r["swap_tx"], indent=2))
            elif amt == 0 and args.tx:
                print("  (nothing to migrate — no txs generated)")
        return 0
    if args.top:
        ranked = top_addresses(n=args.top, min_balance=args.min)

        def scan_one(addr):
            hits = []
            for k, p in sorted(MIGRATE_PATHS.items()):
                try:
                    r = migrate_check(config, p, addr)
                except (RevertError, RuntimeError):
                    continue
                amt = r["wei"] / 10 ** p["decimals"]
                if amt > 0:
                    hits.append(f"{k}={amt:.4f}")
            return addr, hits

        rows = []
        done = 0
        with ThreadPoolExecutor(max_workers=8) as ex:
            for addr, hits in ex.map(scan_one, [a for a, _ in ranked]):
                done += 1
                if hits and addr != "0x" + "0" * 40:
                    rows.append((addr, hits))
                if done % 100 == 0:
                    print(f"  scanned {done}/{len(ranked)}", file=sys.stderr)
        print(f"{'address':<42}  unmigrated tokens")
        for addr, hits in rows:
            print(f"{addr:<42}  {'; '.join(hits)}")
        return 0
    return 1


# ---------------------------------------------------------------------------
# Phase 5 — Registry: donation / declared-recovery address tracking
# ---------------------------------------------------------------------------

DEFAULT_REGISTRY = os.path.join(CONFIG_DIR, "registry.json")
REGISTRY_STATE = os.path.join(CONFIG_DIR, "registry_state.json")

DECLARATION_TYPES = {
    "donation_designated": "donation/burn address with publicly designated recovery",
    "burn_with_claim": "burn address that keeps a published claim() path",
    "successor_designated": "address whose successor/beneficiary is publicly declared",
    "open_claim": "public contract with a claim function anyone may call",
    "funding_target": "address watched purely as a funding/entitlement target",
}

REGISTRY_ELIGIBILITY = ("open", "proof", "designated", "unknown")


def load_registry(path):
    reg = load_json(path, {"entries": []})
    reg.setdefault("entries", [])
    return reg


def save_registry(path, reg):
    save_json(path, reg)


def registry_key(e):
    return normalize(e.get("address", ""))


def registry_check_path(e):
    """A live read path for a declared address, if it has one registered."""
    claim = (e.get("claim_method") or "").strip()
    if not claim or "(" not in claim:
        return None
    return {"contract": e.get("address"), "method": claim}


def cmd_registry(args, config):
    path = args.config or DEFAULT_REGISTRY
    if args.export:
        reg = load_registry(path)
        with open(args.export, "w") as f:
            json.dump(reg, f, indent=2)
        print(f"exported {len(reg['entries'])} registry entries to {args.export}")
        return 0
    if args.add:
        addr = normalize(args.add)
        if len(addr.removeprefix("0x")) != 40:
            print(f"invalid address: {args.add}")
            return 1
        reg = load_registry(path)
        entry = {
            "address": addr,
            "label": args.label or addr,
            "declaration_type": args.type or "donation_designated",
            "source": args.source or "",
            "claim_method": args.claim_method or "",
            "eligibility": args.eligibility or "unknown",
            "note": args.note or "",
            "min_delta_eth": args.min_delta,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        if entry["declaration_type"] not in DECLARATION_TYPES:
            print(f"unknown declaration type '{entry['declaration_type']}'. Known: {', '.join(sorted(DECLARATION_TYPES))}")
            return 1
        if entry["eligibility"] not in REGISTRY_ELIGIBILITY:
            print(f"unknown eligibility '{entry['eligibility']}'. Known: {', '.join(REGISTRY_ELIGIBILITY)}")
            return 1
        reg["entries"] = [e for e in reg["entries"] if registry_key(e) != addr]
        reg["entries"].append(entry)
        save_registry(path, reg)
        print(f"added {addr} to registry ({entry['label']})")
        return 0
    if args.remove:
        addr = normalize(args.remove)
        reg = load_registry(path)
        before = len(reg["entries"])
        reg["entries"] = [e for e in reg["entries"] if registry_key(e) != addr]
        save_registry(path, reg)
        print(f"removed {addr}: {before - len(reg['entries'])} entry deleted")
        return 0
    if args.live or args.addr:
        targets = []
        if args.addr:
            targets = [{"address": normalize(args.addr), "label": args.addr}]
        else:
            reg = load_registry(path)
            targets = reg["entries"]
        if not targets:
            print("registry is empty; add entries with: claimradar.py registry --add <addr> --label ...")
            return 0
        print(f"{'balance ETH':>12}  {'address':<42}  label")
        for e in targets:
            bal = rpc_balance(config, e["address"])
            label = (e.get("label") or e["address"])[:36]
            print(f"{bal if bal is None else round(bal, 6):>12}  {e['address']:<42}  {label}")
        return 0
    if args.watch:
        reg = load_registry(path)
        if not reg["entries"]:
            print("registry is empty; add entries with: claimradar.py registry --add <addr> --label ...")
            return 1
        prior = load_json(REGISTRY_STATE, {}).get("balances", {})
        while True:
            alerts = []
            for e in reg["entries"]:
                addr = e["address"]
                label = e.get("label") or addr
                min_delta = float(e.get("min_delta_eth", 0.01))
                bal = rpc_balance(config, addr)
                if bal is None:
                    alerts.append(f"[registry] {label}: RPC unreachable, skipped")
                    continue
                prev = prior.get(addr)
                if prev is None:
                    alerts.append(f"[registry] {label}: baseline balance {bal:.6f} ETH")
                else:
                    delta = bal - prev
                    if delta > min_delta:
                        alerts.append(
                            f"[registry] {label}: +{delta:.6f} ETH inbound funding "
                            f"({prev:.6f} -> {bal:.6f})"
                        )
                    elif delta < -min_delta:
                        alerts.append(
                            f"[registry] {label}: -{abs(delta):.6f} ETH "
                            f"({prev:.6f} -> {bal:.6f})"
                        )
                prior[addr] = bal
            save_json(REGISTRY_STATE, {"balances": prior})
            notify(alerts, notify_cmd=args.notify_cmd, quiet=args.quiet)
            if not args.interval:
                break
            time.sleep(args.interval)
        return 0
    # default: list
    reg = load_registry(path)
    if not reg["entries"]:
        print("registry is empty. Add declared-recovery addresses:")
        print("  claimradar.py registry --add 0x... --label 'Public vault' --source <url> --type donation_designated")
        return 0
    print(f"{'address':<42}  {'type':<20} {'elig':<10} claim method")
    for e in reg["entries"]:
        print(
            f"{e['address']:<42}  {(e.get('declaration_type') or ''):<20} "
            f"{(e.get('eligibility') or ''):<10} {e.get('claim_method') or '-'}"
        )
        if e.get("label"):
            print(f"{'':<42}  label: {e['label']}")
        if e.get("source"):
            print(f"{'':<42}  source: {e['source']}")
        if e.get("note"):
            print(f"{'':<42}  note: {e['note'][:120]}")
    return 0


# ---------------------------------------------------------------------------
# Phase 5 — Open Claims: 'free unclaimed crypto' registry + scanner
# ---------------------------------------------------------------------------

DEFAULT_OPEN_CLAIMS = os.path.join(CONFIG_DIR, "open_claims.json")
OPEN_STATE = os.path.join(CONFIG_DIR, "open_state.json")

OPEN_CATEGORIES = {
    "airdrop": "public airdrop/claim contract",
    "bounty": "public bounty / prize pool",
    "redemption": "public redemption / migration pool",
    "burn_claim": "burn address with published claim path",
    "public_vault": "publicly declared recovery vault",
}

# Seed pools are the real, on-chain-verified recovery mechanisms already in
# this tool. Each is tagged 'designated': the caller must control the address
# whose balance is being claimed. `open add` registers new pools you find with
# public terms; `open scan` live-checks how much still sits in each pool.
OPEN_CLAIM_DEFAULTS = {
    "aave_v1": {
        "name": "Aave v1 (aETH redeem)",
        "contract": "0x3a3A65aAb0dd2A17E3F1947bA16138cd37d08c04",
        "category": "redemption",
        "eligibility": "designated",
        "eligibility_note": "Caller must hold aETH shares (balanceOf).",
        "read": {"method": "balanceOf(address)", "args": ["<address>"]},
        "read_contract": "0x3a3A65aAb0dd2A17E3F1947bA16138cd37d08c04",
        "claim_method": "redeem(uint256)",
        "claim_args": ["<amount>"],
        "unit": "aETH",
        "terms_url": "https://github.com/q84c6tsm95-create/forgotten-eth",
        "deadline": None,
        "note": "Burn aETH shares for ETH. Stuck since Aave v1 shutdown.",
    },
    "etherdelta": {
        "name": "EtherDelta v2 (ETH withdraw)",
        "contract": "0x8d12A197cB00D4747a1fe03395095ce2A5CC6819",
        "category": "redemption",
        "eligibility": "designated",
        "eligibility_note": "Caller must control the address with the deposit balance.",
        "read": {"method": "balanceOf(address,address)", "args": ["0x0000000000000000000000000000000000000000", "<address>"]},
        "read_contract": "0x8d12A197cB00D4747a1fe03395095ce2A5CC6819",
        "claim_method": "withdraw(uint256)",
        "claim_args": ["<amount>"],
        "unit": "ETH",
        "terms_url": "https://github.com/q84c6tsm95-create/forgotten-eth",
        "deadline": None,
        "note": "Withdraw ETH deposit balance from the defunct exchange contract.",
    },
    "idex_v1": {
        "name": "IDEX v1 (ETH withdraw)",
        "contract": "0x2a0c0DBEcC7E4D658f48E01e3fA353F44050c208",
        "category": "redemption",
        "eligibility": "designated",
        "eligibility_note": "Caller must control the address with the deposit balance.",
        "read": {"method": "balanceOf(address,address)", "args": ["0x0000000000000000000000000000000000000000", "<address>"]},
        "read_contract": "0x2a0c0DBEcC7E4D658f48E01e3fA353F44050c208",
        "claim_method": "withdraw(address,uint256)",
        "claim_args": ["0x0000000000000000000000000000000000000000", "<amount>"],
        "unit": "ETH",
        "terms_url": "https://github.com/q84c6tsm95-create/forgotten-eth",
        "deadline": None,
        "note": "Withdraw ETH deposit balance from the defunct IDEX v1 Exchange.",
    },
}


def load_open_claims(path):
    doc = load_json(path, {"pools": dict(OPEN_CLAIM_DEFAULTS)})
    doc.setdefault("pools", {})
    for key, pool in OPEN_CLAIM_DEFAULTS.items():
        doc["pools"].setdefault(key, pool)
    return doc


def open_pool_read_path(pool):
    """Resolve a read path into (contract, data) for live balance checks."""
    read = pool.get("read")
    if not read:
        return None
    contract = pool.get("read_contract") or pool.get("contract")
    method = read["method"]
    args = [a for a in read.get("args", ["<address>"])]
    if "<address>" in args:
        return None
    return contract, path_data(method, args)


def open_pool_balance(config, pool):
    """Live total balance held in a pool. Returns float in pool units, or None."""
    rp = open_pool_read_path(pool)
    if rp is None:
        return None
    contract, data = rp
    try:
        res = eth_call(config.get("rpc"), contract, data.hex())
    except (RevertError, RuntimeError):
        return None
    return int(res, 16) / 1e18


def cmd_open(args, config):
    path = args.config or DEFAULT_OPEN_CLAIMS
    doc = load_open_claims(path)
    pools = doc["pools"]

    if args.add:
        key = args.add
        if key in pools:
            print(f"pool '{key}' already exists; remove it first or use a different key")
            return 1
        contract = normalize(args.contract or "")
        if len(contract.removeprefix("0x")) != 40:
            print("open add requires --contract <0x...>")
            return 1
        pools[key] = {
            "name": args.name or key,
            "contract": contract,
            "category": args.category or "public_vault",
            "eligibility": args.eligibility or "unknown",
            "eligibility_note": args.eligibility_note or "",
            "read": {"method": args.read_method or "balanceOf(address)", "args": [contract]} if args.read_method else None,
            "read_contract": contract,
            "claim_method": args.claim_method or "",
            "claim_args": [],
            "unit": args.unit or "units",
            "terms_url": args.terms or "",
            "deadline": args.deadline,
            "note": args.note or "",
        }
        if args.category not in OPEN_CATEGORIES:
            print(f"warning: unknown category '{args.category}' (known: {', '.join(sorted(OPEN_CATEGORIES))})")
        if args.eligibility not in REGISTRY_ELIGIBILITY:
            print(f"warning: unknown eligibility '{args.eligibility}' (known: {', '.join(REGISTRY_ELIGIBILITY)})")
        save_json(path, doc)
        print(f"added open-claim pool '{key}': {pools[key]['name']}")
        return 0
    if args.remove:
        key = args.remove
        if key not in pools:
            print(f"unknown pool '{key}'")
            return 1
        del pools[key]
        save_json(path, doc)
        print(f"removed pool '{key}'")
        return 0
    if args.check:
        key = args.check
        pool = pools.get(key)
        if not pool:
            print(f"unknown pool '{key}'. Known: {', '.join(sorted(pools))}")
            return 1
        bal = open_pool_balance(config, pool)
        print(f"pool:       {pool['name']} ({key})")
        print(f"contract:   {pool['contract']}")
        print(f"category:   {pool.get('category')}")
        print(f"eligibility:{pool.get('eligibility')} — {pool.get('eligibility_note', '')}")
        print(f"pool units: {bal if bal is None else round(bal, 4)} {pool.get('unit')}")
        if pool.get("claim_method"):
            print(f"claim:      {pool['claim_method']} on {pool['contract']}")
        if pool.get("deadline"):
            print(f"deadline:   {pool['deadline']}")
        if pool.get("terms_url"):
            print(f"terms:      {pool['terms_url']}")
        if pool.get("note"):
            print(f"note:       {pool['note']}")
        return 0
    if args.claim_pool:
        key = args.claim_pool
        pool = pools.get(key)
        if not pool:
            print(f"unknown pool '{key}'. Known: {', '.join(sorted(pools))}")
            return 1
        user = normalize(args.claim_address or "")
        if len(user.removeprefix("0x")) != 40:
            print("open claim requires --claim-address <your-wallet>")
            return 1
        elig = pool.get("eligibility", "unknown")
        if elig != "open":
            print(f"pool '{key}' is not open-to-anyone (eligibility={elig}).")
            print(f"  {pool.get('eligibility_note', '')}")
            if elig == "designated":
                print("  -> you can only claim balances at addresses you control; use:")
                print(f"     claimradar.py claim --protocol {key} --address {user}")
            else:
                print("  -> resolve the eligibility requirement (proof/designation) before claiming.")
            return 1
        method = pool.get("claim_method") or ""
        if not method or "(" not in method:
            print(f"pool '{key}' has no registered claim method")
            return 1
        amount_wei = int(args.amount * 1e18) if args.amount else None
        if amount_wei is None:
            bal = open_pool_balance(config, pool)
            amount_wei = int((bal or 0) * 1e18)
        if amount_wei <= 0:
            print("pool balance is 0; nothing to claim")
            return 0
        args_claim = []
        for a in pool.get("claim_args", []):
            args_claim.append(user if a == "<address>" else amount_wei if a == "<amount>" else a)
        data = path_data(method, args_claim)
        print(f"open claim: {pool['name']} ({key})")
        print(f"  contract:   {pool['contract']}")
        print(f"  method:     {method}")
        print(f"  amount:     {amount_wei} wei ({amount_wei / 1e18:.6f} {pool.get('unit')})")
        print("  simulating eth_call...")
        try:
            eth_call(config.get("rpc"), pool["contract"], data.hex(), from_addr=user)
            print("  eth_call OK (no revert)")
        except RuntimeError as e:
            print(f"  WARNING: {e}")
        tx = build_unsigned(config, pool["contract"], method, args_claim, user)
        print()
        print("unsigned EIP-1559 transaction (sign offline; never expose your private key):")
        print(json.dumps(tx, indent=2))
        return 0
    if args.watch:
        if not pools:
            print("no pools registered")
            return 1
        prior = load_json(OPEN_STATE, {}).get("balances", {})
        while True:
            alerts = []
            for key, pool in sorted(pools.items()):
                bal = open_pool_balance(config, pool)
                prev = prior.get(key)
                if bal is None:
                    continue
                if prev is None:
                    alerts.append(f"[open] {key} baseline balance {bal:.4f} {pool.get('unit')}")
                else:
                    delta = bal - prev
                    if abs(delta) >= 1e-9:
                        alerts.append(
                            f"[open] {key} {'+' if delta > 0 else ''}{delta:.4f} {pool.get('unit')} "
                            f"({prev:.4f} -> {bal:.4f})"
                        )
                prior[key] = bal
            save_json(OPEN_STATE, {"balances": prior})
            notify(alerts, notify_cmd=args.notify_cmd, quiet=args.quiet)
            if not args.interval:
                break
            time.sleep(args.interval)
        return 0
    # default: scan / list
    if args.json:
        rows = []
        for key, pool in sorted(pools.items()):
            bal = open_pool_balance(config, pool)
            rows.append({
                "pool": key,
                "name": pool["name"],
                "contract": pool["contract"],
                "category": pool.get("category"),
                "eligibility": pool.get("eligibility"),
                "balance": bal,
                "unit": pool.get("unit"),
                "deadline": pool.get("deadline"),
            })
        print(json.dumps(rows, indent=2))
        return 0
    if not pools:
        print("no open-claim pools registered. Add one with:")
        print("  claimradar.py open add <key> --contract 0x... --name '...' --eligibility open")
        return 0
    print(f"{'pool':<14} {'balance':>12} {'unit':<8} {'elig':<10} {'deadline':<12} name")
    for key, pool in sorted(pools.items()):
        bal = open_pool_balance(config, pool)
        bal_str = "-" if bal is None else f"{bal:.4f}"
        print(
            f"{key:<14} {bal_str:>12} {pool.get('unit', ''):<8} "
            f"{pool.get('eligibility', ''):<10} {str(pool.get('deadline') or '-'):<12} {pool['name'][:34]}"
        )
    return 0


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

    mi = sub.add_parser("migrate", help="find unmigrated token balances (SAI->DAI, ANTv1->ANTv2)")
    mi.add_argument("--list", action="store_true", help="list registered migration paths and exit")
    mi.add_argument("--address", help="check a single address for unmigrated balances")
    mi.add_argument("--tx", action="store_true", help="also generate unsigned approve + swap txs")
    mi.add_argument("--top", type=int, default=0, help="scan the top-N addresses by mapped balance")
    mi.add_argument("--min", type=float, default=0.0, help="min mapped balance to include in scan")
    mi.add_argument("--config", default=DEFAULT_CONFIG)

    rg = sub.add_parser(
        "registry",
        help="track donation/declared-recovery addresses (public offers) and watch them",
    )
    rg.add_argument("--add", metavar="ADDR", help="add an entry to the registry")
    rg.add_argument("--label", default="", help="human label for the declared address")
    rg.add_argument("--type", default="donation_designated", help="declaration type")
    rg.add_argument("--source", default="", help="URL or reference to the public declaration/terms")
    rg.add_argument("--claim-method", default="", help="on-chain claim function, if published (e.g. claim(uint256))")
    rg.add_argument("--eligibility", default="unknown", help="who may claim: open, proof, designated, unknown")
    rg.add_argument("--note", default="", help="free-form note")
    rg.add_argument("--min-delta", type=float, default=0.01, help="min ETH delta to alert on in watch mode")
    rg.add_argument("--remove", metavar="ADDR", help="remove an entry from the registry")
    rg.add_argument("--live", action="store_true", help="show live on-chain ETH balance of each entry")
    rg.add_argument("--addr", metavar="ADDR", help="live-check a single declared address")
    rg.add_argument("--export", metavar="FILE", help="export the registry as JSON")
    rg.add_argument("--watch", action="store_true", help="poll declared-address balances and alert on funding changes")
    rg.add_argument("--interval", type=int, default=0, help="watch loop interval in seconds (0 = run once)")
    rg.add_argument("--notify-cmd", default="", help="shell command template; {message} and {ts} are substituted")
    rg.add_argument("--quiet", action="store_true", help="only log, don't print alerts")
    rg.add_argument("--config", default=DEFAULT_REGISTRY)

    op = sub.add_parser(
        "open",
        help="free-unclaimed / open public claim pools: scan, check, claim, watch",
    )
    op.add_argument("--add", metavar="KEY", help="register a new open-claim pool")
    op.add_argument("--name", default="", help="display name for the pool")
    op.add_argument("--contract", default="", help="pool contract address")
    op.add_argument("--category", default="public_vault", help="airdrop, bounty, redemption, burn_claim, public_vault")
    op.add_argument("--eligibility", default="unknown", help="who may claim: open, proof, designated, unknown")
    op.add_argument("--eligibility-note", default="", help="plain-text eligibility requirement")
    op.add_argument("--read-method", default="", help="read method for pool balance (default balanceOf(address))")
    op.add_argument("--claim-method", default="", help="claim method, e.g. claim(uint256)")
    op.add_argument("--unit", default="units", help="unit name for the pool balance")
    op.add_argument("--terms", default="", help="URL to public terms/declaration")
    op.add_argument("--deadline", default="", help="claim deadline, e.g. 2026-12-31")
    op.add_argument("--note", default="", help="free-form note")
    op.add_argument("--remove", metavar="KEY", help="remove a registered pool")
    op.add_argument("--check", metavar="KEY", help="show detailed state of one pool")
    op.add_argument("--scan", dest="scan", action="store_true", help="live-scan all pools (default when no sub-action)")
    op.add_argument("--json", action="store_true", help="JSON output for the default scan")
    op.add_argument("--claim-pool", metavar="KEY", help="build an unsigned claim tx for an open pool")
    op.add_argument("--claim-address", default="", help="your wallet address for the claim tx")
    op.add_argument("--amount", type=float, default=None, help="claim amount in pool units (default: full pool)")
    op.add_argument("--watch", action="store_true", help="poll pool balances and alert on changes")
    op.add_argument("--interval", type=int, default=0, help="watch loop interval in seconds (0 = run once)")
    op.add_argument("--notify-cmd", default="", help="shell command template; {message} and {ts} are substituted")
    op.add_argument("--quiet", action="store_true", help="only log, don't print alerts")
    op.add_argument("--config", default=DEFAULT_OPEN_CLAIMS)

    ap.add_argument("--guide", action="store_true", help="print the plain-English usage guide and exit")

    if "--guide" in sys.argv:
        print(USAGE_GUIDE)
        return

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
    elif args.cmd == "migrate":
        config = load_json(args.config, default_config())
        sys.exit(cmd_migrate(args, config))
    elif args.cmd == "registry":
        config = load_json(DEFAULT_CONFIG, default_config())
        sys.exit(cmd_registry(args, config))
    elif args.cmd == "open":
        config = load_json(DEFAULT_CONFIG, default_config())
        sys.exit(cmd_open(args, config))


if __name__ == "__main__":
    main()
