import time
import logging
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional

import requests

from .models import AddressInfo, UTXO, Transaction
from .config import Config, RPCConfig

log = logging.getLogger(__name__)

BTC_SAT = Decimal("100_000_000")


def _sat_to_btc(sat: int) -> Decimal:
    return Decimal(str(sat)) / BTC_SAT


def _parse_timestamp(ts: Optional[int]) -> Optional[datetime]:
    if ts and ts > 0:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


class BlockchairBackend:
    BASE = "https://api.blockchair.com/bitcoin"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self._last_call = 0.0

    def _rate_limit(self):
        elapsed = time.time() - self._last_call
        if elapsed < 0.1:
            time.sleep(0.1 - elapsed)
        self._last_call = time.time()

    def _get(self, path: str, params: dict | None = None) -> dict:
        self._rate_limit()
        params = dict(params or {})
        if self.api_key:
            params["key"] = self.api_key
        url = f"{self.BASE}/{path}"
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_address(self, address: str) -> AddressInfo | None:
        data = self._get(f"dashboards/address/{address}")
        addr = data.get("data", {}).get(address, {})
        if not addr:
            return None
        info = addr.get("address", {})
        utxos_raw = addr.get("utxo", [])

        utxos: list[UTXO] = []
        for u in utxos_raw:
            utxos.append(UTXO(
                txid=u["transaction_hash"],
                vout=u["index"],
                address=address,
                amount_btc=_sat_to_btc(u["value"]),
                script_type=u.get("script_hex", "")[:20],
                confirmations=0,
                height=u.get("block_id"),
            ))

        return AddressInfo(
            address=address,
            balance_btc=_sat_to_btc(sum(u["value"] for u in utxos_raw)),
            total_received_btc=_sat_to_btc(info.get("received", 0)),
            total_sent_btc=_sat_to_btc(info.get("spent", 0)),
            tx_count=info.get("transaction_count", 0) + info.get("spending_transaction_count", 0),
            first_seen=_parse_timestamp(info.get("first_seen_receive")),
            last_active=_parse_timestamp(info.get("last_seen_receive") or info.get("last_seen_spend")),
            utxos=utxos,
        )

    def get_utxo_set_stats(self) -> dict:
        data = self._get("stats")
        chain = data.get("data", {})
        return {
            "utxo_count": chain.get("utxo_count", 0),
            "block_height": chain.get("best_block_height", 0),
            "total_btc": Decimal(str(chain.get("circulation", 0))),
        }

    def get_block(self, height: int) -> dict | None:
        data = self._get(f"dashboards/block/{height}")
        block = data.get("data", {}).get(str(height))
        return block


class BlockchainInfoBackend:
    BASE = "https://blockchain.info"

    def _get(self, path: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        params["cors"] = "true"
        url = f"{self.BASE}/{path}"
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_address(self, address: str) -> AddressInfo | None:
        data = self._get(f"address/{address}", {"format": "json"})
        if "address" not in data:
            return None

        utxos: list[UTXO] = []
        for txref in data.get("txrefs", []):
            if txref.get("tx_output_n", -1) >= 0 and not txref.get("spent", True):
                utxos.append(UTXO(
                    txid=txref["tx_hash"],
                    vout=txref["tx_output_n"],
                    address=address,
                    amount_btc=Decimal(str(txref["value"])) / BTC_SAT,
                    script_type="",
                    confirmations=txref.get("confirmations", 0),
                    height=txref.get("block_height"),
                ))

        return AddressInfo(
            address=address,
            balance_btc=Decimal(str(data.get("final_balance", 0))) / BTC_SAT,
            total_received_btc=Decimal(str(data.get("total_received", 0))) / BTC_SAT,
            total_sent_btc=Decimal(str(data.get("total_sent", 0))) / BTC_SAT,
            tx_count=data.get("n_tx", 0),
            first_seen=None,
            last_active=None,
            utxos=utxos,
        )

    def get_latest_block_height(self) -> int:
        data = self._get("latestblock")
        return data.get("height", 0)

    def get_block_hash(self, height: int) -> str | None:
        try:
            resp = requests.get(
                f"{self.BASE}/block-height/{height}",
                params={"format": "json"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            blocks = data.get("blocks", [])
            return blocks[0]["hash"] if blocks else None
        except Exception:
            return None


class BitcoinRPCBackend:
    def __init__(self, config: RPCConfig):
        self.config = config
        proto = "https" if config.use_ssl else "http"
        self.url = f"{proto}://{config.host}:{config.port}"
        self.session = requests.Session()
        self.session.auth = (config.user, config.password)
        self._id = 0

    def _call(self, method: str, *params) -> dict:
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": list(params),
        }
        resp = self.session.post(
            self.url,
            json=payload,
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data and data["error"]:
            raise RuntimeError(f"RPC error: {data['error']}")
        return data["result"]

    def get_utxo_set_info(self) -> dict:
        return self._call("gettxoutsetinfo")

    def get_blockchain_info(self) -> dict:
        return self._call("getblockchaininfo")

    def get_utxo_for_address(self, address: str) -> list[UTXO]:
        utxos: list[UTXO] = []
        try:
            result = self._call("listunspent", 0, 9999999, [address])
            for u in result:
                utxos.append(UTXO(
                    txid=u["txid"],
                    vout=u["vout"],
                    address=u["address"],
                    amount_btc=Decimal(str(u["amount"])),
                    script_type=u.get("scriptPubKey", "")[:20],
                    confirmations=u.get("confirmations", 0),
                ))
        except Exception as e:
            log.warning("listunspent failed (wallet may not be loaded): %s", e)
        return utxos

    def get_raw_transaction(self, txid: str) -> dict:
        return self._call("getrawtransaction", txid, True)

    def get_block_stats(self, height: int) -> dict:
        return self._call("getblockstats", height)


class Fetcher:
    def __init__(self, config: Config | None = None):
        self.config = config or Config.from_env()
        self.blockchair = BlockchairBackend(self.config.blockchair_api_key)
        self.blockchain_info = BlockchainInfoBackend()
        self.rpc: BitcoinRPCBackend | None = None
        if self.config.rpc.password:
            self.rpc = BitcoinRPCBackend(self.config.rpc)

    def get_address(self, address: str) -> AddressInfo | None:
        for backend_name in ["blockchair", "blockchain_info"]:
            backend = getattr(self, backend_name)
            try:
                result = backend.get_address(address)
                if result:
                    return result
            except Exception as e:
                log.warning("%s backend failed for %s: %s", backend_name, address, e)
        return None

    def get_utxo_set_summary(self) -> dict:
        info = {"utxo_count": 0, "block_height": 0, "total_btc": Decimal("0")}
        if self.rpc:
            try:
                rpc_info = self.rpc.get_utxo_set_info()
                if rpc_info:
                    info.update({
                        "utxo_count": rpc_info.get("txouts", 0),
                        "block_height": rpc_info.get("height", 0),
                        "total_btc": Decimal(str(rpc_info.get("total_amount", 0))),
                    })
                    return info
            except Exception as e:
                log.warning("RPC gettxoutsetinfo failed: %s", e)
        try:
            blockchair_info = self.blockchair.get_utxo_set_stats()
            if blockchair_info:
                info.update(blockchair_info)
        except Exception as e:
            log.warning("Blockchair stats failed: %s", e)
        return info

    def get_address_batch(self, addresses: list[str]) -> dict[str, AddressInfo | None]:
        results: dict[str, AddressInfo | None] = {}
        for addr in addresses:
            results[addr] = self.get_address(addr)
        return results
