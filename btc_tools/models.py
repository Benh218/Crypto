from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass
class UTXO:
    txid: str
    vout: int
    address: str
    amount_btc: Decimal
    script_type: str
    confirmations: int
    locktime: Optional[int] = None
    height: Optional[int] = None
    timestamp: Optional[datetime] = None


@dataclass
class AddressInfo:
    address: str
    balance_btc: Decimal
    total_received_btc: Decimal
    total_sent_btc: Decimal
    tx_count: int
    first_seen: Optional[datetime] = None
    last_active: Optional[datetime] = None
    utxos: list[UTXO] = field(default_factory=list)


@dataclass
class Transaction:
    txid: str
    block_hash: Optional[str] = None
    block_height: Optional[int] = None
    timestamp: Optional[datetime] = None
    inputs: list[dict] = field(default_factory=list)
    outputs: list[dict] = field(default_factory=list)
    total_input_btc: Decimal = Decimal("0")
    total_output_btc: Decimal = Decimal("0")
    fee_btc: Decimal = Decimal("0")
    size_bytes: int = 0
    confirmations: int = 0


@dataclass
class DormantAddress:
    address: str
    balance_btc: Decimal
    last_active: datetime
    dormant_years: float
    tx_count: int
    first_seen: Optional[datetime] = None
    utxo_count: int = 0
    tags: list[str] = field(default_factory=list)
