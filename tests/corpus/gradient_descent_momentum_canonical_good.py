"""Self-contained correct implementation of vanilla and momentum gradient descent.

This file can be run standalone or verified against the Forge Gate oracle:

    forge-gate verify examples/gradient_descent_momentum_good.py --pattern gradient_descent_momentum
"""


def quadratic_bowl(x: float, y: float) -> float:
    """2D quadratic bowl: f(x,y) = 100*x^2 + y^2."""
    return 100 * x ** 2 + y ** 2


def gradient_bowl(x: float, y: float) -> tuple[float, float]:
    """Gradient of quadratic_bowl."""
    return (200 * x, 2 * y)


def vanilla_gd(
    x: float,
    y: float,
    learning_rate: float = 0.005,
    tolerance: float = 1e-6,
    max_iters: int = 10000,
) -> tuple[int, float]:
    """Vanilla gradient descent on the 2D quadratic bowl."""
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
    """Gradient descent with momentum on the 2D quadratic bowl."""
    vx, vy = 0.0, 0.0
    for i in range(max_iters):
        fx = quadratic_bowl(x, y)
        if fx < tolerance:
            return i, fx
        gx, gy = gradient_bowl(x, y)
        vx = momentum * vx + learning_rate * gx
        vy = momentum * vy + learning_rate * gy
        x -= vx
        y -= vy
    return max_iters, quadratic_bowl(x, y)


if __name__ == "__main__":
    x0, y0 = 10.0, 10.0

    iters_vanilla, fx_vanilla = vanilla_gd(x0, y0)
    iters_momentum, fx_momentum = momentum_gd(x0, y0)

    assert fx_vanilla < 1e-6, f"Vanilla GD did not converge: {fx_vanilla}"
    assert fx_momentum < 1e-6, f"Momentum GD did not converge: {fx_momentum}"
    assert iters_momentum < iters_vanilla, (
        f"Momentum ({iters_momentum}) not faster than vanilla ({iters_vanilla})"
    )

    print(
        f"gradient_descent_momentum.py: vanilla_iters={iters_vanilla} "
        f"momentum_iters={iters_momentum} PASS"
    )
