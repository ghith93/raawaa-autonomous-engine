// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

interface IERC20MockV2 {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
}

interface IUniswapV2CalleeMock {
    function uniswapV2Call(address sender, uint256 amount0, uint256 amount1, bytes calldata data) external;
}

contract MockERC20 {
    string public name;
    mapping(address => uint256) public balanceOf;

    constructor(string memory name_) {
        name = name_;
    }

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        require(balanceOf[msg.sender] >= amount, "BALANCE");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}

contract MockV2Factory {
    mapping(address => mapping(address => address)) private pairs;

    function createPair(address tokenA, address tokenB) external returns (address pair) {
        require(tokenA != tokenB, "IDENTICAL");
        (address token0, address token1) = tokenA < tokenB
            ? (tokenA, tokenB)
            : (tokenB, tokenA);
        require(token0 != address(0) && pairs[token0][token1] == address(0), "EXISTS");
        pair = address(new MockV2Pair(address(this), token0, token1));
        pairs[token0][token1] = pair;
        pairs[token1][token0] = pair;
    }

    function getPair(address tokenA, address tokenB) external view returns (address) {
        return pairs[tokenA][tokenB];
    }
}

contract MockV2Pair {
    address public immutable factory;
    address public immutable token0;
    address public immutable token1;
    uint112 private reserve0;
    uint112 private reserve1;
    uint32 private blockTimestampLast;
    bool private unlocked = true;

    constructor(address factory_, address token0_, address token1_) {
        factory = factory_;
        token0 = token0_;
        token1 = token1_;
    }

    modifier lock() {
        require(unlocked, "LOCKED");
        unlocked = false;
        _;
        unlocked = true;
    }

    function getReserves() external view returns (uint112, uint112, uint32) {
        return (reserve0, reserve1, blockTimestampLast);
    }

    function sync() external {
        _update(
            IERC20MockV2(token0).balanceOf(address(this)),
            IERC20MockV2(token1).balanceOf(address(this))
        );
    }

    function swap(
        uint256 amount0Out,
        uint256 amount1Out,
        address to,
        bytes calldata data
    ) external lock {
        require(amount0Out > 0 || amount1Out > 0, "ZERO_OUTPUT");
        require(amount0Out < reserve0 && amount1Out < reserve1, "LIQUIDITY");
        require(to != token0 && to != token1, "INVALID_TO");

        if (amount0Out > 0) _safeTransfer(token0, to, amount0Out);
        if (amount1Out > 0) _safeTransfer(token1, to, amount1Out);
        if (data.length > 0) {
            IUniswapV2CalleeMock(to).uniswapV2Call(msg.sender, amount0Out, amount1Out, data);
        }

        uint256 balance0 = IERC20MockV2(token0).balanceOf(address(this));
        uint256 balance1 = IERC20MockV2(token1).balanceOf(address(this));
        uint256 amount0In = balance0 > uint256(reserve0) - amount0Out
            ? balance0 - (uint256(reserve0) - amount0Out)
            : 0;
        uint256 amount1In = balance1 > uint256(reserve1) - amount1Out
            ? balance1 - (uint256(reserve1) - amount1Out)
            : 0;
        require(amount0In > 0 || amount1In > 0, "NO_INPUT");

        uint256 balance0Adjusted = balance0 * 10_000 - amount0In * 30;
        uint256 balance1Adjusted = balance1 * 10_000 - amount1In * 30;
        require(
            balance0Adjusted * balance1Adjusted
                >= uint256(reserve0) * uint256(reserve1) * 10_000 * 10_000,
            "K"
        );
        _update(balance0, balance1);
    }

    function _update(uint256 balance0, uint256 balance1) private {
        require(balance0 <= type(uint112).max && balance1 <= type(uint112).max, "OVERFLOW");
        reserve0 = uint112(balance0);
        reserve1 = uint112(balance1);
        blockTimestampLast = uint32(block.timestamp);
    }

    function _safeTransfer(address token, address to, uint256 amount) private {
        (bool success, bytes memory result) = token.call(
            abi.encodeWithSelector(IERC20MockV2.transfer.selector, to, amount)
        );
        require(success && (result.length == 0 || abi.decode(result, (bool))), "TRANSFER");
    }
}
