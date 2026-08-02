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
