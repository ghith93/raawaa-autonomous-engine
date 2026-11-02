// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

interface IERC20FlashTest {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
}

interface IV2FactoryFlashTest {
    function getPair(address tokenA, address tokenB) external view returns (address);
}

interface IV2PairFlashTest {
    function factory() external view returns (address);
    function token0() external view returns (address);
    function token1() external view returns (address);
    function getReserves() external view returns (uint112, uint112, uint32);
    function swap(uint256 amount0Out, uint256 amount1Out, address to, bytes calldata data) external;
}

interface IUniswapV2CalleeFlashTest {
    function uniswapV2Call(address sender, uint256 amount0, uint256 amount1, bytes calldata data) external;
}

/// @dev Test-only callback harness. It is not part of the Python package and has no deployment workflow.
contract FlashSwapHarness is IUniswapV2CalleeFlashTest {
    uint256 private constant BPS = 10_000;
    uint256 private constant FEE_BPS = 30;

    struct CallbackData {
        address destinationPair;
        address borrowedToken;
        uint256 amountBorrowed;
        uint256 deadline;
    }

    struct PoolData {
        uint256 reserveBorrowed;
        uint256 reserveRepayment;
        uint256 destinationReserveIn;
        uint256 destinationReserveOut;
        bool repaymentIsToken0;
        bool borrowedIsToken0;
    }

    address public immutable owner;
    address public immutable sourceFactory;
    address public immutable destinationFactory;

    bool private active;
    address private activeSourcePair;
    address private activeDestinationPair;
    address private activeBorrowedToken;
    address private activeRepaymentToken;
    uint256 private activeBorrowAmount;
    uint256 private activeDeadline;
    uint256 public lastProfit;

    constructor(address sourceFactory_, address destinationFactory_) {
        require(sourceFactory_ != address(0) && destinationFactory_ != address(0), "ZERO_FACTORY");
        owner = msg.sender;
        sourceFactory = sourceFactory_;
        destinationFactory = destinationFactory_;
    }

    function execute(
        address sourcePairAddress,
        address destinationPairAddress,
        address borrowedToken,
        uint256 amountBorrowed,
        uint256 minProfit,
        uint256 deadline
    ) external returns (uint256 profit) {
        require(msg.sender == owner, "ONLY_OWNER");
        require(!active, "ALREADY_ACTIVE");
        require(block.timestamp <= deadline, "EXPIRED");
        require(amountBorrowed > 0, "ZERO_BORROW");
        require(sourcePairAddress != destinationPairAddress, "SAME_PAIR");

        IV2PairFlashTest sourcePair = IV2PairFlashTest(sourcePairAddress);
        IV2PairFlashTest destinationPair = IV2PairFlashTest(destinationPairAddress);
        _validatePair(sourcePair, sourceFactory);
        _validatePair(destinationPair, destinationFactory);

        address token0 = sourcePair.token0();
        address token1 = sourcePair.token1();
        require(
            destinationPair.token0() == token0 && destinationPair.token1() == token1,
            "TOKEN_MISMATCH"
        );
        require(borrowedToken == token0 || borrowedToken == token1, "BORROW_TOKEN_MISMATCH");

        address repaymentToken = borrowedToken == token0 ? token1 : token0;
        uint256 balanceBefore = IERC20FlashTest(repaymentToken).balanceOf(address(this));

        active = true;
        activeSourcePair = sourcePairAddress;
        activeDestinationPair = destinationPairAddress;
        activeBorrowedToken = borrowedToken;
        activeRepaymentToken = repaymentToken;
        activeBorrowAmount = amountBorrowed;
        activeDeadline = deadline;

        uint256 amount0Out = borrowedToken == token0 ? amountBorrowed : 0;
        uint256 amount1Out = borrowedToken == token1 ? amountBorrowed : 0;
        CallbackData memory callbackData = CallbackData({
            destinationPair: destinationPairAddress,
            borrowedToken: borrowedToken,
            amountBorrowed: amountBorrowed,
            deadline: deadline
        });
        sourcePair.swap(amount0Out, amount1Out, address(this), abi.encode(callbackData));

        active = false;
        activeSourcePair = address(0);
        activeDestinationPair = address(0);
        activeBorrowedToken = address(0);
        activeRepaymentToken = address(0);
        activeBorrowAmount = 0;
        activeDeadline = 0;

        uint256 balanceAfter = IERC20FlashTest(repaymentToken).balanceOf(address(this));
        require(balanceAfter >= balanceBefore, "NEGATIVE_PROFIT");
        profit = balanceAfter - balanceBefore;
        require(profit >= minProfit, "MIN_PROFIT");
        lastProfit = profit;
        if (profit > 0) _safeTransfer(repaymentToken, owner, profit);
    }

    function uniswapV2Call(
        address sender,
        uint256 amount0,
        uint256 amount1,
        bytes calldata data
    ) external override {
        require(active && msg.sender == activeSourcePair, "UNAUTHORIZED_CALLBACK");
        require(sender == address(this), "UNEXPECTED_SENDER");
        require(block.timestamp <= activeDeadline, "CALLBACK_EXPIRED");

        CallbackData memory callbackData = abi.decode(data, (CallbackData));
        require(
            callbackData.destinationPair == activeDestinationPair
                && callbackData.borrowedToken == activeBorrowedToken
                && callbackData.amountBorrowed == activeBorrowAmount
                && callbackData.deadline == activeDeadline,
            "CALLBACK_DATA_MISMATCH"
        );

        IV2PairFlashTest sourcePair = IV2PairFlashTest(activeSourcePair);
        IV2PairFlashTest destinationPair = IV2PairFlashTest(activeDestinationPair);
        _validatePair(sourcePair, sourceFactory);
        _validatePair(destinationPair, destinationFactory);

        PoolData memory pool = _loadPoolData(
            sourcePair, destinationPair, callbackData.borrowedToken
        );
        require(
            (pool.borrowedIsToken0 && amount0 == callbackData.amountBorrowed && amount1 == 0)
                || (!pool.borrowedIsToken0 && amount1 == callbackData.amountBorrowed && amount0 == 0),
            "FLASH_AMOUNT_MISMATCH"
        );
        require(
            callbackData.amountBorrowed < pool.reserveBorrowed,
            "INSUFFICIENT_SOURCE_RESERVE"
        );

        uint256 amountOut = _getAmountOut(
            callbackData.amountBorrowed,
            pool.destinationReserveIn,
            pool.destinationReserveOut
        );
        require(
            amountOut > 0 && amountOut < pool.destinationReserveOut,
            "INSUFFICIENT_DESTINATION_LIQUIDITY"
        );

        _safeTransfer(callbackData.borrowedToken, activeDestinationPair, callbackData.amountBorrowed);
        destinationPair.swap(
            pool.repaymentIsToken0 ? amountOut : 0,
            pool.repaymentIsToken0 ? 0 : amountOut,
            address(this),
            bytes("")
        );

        uint256 repayment = _getAmountIn(
            callbackData.amountBorrowed,
            pool.reserveRepayment,
            pool.reserveBorrowed
        );
        _safeTransfer(activeRepaymentToken, activeSourcePair, repayment);
    }

    function _loadPoolData(
        IV2PairFlashTest sourcePair,
        IV2PairFlashTest destinationPair,
        address borrowedToken
    ) private view returns (PoolData memory pool) {
        address token0 = sourcePair.token0();
        address token1 = sourcePair.token1();
        require(
            destinationPair.token0() == token0 && destinationPair.token1() == token1,
            "CALLBACK_TOKEN_MISMATCH"
        );
        (uint112 source0, uint112 source1,) = sourcePair.getReserves();
        (uint112 destination0, uint112 destination1,) = destinationPair.getReserves();

        if (borrowedToken == token0) {
            pool.reserveBorrowed = source0;
            pool.reserveRepayment = source1;
            pool.destinationReserveIn = destination0;
            pool.destinationReserveOut = destination1;
            pool.repaymentIsToken0 = false;
            pool.borrowedIsToken0 = true;
        } else {
            require(borrowedToken == token1, "BORROW_TOKEN_MISMATCH");
            pool.reserveBorrowed = source1;
            pool.reserveRepayment = source0;
            pool.destinationReserveIn = destination1;
            pool.destinationReserveOut = destination0;
            pool.repaymentIsToken0 = true;
            pool.borrowedIsToken0 = false;
        }
    }

    function _validatePair(IV2PairFlashTest pair, address expectedFactory) private view {
        address token0 = pair.token0();
        address token1 = pair.token1();
        require(token0 != address(0) && token1 != address(0) && token0 < token1, "INVALID_PAIR_TOKENS");
        require(pair.factory() == expectedFactory, "FACTORY_MISMATCH");
        require(
            IV2FactoryFlashTest(expectedFactory).getPair(token0, token1) == address(pair),
            "PAIR_NOT_REGISTERED"
        );
    }

    function _getAmountOut(
        uint256 amountIn,
        uint256 reserveIn,
        uint256 reserveOut
    ) private pure returns (uint256) {
        require(amountIn > 0 && reserveIn > 0 && reserveOut > 0, "INSUFFICIENT_LIQUIDITY");
        uint256 amountInWithFee = amountIn * (BPS - FEE_BPS);
        return (amountInWithFee * reserveOut) / (reserveIn * BPS + amountInWithFee);
    }

    function _getAmountIn(
        uint256 amountOut,
        uint256 reserveIn,
        uint256 reserveOut
    ) private pure returns (uint256) {
        require(
            amountOut > 0 && reserveIn > 0 && reserveOut > amountOut,
            "INSUFFICIENT_REPAYMENT_LIQUIDITY"
        );
        uint256 numerator = reserveIn * amountOut * BPS;
        uint256 denominator = (reserveOut - amountOut) * (BPS - FEE_BPS);
        return numerator / denominator + 1;
    }

    function _safeTransfer(address token, address to, uint256 amount) private {
        (bool success, bytes memory result) = token.call(
            abi.encodeWithSelector(IERC20FlashTest.transfer.selector, to, amount)
        );
        require(success && (result.length == 0 || abi.decode(result, (bool))), "TOKEN_TRANSFER_FAILED");
    }
}
