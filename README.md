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

## Claim Radar Executor (Phase 3)

The `claim` command simulates and generates an **unsigned** EIP-1559 transaction for a verified
recovery path. It reads public data (ABIs from Blockscout, live state via public RPCs) and never
touches your private keys — you sign offline in your wallet.

```bash
# List registered claim paths
python claim_radar.py claim --list-paths

# Aave v1: auto-resolves your aETH balance via balanceOf(), simulates redeem()
python claim_radar.py claim --protocol aave_v1 --address 0x5d843c34ff45d866a84d6913cdabd5845ba7c357

# EtherDelta v2: supply the amount from your mapped deposit
python claim_radar.py claim --protocol etherdelta \
  --address 0x00317cd2da2044840b1ebe775c676530a7c65ba3 --amount 22
```

For each path it: resolves the amount, does an `eth_call` simulation (surfacing revert reasons such
as `execution reverted: Transfer cannot be allowed.`), pulls your live nonce, estimates gas, and
prints an unsigned type-0x2 transaction JSON. Sign that JSON offline in a wallet like MetaMask or
Frame, then broadcast via any public node.

Registered paths (contracts + method selectors verified against on-chain ABIs):

| Protocol | Contract | Method | Notes |
|---|---|---|---|
| Aave v1 | `0x3a3A65aAb0dd2A17E3F1947bA16138cd37d08c04` | `redeem(uint256)` | Burn aETH shares for ETH |
| EtherDelta v2 | `0x8d12A197cB00D4747a1fe03395095ce2A5CC6819` | `withdraw(uint256)` | Withdraw ETH deposit balance |
| IDEX v1 | `0x2a0c0DBEcC7E4D658f48E01e3fA353F44050c208` | `withdraw(address,uint256)` | Withdraw ETH deposit balance |

## Claim Radar Sweeper (Phase 4a)

The `sweep` command does **live on-chain reads** (not the static index) against a registry of
recovery contracts to find currently-claimable balances. It caches reads for 6h in
`~/.claim_radar/sweep_cache.json`.

```bash
# List registered sweep contracts (read function used per contract)
python claim_radar.py sweep --list

# Check one address against every sweep contract (live balanceOf calls)
python claim_radar.py sweep --address 0xa7267b534bada4f7a77251ab54e6a78444786c7c

# Cross-reference: scan the top-N addresses by mapped index balance and rank
# by live claimable amount (cron-friendly, resumes fast via cache)
python claim_radar.py sweep --top 300 --min 1
```

Each hit prints the claim command to run against the same contract. Verified sweep paths:

| Protocol | Contract | Read | Unit |
|---|---|---|---|
| Aave v1 | `0x3a3A65aAb0dd2A17E3F1947bA16138cd37d08c04` | `balanceOf(address)` | aETH |
| EtherDelta v2 | `0x8d12A197cB00D4747a1fe03395095ce2A5CC6819` | `balanceOf(address,address)` | ETH |
| IDEX v1 | `0x2a0c0DBEcC7E4D658f48E01e3fA353F44050c208` | `balanceOf(address,address)` | ETH |

## Claim Radar Dead-Man's Switch (Phase 4b)

The `dormant` command finds high-value addresses that have been inactive for years — classic
recovery targets — using the Blockscout explorer API for last-activity and public RPCs for balances.
Optional `--sweep` cross-references each hit against the sweep contracts to surface value still
sitting in defunct protocols.

```bash
# Single address check
python claim_radar.py dormant --address 0x6d2af065ccb60c0f7e8ec5907c961c42a3447127 --sweep

# Scan the top-N mapped addresses for dormant whales (value >= 100 ETH, inactive >= 3y)
python claim_radar.py dormant --top 100 --min-eth 100 --inactive-years 3 --sweep

# Custom watchlist (newline-separated addresses)
python claim_radar.py dormant --addr-file ./watchlist.txt --min-eth 1 --inactive-years 2
```

"Value" is the larger of the address's current ETH balance and its mapped index balance, so money
parked in defunct contracts counts even when the wallet itself holds no ETH.

## Claim Radar Migration Pools (Phase 4c)

The `migrate` command finds legacy-token balances that are still redeemable 1:1 through official
migration contracts — money that is *accessible* but was never moved. Each path is a two-step flow:
`approve` the migration contract on the legacy token, then call its swap/migrate function. With
`--tx`, the tool emits both unsigned transactions (swap tx pre-set to nonce+1).

```bash
# List registered migration paths
python claim_radar.py migrate --list

# Check one address for unmigrated SAI / ANTv1, generate unsigned approve + swap txs
python claim_radar.py migrate --address 0x5256d6d94ed14667fa1661a99f5b142b1e051b8e --tx

# Scan the top-N mapped addresses for unmigrated balances
python claim_radar.py migrate --top 200 --min 1
```

Verified migration paths:

| path | legacy token | migration contract | call |
|------|--------------|--------------------|------|
| `sai` | SAI `0x89d24a…2359` | ScdMcdMigration `0xc73e03…ab849` | `swapSaiToDai(uint256)` |
| `ant_v1` | ANTv1 `0x960b23…88C0` | ANTv2Migrator `0x078BEb…F912D` | `migrate(uint256)` |

Notes: the SAI→DAI swap is still 1:1 on the original `ScdMcdMigration` contract even though the old
web portal is gone; the ANTv1→ANTv2 upgrade officially has **no deadline**. A top-200 scan surfaced
e.g. 10,999 ANTv1 (`0x893608…7cb8`) and 25,000 ANTv1 (`0x41f89c…5f92c`) still unmigrated.

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
