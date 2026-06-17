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
```

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
