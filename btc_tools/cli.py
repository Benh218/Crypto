import argparse
import logging
import sys

from .config import Config
from .fetcher import Fetcher
from .scanner import DormantScanner, KNOWN_DORMANT

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
)

log = logging.getLogger("btc_tools")


def cmd_address(args, fetcher: Fetcher):
    info = fetcher.get_address(args.address)
    if not info:
        print("Address not found or API error.")
        return
    print(f"Address:       {info.address}")
    print(f"Balance:       {info.balance_btc:.8f} BTC")
    print(f"Total Received:{info.total_received_btc:.8f} BTC")
    print(f"Total Sent:    {info.total_sent_btc:.8f} BTC")
    print(f"Transactions:  {info.tx_count}")
    print(f"UTXOs:         {len(info.utxos)}")
    if info.first_seen:
        print(f"First Seen:    {info.first_seen.date()}")
    if info.last_active:
        print(f"Last Active:   {info.last_active.date()}")

    if info.utxos:
        print(f"\nTop UTXOs:")
        for u in sorted(info.utxos, key=lambda x: x.amount_btc, reverse=True)[:5]:
            print(f"  {u.amount_btc:.8f} BTC  {u.txid[:16]}...:{u.vout}")


def cmd_utxo_summary(args, fetcher: Fetcher):
    summary = fetcher.get_utxo_set_summary()
    print(f"Block Height:  {summary.get('block_height', '?')}")
    print(f"UTXO Count:    {summary.get('utxo_count', '?'):,}")
    total = summary.get("total_btc", 0)
    print(f"Total BTC:     {total:.8f}  ({total:,.0f} BTC)")


def cmd_dormant(args, fetcher: Fetcher):
    config = Config.from_env()
    scanner = DormantScanner(fetcher, config)

    if args.use_known:
        results = scanner.check_known_dormant()
    elif args.addr_file:
        with open(args.addr_file) as f:
            addrs = [line.strip() for line in f if line.strip()]
        results = scanner.scan_address_list(addrs)
    elif args.addresses:
        results = scanner.scan_address_list(args.addresses)
    else:
        print("Nothing to scan. Use --known, --addr-file, or --addresses.")
        return

    print(scanner.report(results, top_n=args.top))

    if args.csv:
        scanner.to_csv(results, args.csv)
        print(f"Saved to {args.csv}")


def cmd_known(args, fetcher: Fetcher):
    print(f"{'Address':<36} {'Tag':<25} {'First Seen'}")
    print("-" * 80)
    for addr, tag, first in KNOWN_DORMANT:
        print(f"{addr:<36} {tag:<25} {first}")


def main():
    parser = argparse.ArgumentParser(
        description="Bitcoin blockchain data fetcher & dormant coin scanner"
    )
    parser.add_argument("--api-key", help="Blockchair API key")
    sub = parser.add_subprocess = parser.add_subparsers(dest="command")

    p_addr = sub.add_parser("address", help="Look up a Bitcoin address")
    p_addr.add_argument("address")

    p_utxo = sub.add_parser("utxo-summary", help="UTXO set summary")

    p_dormant = sub.add_parser("dormant", help="Scan for dormant coins")
    p_dormant.add_argument("--known", dest="use_known", action="store_true",
                           help="Scan known dormant addresses")
    p_dormant.add_argument("--addr-file", help="File with addresses to scan (one per line)")
    p_dormant.add_argument("--addresses", nargs="+", help="Specific addresses to scan")
    p_dormant.add_argument("--top", type=int, default=20, help="Show top N results")
    p_dormant.add_argument("--csv", help="Export results to CSV file")

    p_known = sub.add_parser("known", help="List known dormant addresses")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    config = Config.from_env()
    if args.api_key:
        config.blockchair_api_key = args.api_key
    fetcher = Fetcher(config)

    commands = {
        "address": cmd_address,
        "utxo-summary": cmd_utxo_summary,
        "dormant": cmd_dormant,
        "known": cmd_known,
    }
    commands[args.command](args, fetcher)


if __name__ == "__main__":
    main()
