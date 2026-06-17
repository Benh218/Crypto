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
