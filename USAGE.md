# Claim Radar — Plain-English Usage Guide

Claim Radar is a scanner + alarm system + transaction builder that finds and helps recover
crypto stuck in broken, abandoned, or shut-down Ethereum projects from ~2016–2018. It reads the
**public ForgottenETH recovery index** and the **live Ethereum blockchain** — it never touches
your private keys, and every transaction it outputs is unsigned (you sign it yourself).

## What it does, in one line per command

| Command | Plain meaning | Expected result |
|---|---|---|
| `check <addr>` | Search the index for your address | amount per project, or "no mapped balances" |
| `top -n 10` | Biggest stuck balances overall | ranked list with ETH totals |
| `watch` | Background alarm on funding + index updates | one alert per meaningful change |
| `sweep --top 300` | Live read of dead contracts (Aave v1, EtherDelta, IDEX v1) | claimable ETH per address |
| `dormant --top 100` | Find high-value, long-inactive addresses | dormant whales, optionally sweep-cross-referenced |
| `migrate --top 300` | Find unmigrated SAI / ANTv1 (still swappable 1:1) | addresses + amounts |
| `claim --protocol X` | Build a simulated, unsigned recovery tx | JSON tx to sign yourself |
| `registry` | Track donation/declared-recovery addresses & alert on funding | live balances + alerts |
| `open` | Free-unclaimed/open public claim pools: scan, check, claim | pool balances + unsigned claim tx |

## Setup

Needs only **Python 3 stdlib** — no package installs.

```bash
git clone https://github.com/Benh218/Crypto.git
cd Crypto
python claim_radar.py --help      # all commands
python claim_radar.py --guide     # this guide, built into the tool
```

Optional: point it at the downloaded ForgottenETH index for `--detail` lookups.

```bash
export CLAIM_RADAR_DATA=/path/to/forgotten-eth/data
```

## Step-by-step

### 1. Find out if you have anything stuck

```bash
python claim_radar.py check 0xYourAddress1 0xYourAddress2 --detail
```

### 2. See what's worth looking at

```bash
python claim_radar.py top -n 20                 # biggest stuck balances
python claim_radar.py sweep --top 300 --min 1   # live claimable ETH today
python claim_radar.py dormant --top 100 --min-eth 100 --inactive-years 3
python claim_radar.py migrate --top 300 --min 1 # unmigrated tokens
```

### 3. Deep-check a single address

```bash
python claim_radar.py sweep --address 0xa7267b534bada4f7a77251ab54e6a78444786c7c
python claim_radar.py dormant --address 0x6d2af065ccb60c0f7e8ec5907c961c42a3447127 --sweep
python claim_radar.py migrate --address 0x893608751d68d046e85802926673cdf2f57f7cb8 --tx
```

### 4. Recover what's yours

```bash
# unsigned claim transaction for a stuck exchange/DAO balance
python claim_radar.py claim --protocol etherdelta --address 0xYourAddress
python claim_radar.py claim --list-paths   # see all supported recovery methods

# migration = two steps (approve, then swap) — both txs printed
python claim_radar.py migrate --address 0xYourAddress --tx
```

Sign the printed JSON in your own wallet (MetaMask, Ledger, MyCrypto, ethers.js) and broadcast.
Claim Radar never holds your keys.

### 5. Keep watch (optional)

```bash
python claim_radar.py watch --init                      # write default config once
python claim_radar.py watch --index --funding --quiet   # run every 10 min via cron
```

Runtime state lives in `~/.claim_radar/` (watch config, state, alerts log, sweep cache).

### 6. Track donation / declared-recovery addresses

Some addresses *publicly declare* what happens to funds sent to them (donation vaults, burn
addresses with a published claim path, successor-designated wallets). Keep those in a registry
and get alerted when they receive funding:

```bash
python claim_radar.py registry --add 0x... --label 'Vault' \
  --source 'https://link-to-terms' --type donation_designated \
  --eligibility open --claim-method 'claim()'

python claim_radar.py registry            # list what you're tracking
python claim_radar.py registry --live     # current on-chain balance of each
python claim_radar.py registry --watch --interval 300   # alert on funding changes
```

### 7. Free-unclaimed / open public claims

The `open` command is the tracker for public claim pools — each pool is tagged by **who may
legally claim it** (`open` = anyone, `proof` = needs a proof, `designated` = you must control the
address). It scans balances, shows terms, and builds an unsigned claim tx **only** for pools
tagged `open`.

```bash
python claim_radar.py open                         # scan all pool balances
python claim_radar.py open --check aave_v1         # terms + eligibility of one pool
python claim_radar.py open --add mypool --contract 0x... --name '...' \
  --eligibility open --claim-method 'claim(uint256)' --terms 'https://...'
python claim_radar.py open --claim-pool mypool --claim-address 0xYourWallet --amount 1.5
python claim_radar.py open --watch --interval 300  # alert when a pool's balance changes
```

Honest limit: genuinely "anyone can claim" pools are rare. If a pool is `designated` or `proof`,
the tool tells you what's required instead of pretending it's claimable — it never generates a
transaction that would take someone else's funds.

## Real findings from development scans

- **Sweep top-300**: ~3,450 ETH claimable (≈1,671 ETH IDEX v1 + ≈1,505 ETH EtherDelta + aETH),
  across 10 addresses. Example hit: 604.22 ETH EtherDelta.
- **Dormant top-100**: ~30 whales; 7 had live sweep hits (946 ETH IDEX since 2020-11,
  604 ETH EtherDelta since 2017, 387 ETH, 278 ETH IDEX since 2019).
- **Migrate top-200**: 25,000 ANTv1 and 10,999 ANTv1 still unmigrated; ~87 SAI across two
  addresses.

## Safety notes

- Reads public data only; never asks for or stores private keys.
- "Mapped" balances come from a public research index — confirm with a live `sweep`/`claim`
  simulation before trusting a number.
- Claimable ≠ free money: recovery still needs the right contract call and gas.
- Nothing here grants access to anyone else's funds; it only surfaces which addresses hold
  stuck value.
