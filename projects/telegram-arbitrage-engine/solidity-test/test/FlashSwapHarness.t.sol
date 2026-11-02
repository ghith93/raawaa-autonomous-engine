// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import "../src/FlashSwapHarness.sol";
import "./mocks/MockV2.sol";

interface VmFlashSwap {
    function expectRevert() external;
    function prank(address sender) external;
}

contract FlashSwapHarnessTest {
    VmFlashSwap private constant vm =
        VmFlashSwap(address(uint160(uint256(keccak256("hevm cheat code")))));

    MockERC20 private tokenA;
    MockERC20 private tokenB;
    MockV2Factory private sourceFactory;
    MockV2Factory private destinationFactory;
    MockV2Pair private sourcePair;
    MockV2Pair private destinationPair;
    FlashSwapHarness private harness;

    function setUp() public {
        tokenA = new MockERC20("Token A");
        tokenB = new MockERC20("Token B");
        sourceFactory = new MockV2Factory();
        destinationFactory = new MockV2Factory();
        sourcePair = MockV2Pair(sourceFactory.createPair(address(tokenA), address(tokenB)));
        destinationPair = MockV2Pair(
            destinationFactory.createPair(address(tokenA), address(tokenB))
        );
        harness = new FlashSwapHarness(address(sourceFactory), address(destinationFactory));

        _seed(sourcePair, 1_000_000, 1_000_000);
        _seed(destinationPair, 1_050_000, 1_000_000);
    }

    function testProfitableRouteSettlesBothPairsAtomically() public {
        address borrowedToken = sourcePair.token1();
        address repaymentToken = sourcePair.token0();
        uint256 ownerBalanceBefore = IERC20FlashTest(repaymentToken).balanceOf(address(this));

        uint256 profit = harness.execute(
            address(sourcePair),
            address(destinationPair),
            borrowedToken,
            10_000,
            1,
            block.timestamp + 60
        );

        require(profit > 0, "NO_PROFIT");
        require(harness.lastProfit() == profit, "PROFIT_NOT_RECORDED");
        require(
            IERC20FlashTest(repaymentToken).balanceOf(address(this)) == ownerBalanceBefore + profit,
            "PROFIT_NOT_PAID"
        );

        (uint112 source0, uint112 source1,) = sourcePair.getReserves();
        (uint112 destination0, uint112 destination1,) = destinationPair.getReserves();
        require(source0 > 1_000_000, "SOURCE_NOT_REPAID");
        require(source1 == 990_000, "SOURCE_BORROW_NOT_ACCOUNTED");
        require(destination0 < 1_050_000, "DESTINATION_NOT_SWAPPED");
        require(destination1 == 1_010_000, "DESTINATION_INPUT_NOT_ACCOUNTED");
    }

    function testMinimumProfitFailureRollsBackBothPairs() public {
        (uint112 source0Before, uint112 source1Before,) = sourcePair.getReserves();
        (uint112 destination0Before, uint112 destination1Before,) = destinationPair.getReserves();

        vm.expectRevert();
        harness.execute(
            address(sourcePair),
            address(destinationPair),
            sourcePair.token1(),
            10_000,
            type(uint256).max,
            block.timestamp + 60
        );

        (uint112 source0After, uint112 source1After,) = sourcePair.getReserves();
        (uint112 destination0After, uint112 destination1After,) = destinationPair.getReserves();
        require(source0After == source0Before && source1After == source1Before, "SOURCE_PARTIAL_STATE");
        require(
            destination0After == destination0Before && destination1After == destination1Before,
            "DESTINATION_PARTIAL_STATE"
        );
    }

    function testRejectsSpoofedCallback() public {
        vm.expectRevert();
        harness.uniswapV2Call(address(harness), 0, 100, bytes(""));
    }

    function testRejectsUnregisteredSourcePair() public {
        MockV2Pair rogue = new MockV2Pair(
            address(sourceFactory), sourcePair.token0(), sourcePair.token1()
        );
        vm.expectRevert();
        harness.execute(
            address(rogue),
            address(destinationPair),
            sourcePair.token1(),
            10_000,
            0,
            block.timestamp + 60
        );
    }

    function testRejectsMismatchedDestinationTokens() public {
        MockERC20 tokenC = new MockERC20("Token C");
        MockV2Factory otherFactory = new MockV2Factory();
        MockV2Pair wrongPair = MockV2Pair(
            otherFactory.createPair(address(tokenA), address(tokenC))
        );
        _seed(wrongPair, 1_000_000, 1_000_000);

        vm.expectRevert();
        harness.execute(
            address(sourcePair),
            address(wrongPair),
            sourcePair.token1(),
            10_000,
            0,
            block.timestamp + 60
        );
    }

    function testRejectsExpiredDeadlineAndNonOwner() public {
        vm.expectRevert();
        harness.execute(
            address(sourcePair),
            address(destinationPair),
            sourcePair.token1(),
            10_000,
            0,
            block.timestamp - 1
        );

        vm.prank(address(0xBEEF));
        vm.expectRevert();
        harness.execute(
            address(sourcePair),
            address(destinationPair),
            sourcePair.token1(),
            10_000,
            0,
            block.timestamp + 60
        );
    }

    function testRejectsSamePairAndOversizedBorrow() public {
        vm.expectRevert();
        harness.execute(
            address(sourcePair),
            address(sourcePair),
            sourcePair.token1(),
            10_000,
            0,
            block.timestamp + 60
        );

        vm.expectRevert();
        harness.execute(
            address(sourcePair),
            address(destinationPair),
            sourcePair.token1(),
            1_000_000,
            0,
            block.timestamp + 60
        );
    }

    function _seed(MockV2Pair pair, uint256 amount0, uint256 amount1) private {
        MockERC20(pair.token0()).mint(address(pair), amount0);
        MockERC20(pair.token1()).mint(address(pair), amount1);
        pair.sync();
    }
}
