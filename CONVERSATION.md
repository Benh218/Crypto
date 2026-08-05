# Conversation Summary

## Session: June 17, 2026 — Bitcoin Blockchain Tools

### Context Switch
Started from a completed Brisbane HDD/SSD store project, moved to building Bitcoin blockchain analysis tools.

### Built: BTC Tools (Python CLI Toolkit)

**Modules:**
- **models.py** — Dataclasses for UTXO, AddressInfo, Transaction, DormantAddress
- **config.py** — Config with Blockchair API key, Bitcoin Core RPC, and scanner settings (all env-var driven)
- **fetcher.py** — Three backends: Blockchair (primary), Blockchain.info (fallback), Bitcoin Core RPC (local node). Unified `Fetcher` class auto-falls through on failure.
- **scanner.py** — Dormant coin scanner. Checks known-lost addresses (Satoshi, Patoshi, Silk Road, CryptoLocker) plus arbitrary address lists. Multi-threaded. CSV export.
- **cli.py** — CLI entry: `address`, `utxo-summary`, `dormant`, `known`

### Discussion Highlights
1. **HDD/SSD Brisbane project** — Completed and pushed to `Benh218/HDD-and-SSD-info-and-buy`
2. **Dormant BTC** — ~3–4M BTC estimated permanently lost (15–20% of supply). Satoshi's 1M BTC, landfill drives, deceased owners, burn addresses
3. **Legitimate claiming** — Three paths only: your own lost keys, inheritance, or published challenges
4. **Bitcoin Puzzle** (1000 BTC Challenge) — 160 addresses with escalating key difficulty (Puzzle #66 solved Sep 2024, ~916 BTC still unclaimed). Hunted via GPU brute-force tools (BitCrack, KeyHunt, Pollard's Kangaroo)

### Future Possibilities (not started)
- UTXO dust tracker module
- Wallet recovery (local disk scan for wallet.dat / seed phrases)
- Puzzle-solving module integration

---

## Session: August 4–5, 2026 — Claim Radar "Claim Without Key" Work

### Context Switch
Resumed Claim Radar (Ethereum stuck-funds recovery tool) from an earlier session; the prior
conversation had been recovered from opencode's local session DB.

### Built: Claim Radar Phases 5–5c

**Phase 5a — `registry` command (donation / declared-recovery tracker)**
- Tracks addresses with public declarations (donation vaults, burn-with-claim, successor-designated)
- Types: `donation_designated`, `burn_with_claim`, `successor_designated`, `open_claim`, `funding_target`
- Eligibility: `open`, `proof`, `designated`, `unknown`
- `--add/--remove/--live/--addr/--watch/--export`; state in `~/.claim_radar/registry.json`

**Phase 5b — `open` command (free-unclaimed / open public claim pools)**
- Public claim pool registry + live balance scanner + unsigned-claim builder
- Only builds claim txs for pools tagged `eligibility=open`; everything else prints the requirement
- Categories: `airdrop`, `bounty`, `redemption`, `burn_claim`, `public_vault`
- Seeded with the 3 real, on-chain-verified recovery mechanisms (Aave v1, EtherDelta v2, IDEX v1),
  honestly tagged `designated`

**Phase 5c — `claimcheck` command (live claim/airdrop window tracker)** — commit `befd339`
- Pre-seeded with 15 real live windows from a public tracker (Warden ~5d left, Superform ~10d,
  Infinex ~184d, Pharos until 2026-10-25, plus GRVT, Arcium, DeepBook, Jupiter, Solstice,
  BasedApp, Plume, Nexus, Satsuma, AIW3, ETHGAS)
- `--check`, `--address` (reminder only), `--watch --soon-days N` alerts, `--add/--remove`, `--json`
- State in `~/.claim_radar/claimcheck.json` + `claimcheck_state.json`

### Bug Fixes / Restoration
- Restored dropped `http_json()`, `wei_to_eth()`, `ALERT_LOG` helpers (were causing NameError in
  watch/sweep/claim)
- Guarded `top_addresses()` / `mapped_for()` against missing `data/index_shards`

### Live On-Chain Findings (verified)
- EtherDelta v2 contract holds ~15,208 ETH total; docs-example address `0xa7267b…7c7c` = 604.22 ETH
- `0x893608…7cb8` = 10,999 ANTv1 unmigrated (1:1 to ANTv2, no deadline)
- The DAO WithdrawDAO wrapper (`0xbb9bc2…9413`) = 0.0006 ETH

### Key Discussion: "Claim without a key"
- Honest stance maintained: every stuck coin is tied to an address with an owner; possession of the
  key IS ownership. No "abandoned property" mechanism exists on Ethereum.
- Burn addresses are unrecoverable by design. Only three legitimate paths: your own keys,
  open-to-anyone pools (registered in `open`, currently zero verified), and airdrops tied to
  addresses you control.
- Refused to hunt addresses for the user to "acquire" — that would be theft; the tool is built to
  prevent pretending otherwise.
- User created no ETH address yet; live sweep demo used the docs-example address only.

### Notes
- GitHub token embedded in remote URL — rotate it.
- `dormant`/`top`/`sweep --top` need the ForgottenETH `data/` folder (`CLAIM_RADAR_DATA`) which is
  not cloned locally; `--address` live reads work without it.
