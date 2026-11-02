// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import "../src/FlashSwapHarness.sol";

interface VmPinnedFork {
    function createSelectFork(string calldata urlOrAlias, uint256 blockNumber) external returns (uint256 forkId);
    function envString(string calldata name) external returns (string memory value);
    function envUint(string calldata name) external returns (uint256 value);
    function envBytes32(string calldata name) external returns (bytes32 value);
    function expectRevert() external;
}

interface IV2RouterFactoryPinned {
    function factory() external view returns (address);
}

interface IV2FactoryPinned {
    function getPair(address tokenA, address tokenB) external view returns (address);
}

interface IV2PairPinned {
    function factory() external view returns (address);
    function token0() external view returns (address);
    function token1() external view returns (address);
    function getReserves() external view returns (uint112, uint112, uint32);
}

contract PinnedEthereumV2FlashSwapForkTest {
    VmPinnedFork private constant vm =
        VmPinnedFork(address(uint160(uint256(keccak256("hevm cheat code")))));

    address private constant UNISWAP_FACTORY =
        0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f;
    address private constant UNISWAP_ROUTER =
        0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D;
    address private constant SUSHI_FACTORY =
        0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac;
    address private constant SUSHI_ROUTER =
        0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F;
    address private constant USDC =
        0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address private constant WETH =
        0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;
    uint256 private constant BPS = 10_000;
    uint256 private constant FEE_BPS = 30;

    event RouteCallGas(
        uint256 indexed blockNumber,
        address indexed borrowedToken,
        uint256 amountBorrowed,
        uint256 harnessCallGas,
        bool succeeded
    );

    event ForkSnapshot(
        uint256 indexed blockNumber,
        bytes32 indexed blockHash,
        bytes32 parentHash,
        address sourcePair,
        address destinationPair,
        address borrowedToken,
        uint256 amountBorrowed,
        uint256 expectedGrossProfit
    );

    function testPinnedForkExecutesOrAtomicallyRejectsRealV2PairRoute() public {
        string memory rpcUrl = vm.envString("MAINNET_RPC_URL");
        uint256 forkBlock = vm.envUint("FORK_BLOCK");
        bytes32 forkBlockHash = vm.envBytes32("FORK_BLOCK_HASH");
        bytes32 forkParentHash = vm.envBytes32("FORK_PARENT_HASH");
        vm.createSelectFork(rpcUrl, forkBlock);

        require(block.chainid == 1, "NOT_ETHEREUM_MAINNET_FORK");
        require(block.number == forkBlock, "FORK_BLOCK_MISMATCH");
        require(forkBlockHash != bytes32(0), "MISSING_FORK_BLOCK_HASH");
        require(forkParentHash != bytes32(0), "MISSING_FORK_PARENT_HASH");
        require(blockhash(block.number - 1) == forkParentHash, "FORK_PARENT_HASH_MISMATCH");

        IV2FactoryPinned sourceFactory = IV2FactoryPinned(UNISWAP_FACTORY);
        IV2FactoryPinned destinationFactory = IV2FactoryPinned(SUSHI_FACTORY);
        address sourcePairAddress = sourceFactory.getPair(USDC, WETH);
        address destinationPairAddress = destinationFactory.getPair(USDC, WETH);
        require(sourcePairAddress != address(0), "UNISWAP_PAIR_MISSING");
        require(destinationPairAddress != address(0), "SUSHI_PAIR_MISSING");
        require(sourcePairAddress != destinationPairAddress, "PAIR_ADDRESSES_IDENTICAL");

        require(UNISWAP_FACTORY.code.length > 0, "UNISWAP_FACTORY_NO_CODE");
        require(UNISWAP_ROUTER.code.length > 0, "UNISWAP_ROUTER_NO_CODE");
        require(SUSHI_FACTORY.code.length > 0, "SUSHI_FACTORY_NO_CODE");
        require(SUSHI_ROUTER.code.length > 0, "SUSHI_ROUTER_NO_CODE");
        require(sourcePairAddress.code.length > 0, "UNISWAP_PAIR_NO_CODE");
        require(destinationPairAddress.code.length > 0, "SUSHI_PAIR_NO_CODE");
        require(IV2RouterFactoryPinned(UNISWAP_ROUTER).factory() == UNISWAP_FACTORY, "UNISWAP_ROUTER_FACTORY");
        require(IV2RouterFactoryPinned(SUSHI_ROUTER).factory() == SUSHI_FACTORY, "SUSHI_ROUTER_FACTORY");

        IV2PairPinned sourcePair = IV2PairPinned(sourcePairAddress);
        IV2PairPinned destinationPair = IV2PairPinned(destinationPairAddress);
        require(sourcePair.factory() == UNISWAP_FACTORY, "UNISWAP_PAIR_FACTORY");
        require(destinationPair.factory() == SUSHI_FACTORY, "SUSHI_PAIR_FACTORY");
        require(sourcePair.token0() == USDC && sourcePair.token1() == WETH, "UNISWAP_TOKEN_ORDER");
        require(destinationPair.token0() == USDC && destinationPair.token1() == WETH, "SUSHI_TOKEN_ORDER");
        require(USDC.code.length > 0 && WETH.code.length > 0, "TOKEN_NO_CODE");

        (uint112 sourceUsdcRaw, uint112 sourceWethRaw,) = sourcePair.getReserves();
        (uint112 destinationUsdcRaw, uint112 destinationWethRaw,) = destinationPair.getReserves();
        require(
            sourceUsdcRaw > 0 && sourceWethRaw > 0
                && destinationUsdcRaw > 0 && destinationWethRaw > 0,
            "EMPTY_POOL"
        );

        (address borrowedToken, uint256 bestAmount, uint256 bestProfit) = _findBestRoute(
            sourceUsdcRaw,
            sourceWethRaw,
            destinationUsdcRaw,
            destinationWethRaw
        );
        require(bestAmount > 0, "NO_VALID_BORROW_AMOUNT");

        emit ForkSnapshot(
            forkBlock,
            forkBlockHash,
            forkParentHash,
            sourcePairAddress,
            destinationPairAddress,
            borrowedToken,
            bestAmount,
            bestProfit
        );

        FlashSwapHarness harness = new FlashSwapHarness(UNISWAP_FACTORY, SUSHI_FACTORY);
        (uint112 source0Before, uint112 source1Before,) = sourcePair.getReserves();
        (uint112 destination0Before, uint112 destination1Before,) = destinationPair.getReserves();

        bytes memory executeCall = abi.encodeWithSelector(
            harness.execute.selector,
            sourcePairAddress,
            destinationPairAddress,
            borrowedToken,
            bestAmount,
            1,
            block.timestamp + 60
        );
        uint256 gasBeforeCall = gasleft();
        (bool callSucceeded, bytes memory returnData) = address(harness).call(executeCall);
        uint256 harnessCallGas = gasBeforeCall - gasleft();
        emit RouteCallGas(
            forkBlock, borrowedToken, bestAmount, harnessCallGas, callSucceeded
        );

        if (bestProfit > 0) {
            require(callSucceeded, "FORK_ROUTE_EXECUTION_FAILED");
            uint256 actualProfit = abi.decode(returnData, (uint256));
            require(actualProfit == bestProfit, "FORK_PROFIT_MISMATCH");
            require(actualProfit > 0, "FORK_ROUTE_NOT_PROFITABLE");
        } else {
            require(!callSucceeded, "EXPECTED_UNPROFITABLE_ROUTE_TO_REVERT");
            (uint112 source0After, uint112 source1After,) = sourcePair.getReserves();
            (uint112 destination0After, uint112 destination1After,) = destinationPair.getReserves();
            require(
                source0After == source0Before && source1After == source1Before,
                "SOURCE_PAIR_PARTIAL_STATE"
            );
            require(
                destination0After == destination0Before && destination1After == destination1Before,
                "DESTINATION_PAIR_PARTIAL_STATE"
            );
        }

        require(block.number == forkBlock, "FORK_BLOCK_CHANGED");
    }

    function _findBestRoute(
        uint112 sourceUsdc,
        uint112 sourceWeth,
        uint112 destinationUsdc,
        uint112 destinationWeth
    ) private pure returns (address borrowedToken, uint256 bestAmount, uint256 bestProfit) {
        borrowedToken = WETH;
        bestAmount = uint256(sourceWeth) / 10_000;
        for (uint256 i = 1; i <= 1_000; i++) {
            uint256 amount = (uint256(sourceWeth) * i) / 10_000;
            if (amount == 0 || amount >= sourceWeth) continue;
            uint256 proceeds = _getAmountOut(amount, destinationWeth, destinationUsdc);
            uint256 repayment = _getAmountIn(amount, sourceUsdc, sourceWeth);
            if (proceeds > repayment && proceeds - repayment > bestProfit) {
                bestAmount = amount;
                bestProfit = proceeds - repayment;
            }
        }

        if (bestProfit == 0) {
            borrowedToken = USDC;
            bestAmount = uint256(sourceUsdc) / 10_000;
            for (uint256 i = 1; i <= 1_000; i++) {
                uint256 amount = (uint256(sourceUsdc) * i) / 10_000;
                if (amount == 0 || amount >= sourceUsdc) continue;
                uint256 proceeds = _getAmountOut(amount, destinationUsdc, destinationWeth);
                uint256 repayment = _getAmountIn(amount, sourceWeth, sourceUsdc);
                if (proceeds > repayment && proceeds - repayment > bestProfit) {
                    bestAmount = amount;
                    bestProfit = proceeds - repayment;
                }
            }
        }
    }

    function _getAmountOut(
        uint256 amountIn,
        uint256 reserveIn,
        uint256 reserveOut
    ) private pure returns (uint256) {
        uint256 amountInWithFee = amountIn * (BPS - FEE_BPS);
        return (amountInWithFee * reserveOut) / (reserveIn * BPS + amountInWithFee);
    }

    function _getAmountIn(
        uint256 amountOut,
        uint256 reserveIn,
        uint256 reserveOut
    ) private pure returns (uint256) {
        uint256 numerator = reserveIn * amountOut * BPS;
        uint256 denominator = (reserveOut - amountOut) * (BPS - FEE_BPS);
        return numerator / denominator + 1;
    }
}
