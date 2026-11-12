"""Self-contained BROKEN implementation of vanilla and momentum gradient descent.

The bug: momentum velocity is subtracted instead of accumulated
(vx = -momentum * vx + lr * gx), killing momentum and making it slower than vanilla.
Verify with Forge Gate to see FAIL.

    forge-gate verify examples/gradient_descent_momentum_bad.py --pattern gradient_descent_momentum
"""


def quadratic_bowl(x: float, y: float) -> float:
    return 100 * x ** 2 + y ** 2


def gradient_bowl(x: float, y: float) -> tuple[float, float]:
    return (200 * x, 2 * y)


def vanilla_gd(
    x: float,
    y: float,
    learning_rate: float = 0.005,
    tolerance: float = 1e-6,
    max_iters: int = 10000,
) -> tuple[int, float]:
    """Correct vanilla gradient descent."""
    for i in range(max_iters):
        fx = quadratic_bowl(x, y)
        if fx < tolerance:
            return i, fx
        gx, gy = gradient_bowl(x, y)
        x -= learning_rate * gx
        y -= learning_rate * gy
    return max_iters, quadratic_bowl(x, y)


def momentum_gd(
    x: float,
    y: float,
    learning_rate: float = 0.005,
    momentum: float = 0.9,
    tolerance: float = 1e-6,
    max_iters: int = 10000,
) -> tuple[int, float]:
    """BUG: velocity sign flipped, so momentum cancels instead of accumulates."""
    vx, vy = 0.0, 0.0
    for i in range(max_iters):
        fx = quadratic_bowl(x, y)
        if fx < tolerance:
            return i, fx
        gx, gy = gradient_bowl(x, y)
        # BUG: minus sign in front of momentum makes velocity oscillate/cancel.
        vx = -momentum * vx + learning_rate * gx
        vy = -momentum * vy + learning_rate * gy
        x -= vx
        y -= vy
    return max_iters, quadratic_bowl(x, y)


if __name__ == "__main__":
    x0, y0 = 10.0, 10.0

    iters_vanilla, fx_vanilla = vanilla_gd(x0, y0)
    iters_momentum, fx_momentum = momentum_gd(x0, y0)

    print(
        f"gradient_descent_momentum.py BAD: vanilla_iters={iters_vanilla} "
        f"momentum_iters={iters_momentum} fx_momentum={fx_momentum}"
    )
