from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from arb_engine.rpc import RpcReadError


def smoke_module():
    repository_root = Path(__file__).resolve().parents[3]
    script = repository_root / "scripts" / "v3_live_readonly_smoke.py"
    spec = importlib.util.spec_from_file_location("v3_live_readonly_smoke", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return script, module


def test_live_v3_smoke_script_parses_and_contains_no_transaction_submission() -> None:
    script, _ = smoke_module()
    source = script.read_text(encoding="utf-8")
    ast.parse(source, filename=str(script))
    assert "eth_sendTransaction" not in source
    assert "eth_sendRawTransaction" not in source
    assert "live_execution" in source
    assert "transaction_submitted" in source
    assert "re.fullmatch" in source
    assert "250_002" in source


def test_v3_smoke_only_fails_over_for_transport_failures() -> None:
    _, module = smoke_module()
    assert module.is_retryable_transport_error(RpcReadError("EVM RPC HTTP error: 403"))
    assert module.is_retryable_transport_error(RpcReadError("EVM RPC HTTP error: 429"))
    assert module.is_retryable_transport_error(RpcReadError("EVM RPC HTTP error: 503"))
    assert module.is_retryable_transport_error(RpcReadError("EVM RPC request failed: URLError"))
    assert not module.is_retryable_transport_error(RpcReadError("Malformed V3 ABI response"))
    assert not module.is_retryable_transport_error(RpcReadError("V3 pool factory mismatch"))
    assert not module.is_retryable_transport_error(RpcReadError("V3 block is no longer canonical"))


def test_v3_smoke_endpoint_override_is_unique_and_does_not_embed_credentials(monkeypatch) -> None:
    _, module = smoke_module()
    monkeypatch.setenv(
        "V3_SMOKE_RPC_URLS",
        "https://eth.drpc.org,https://eth.drpc.org,https://public.1rpc.io/eth",
    )
    assert module.configured_endpoints() == (
        "https://eth.drpc.org",
        "https://public.1rpc.io/eth",
    )


def test_v3_smoke_token_runtime_bytecode_must_be_strict_hex() -> None:
    _, module = smoke_module()
    assert module._has_valid_runtime_bytecode("0x6001600055")
    assert not module._has_valid_runtime_bytecode("0x")
    assert not module._has_valid_runtime_bytecode("0xzz")
    assert not module._has_valid_runtime_bytecode("0x123")
    assert not module._has_valid_runtime_bytecode("0x" + "60" * 125001)
