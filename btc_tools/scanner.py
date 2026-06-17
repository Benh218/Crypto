import csv
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from .models import AddressInfo, DormantAddress
from .fetcher import Fetcher
from .config import Config

log = logging.getLogger(__name__)


KNOWN_DORMANT: list[tuple[str, str, str]] = [
    ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "Satoshi genesis", "2009-01-03"),
    ("12c6DSiU4Rq3P4ZxziKxzrL5LmMBrzjrJX", "Satoshi block 9", "2009-01-12"),
    ("1HLoD9E4SDFFPDiYfNYnkBLQ85Y51J3Zb1", "Patoshi pattern", "2009"),
    ("1LdRq8WzLo9Exe1dWKpDmaWwFRnemY6vg9", "Patoshi pattern", "2009"),
    ("1AC4fMwgY8j9onSbXEWeFcRpxJdpK2hRkM", "Patoshi pattern", "2009"),
    ("1MSPM5KJPuuKk74ntKvV1ofAhbEuT2yXh", "Early miner", "2010"),
    ("16Z7JN7KgPSdx6WAgRyf47o1Pscnq4FVDa", "Known lost wallet", "2010"),
    ("1FeexV6bAHb8ybZjqQMjJrcCrHGW9sb6uNo", "Silk Road seizure", "2011-06"),
    ("1FfmbHfnpaZjKFvyi1okTjJJusf455wsYB", "CryptoLocker wallet", "2013-09"),
    ("1Eym7xyJjz6PQQ1N6Wh2iZXpyLVQNHK8j3", "Early Bitcointalk", "2010"),
]


class DormantScanner:
    def __init__(self, fetcher: Fetcher, config: Config | None = None):
        self.fetcher = fetcher
        self.config = config or Config.from_env()

    def check_known_dormant(self, min_btc: Decimal | None = None) -> list[DormantAddress]:
        results: list[DormantAddress] = []
        threshold = Decimal(str(self.config.scanner.min_btc))

        for addr, tag, first_seen_str in KNOWN_DORMANT:
            info = self.fetcher.get_address(addr)
            if not info:
                continue
            if min_btc is not None and info.balance_btc < min_btc:
                continue
            if info.balance_btc < threshold and min_btc is None:
                continue

            last_active = info.last_active or datetime.now(timezone.utc) - timedelta(days=365 * 10)
            dormant_years = (datetime.now(timezone.utc) - last_active).days / 365.0

            if dormant_years < self.config.scanner.min_dormant_years:
                continue

            results.append(DormantAddress(
                address=addr,
                balance_btc=info.balance_btc,
                last_active=last_active,
                dormant_years=round(dormant_years, 1),
                tx_count=info.tx_count,
                first_seen=info.first_seen,
                utxo_count=len(info.utxos),
                tags=[tag],
            ))

        results.sort(key=lambda x: x.balance_btc, reverse=True)
        return results

    def scan_address_list(
        self,
        addresses: list[str],
        label_fn: Callable[[str], list[str]] | None = None,
    ) -> list[DormantAddress]:
        results: list[DormantAddress] = []
        threshold = Decimal(str(self.config.scanner.min_btc))

        def check(addr: str) -> DormantAddress | None:
            info = self.fetcher.get_address(addr)
            if not info:
                return None
            if info.balance_btc < threshold:
                return None
            last_active = info.last_active or datetime.now(timezone.utc) - timedelta(days=365 * 10)
            dormant_years = (datetime.now(timezone.utc) - last_active).days / 365.0
            if dormant_years < self.config.scanner.min_dormant_years:
                return None
            return DormantAddress(
                address=addr,
                balance_btc=info.balance_btc,
                last_active=last_active,
                dormant_years=round(dormant_years, 1),
                tx_count=info.tx_count,
                first_seen=info.first_seen,
                utxo_count=len(info.utxos),
                tags=label_fn(addr) if label_fn else [],
            )

        max_workers = self.config.scanner.threads
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(check, addr): addr for addr in addresses[:self.config.scanner.max_addresses]}
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                except Exception as e:
                    log.warning("Failed to scan %s: %s", futures[future], e)

        results.sort(key=lambda x: x.balance_btc, reverse=True)
        return results

    @staticmethod
    def report(results: list[DormantAddress], top_n: int = 20) -> str:
        lines = [
            f"Dormant Address Scan Results ({len(results)} found)",
            "=" * 60,
        ]
        for r in results[:top_n]:
            btc = f"{r.balance_btc:.8f}"
            tags = f" [{', '.join(r.tags)}]" if r.tags else ""
            lines.append(
                f"  {r.address[:20]}... | {btc} BTC | {r.dormant_years:.0f}yr dormant{tags}"
            )
        total = sum(r.balance_btc for r in results)
        lines.append("-" * 60)
        lines.append(f"  Total BTC: {total:.8f} ({total:,.0f} BTC)")
        lines.append(f"  Addresses: {len(results)}")
        return "\n".join(lines)

    @staticmethod
    def to_csv(results: list[DormantAddress], path: str):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["address", "balance_btc", "last_active", "dormant_years",
                         "tx_count", "first_seen", "utxo_count", "tags"])
            for r in results:
                w.writerow([
                    r.address, r.balance_btc, r.last_active.isoformat() if r.last_active else "",
                    r.dormant_years, r.tx_count,
                    r.first_seen.isoformat() if r.first_seen else "",
                    r.utxo_count, "; ".join(r.tags),
                ])
