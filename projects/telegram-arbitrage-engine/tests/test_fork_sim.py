import pytest

from arb_engine.fork_sim import AnvilForkSimulator, ForkSimulationError


TO = "0x" + "1" * 40
SENDER = "0x" + "2" * 40
HASH = "0x" + "a" * 64


def test_fork_simulation_checks_anvil_chain_block_and_trace(monkeypatch):
    simulator = AnvilForkSimulator()
    calls = []

    def fake(method, params):
        calls.append((method, params))
        if method == "web3_clientVersion":
            return "anvil/v1.3.0"
        if method == "eth_chainId":
            return "0x1"
        if method == "eth_blockNumber":
            return "0x64"
        if method == "eth_getBlockByNumber":
            assert params == ["0x64", False]
            return {"hash": HASH}
        if method == "eth_call":
            assert params[1] == "0x64"
            assert params[0]["to"] == TO
            assert params[0]["from"] == SENDER
            return "0x1234"
        if method == "eth_estimateGas":
            return "0x186a0"
        if method == "debug_traceCall":
            assert params[2]["tracer"] == "callTracer"
            return {
                "type": "CALL", "from": SENDER, "to": TO,
                "gas": "0x20000", "gasUsed": "0x186a0",
                "output": "0x1234", "calls": [{"type": "STATICCALL"}],
            }
        raise AssertionError(f"Unexpected RPC method: {method}")

    monkeypatch.setattr(simulator, "_rpc", fake)
    result = simulator.simulate_call(
        expected_chain_id=1, fork_block=100, to=TO,
        from_address=SENDER, data="0x12345678", include_trace=True,
    )
    assert result.estimated_gas == 100_000
    assert result.call_output == "0x1234"
    assert result.trace["calls_count"] == 1
    assert result.classification.startswith("anvil_fork")
    assert "eth_sendTransaction" not in [method for method, _ in calls]
    assert "eth_sendRawTransaction" not in [method for method, _ in calls]


def test_fork_simulation_rejects_non_anvil_endpoint(monkeypatch):
    simulator = AnvilForkSimulator()
    monkeypatch.setattr(simulator, "_rpc", lambda method, params: "geth/v1.15")
    with pytest.raises(ForkSimulationError, match="did not identify itself as Anvil"):
        simulator.simulate_call(
            expected_chain_id=1, fork_block=100, to=TO, data="0x12345678",
        )


def test_fork_simulation_rejects_chain_or_head_mismatch(monkeypatch):
    simulator = AnvilForkSimulator()
    monkeypatch.setattr(
        simulator, "_rpc",
        lambda method, params: {
            "web3_clientVersion": "anvil/v1",
            "eth_chainId": "0x89",
        }.get(method),
    )
    with pytest.raises(ForkSimulationError, match="Wrong fork chain ID"):
        simulator.simulate_call(
            expected_chain_id=1, fork_block=100, to=TO, data="0x12345678",
        )


def test_fork_simulation_rejects_advanced_head(monkeypatch):
    simulator = AnvilForkSimulator()
    monkeypatch.setattr(
        simulator, "_rpc",
        lambda method, params: {
            "web3_clientVersion": "anvil/v1",
            "eth_chainId": "0x1",
            "eth_blockNumber": "0x65",
        }.get(method),
    )
    with pytest.raises(ForkSimulationError, match="differs from requested"):
        simulator.simulate_call(
            expected_chain_id=1, fork_block=100, to=TO, data="0x12345678",
        )


def test_fork_simulation_rejects_trace_failure(monkeypatch):
    simulator = AnvilForkSimulator()

    def fake(method, params):
        if method == "web3_clientVersion":
            return "anvil/v1"
        if method == "eth_chainId":
            return "0x1"
        if method == "eth_blockNumber":
            return "0x64"
        if method == "eth_getBlockByNumber":
            return {"hash": HASH}
        if method == "eth_call":
            return "0x"
        if method == "eth_estimateGas":
            return "0x5208"
        if method == "debug_traceCall":
            return {"failed": True, "error": "execution reverted"}
        raise AssertionError(method)

    monkeypatch.setattr(simulator, "_rpc", fake)
    with pytest.raises(ForkSimulationError, match="execution failure"):
        simulator.simulate_call(
            expected_chain_id=1, fork_block=100, to=TO, data="0x12345678",
        )


@pytest.mark.parametrize("endpoint", [
    "https://127.0.0.1:8545",
    "http://0.0.0.0:8545",
    "http://192.168.1.2:8545",
    "http://user:pass@127.0.0.1:8545",
    "http://127.0.0.1:8545?token=secret",
])
def test_fork_simulation_only_accepts_loopback_http_without_credentials(endpoint):
    with pytest.raises(ValueError, match="loopback"):
        AnvilForkSimulator(endpoint)


def test_fork_simulation_validates_calldata_addresses_and_gas():
    simulator = AnvilForkSimulator()
    with pytest.raises(ValueError, match="calldata"):
        simulator.simulate_call(
            expected_chain_id=1, fork_block=100, to=TO, data="0x123",
        )
    with pytest.raises(ValueError, match="non-zero"):
        simulator.simulate_call(
            expected_chain_id=1, fork_block=100, to="0x" + "0" * 40, data="0x12345678",
        )
    with pytest.raises(ValueError, match="gas_limit"):
        simulator.simulate_call(
            expected_chain_id=1, fork_block=100, to=TO, data="0x12345678", gas_limit=1,
        )


def test_fork_simulation_rejects_estimate_above_supplied_gas_limit(monkeypatch):
    simulator = AnvilForkSimulator()

    def fake(method, params):
        if method == "web3_clientVersion":
            return "anvil/v1"
        if method == "eth_chainId":
            return "0x1"
        if method == "eth_blockNumber":
            return "0x64"
        if method == "eth_getBlockByNumber":
            return {"hash": HASH}
        if method == "eth_call":
            return "0x"
        if method == "eth_estimateGas":
            return "0x10000"
        raise AssertionError(method)

    monkeypatch.setattr(simulator, "_rpc", fake)
    with pytest.raises(ForkSimulationError, match="exceeds the caller-supplied"):
        simulator.simulate_call(
            expected_chain_id=1, fork_block=100, to=TO,
            data="0x12345678", gas_limit=30_000,
            include_trace=False,
        )


def test_fork_simulation_rpc_allowlist_excludes_state_changes():
    simulator = AnvilForkSimulator()
    with pytest.raises(ForkSimulationError, match="not permitted"):
        simulator._rpc("eth_sendTransaction", [])
    with pytest.raises(ForkSimulationError, match="not permitted"):
        simulator._rpc("anvil_impersonateAccount", [])
