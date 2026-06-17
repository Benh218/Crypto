from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BackendConfig:
    name: str
    enabled: bool = True
    rate_limit_per_sec: int = 10


@dataclass
class RPCConfig:
    host: str = "127.0.0.1"
    port: int = 8332
    user: str = "bitcoin"
    password: str = ""
    use_ssl: bool = False
    timeout: int = 30


@dataclass
class ScannerConfig:
    min_btc: float = 1.0
    min_dormant_years: int = 5
    max_addresses: int = 1000
    threads: int = 4


class Config:
    blockchair_api_key: str = ""
    blockchain_api_key: str = ""
    rpc: RPCConfig = RPCConfig()
    scanner: ScannerConfig = ScannerConfig()
    backends: list[BackendConfig] = field(default_factory=lambda: [
        BackendConfig("blockchair"),
        BackendConfig("blockchain_info"),
        BackendConfig("bitcoin_rpc"),
    ])

    @classmethod
    def from_env(cls) -> "Config":
        import os
        cfg = cls()
        cfg.blockchair_api_key = os.getenv("BLOCKCHAIR_API_KEY", "")
        cfg.blockchain_api_key = os.getenv("BLOCKCHAIN_API_KEY", "")
        cfg.rpc.user = os.getenv("BTC_RPC_USER", "bitcoin")
        cfg.rpc.password = os.getenv("BTC_RPC_PASSWORD", "")
        cfg.rpc.host = os.getenv("BTC_RPC_HOST", "127.0.0.1")
        cfg.rpc.port = int(os.getenv("BTC_RPC_PORT", "8332"))
        return cfg
