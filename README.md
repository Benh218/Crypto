# BTC Tools

Bitcoin blockchain data analysis toolkit — fetcher, dormant coin scanner, and puzzle challenge tools.

## Structure

```
btc_tools/
├── models.py      — UTXO, AddressInfo, Transaction, DormantAddress
├── config.py      — Config with env var support (Blockchair, RPC, scanner)
├── fetcher.py     — Fetcher with Blockchair / Blockchain.info / Bitcoin Core RPC backends
├── scanner.py     — Dormant coin scanner (known addresses + custom address lists + CSV export)
└── cli.py         — CLI: address lookup, UTXO summary, dormant scan, known list
claim_radar.py     — Claim Radar: check addresses against the ForgottenETH recovery index
```

## Claim Radar (claim_radar.py)

Checks Ethereum addresses against the public [ForgottenETH](https://github.com/q84c6tsm95-create/forgotten-eth)
recovery index (defunct contracts with user-claimable balances — The DAO, Aave v1, EtherDelta, DigixDAO, etc.).
This is an audit/monitoring tool: it reads public data only, never touches private keys.

```bash
# 1. Point it at ForgottenETH's data (clone the repo once):
git clone --depth 1 https://github.com/q84c6tsm95-create/forgotten-eth.git
export CLAIM_RADAR_DATA=/path/to/forgotten-eth/data

# 2. Check one or more addresses
python claim_radar.py check 0xbf2c8b606974e36567b4a6ddf548b70cb622442d --detail

# JSON output
python claim_radar.py check 0x5256d6d94ed14667fa1661a99f5b142b1e051b8e --json

# Largest total mapped balances across the whole index
python claim_radar.py top -n 10 --min 50
```

Handles every shard value shape (ETH floats, decimal strings, token/NFT holder dicts), checksummed or raw
addresses, and leading-zero addresses. Uses the standard library only (no pip installs).

## Claim Radar Notifier (Phase 2)

The `watch` command monitors two things and alerts on changes:

1. **Index updates** — new data commits to the ForgottenETH repo (new protocols/balances added)
2. **Contract funding** — polls watched recovery contracts' ETH balances on public RPCs and alerts
   on inbound funding (`+N ETH`) or claims/sweeps (`-N ETH`)

```bash
# Write the default watch config (edit ~/.claim_radar/watch_config.json as needed)
python claim_radar.py watch --init

# Check index + funding once (cron-friendly)
python claim_radar.py watch --index --funding

# Daemon loop every 300s, with a notification hook
python claim_radar.py watch --index --funding --interval 300 \
  --notify-cmd 'curl -s -X POST -d "{\"text\":\"{message}\"}" <webhook-url>'

# Quiet mode: log to ~/.claim_radar/alerts.log, print nothing
python claim_radar.py watch --funding --quiet
```

**Watch config** (`~/.claim_radar/watch_config.json`):

```json
{
  "rpc": "https://eth.drpc.org",
  "fallback_rpcs": ["https://ethereum-rpc.publicnode.com", "https://1rpc.io/eth"],
  "contracts": {
    "0xbb9bc244d798123fde783fcc1c72d3bb8c189413": {
      "label": "The DAO WithdrawDAO wrapper",
      "min_delta_eth": 0.01
    }
  }
}
```

State (baseline balances, last index commit) is persisted to `~/.claim_radar/state.json`, and all
alerts are appended to `~/.claim_radar/alerts.log`. The notifier reads public data only.

## Quick Start

```bash
pip install requests
python -m btc_tools.cli known                      # List 10 known dormant addresses
python -m btc_tools.cli dormant --known --top 5     # Check which still hold balance
python -m btc_tools.cli address 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa   # Satoshi's address
python -m btc_tools.cli utxo-summary               # UTXO set overview
```

## Dormant Scanner

```bash
# Known dormant addresses (Satoshi, Patoshi, Silk Road, etc.)
python -m btc_tools.cli dormant --known --csv results.csv --top 20

# Custom address list
python -m btc_tools.cli dormant --addr-file ./addresses.txt

# Specific addresses
python -m btc_tools.cli dormant --addresses 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa 12c6DSiU4Rq3P4ZxziKxzrL5LmMBrzjrJX
```

## Config (via env vars)

| Variable | Purpose |
|---|---|
| `BLOCKCHAIR_API_KEY` | Blockchair API key (free tier works without) |
| `BTC_RPC_USER` / `BTC_RPC_PASSWORD` | Bitcoin Core RPC credentials |
| `BTC_RPC_HOST` / `BTC_RPC_PORT` | RPC endpoint (default 127.0.0.1:8332) |
