"""Alternative GD: Nesterov accelerated gradient."""

def quadratic_bowl(x, y):
    return 100 * x ** 2 + y ** 2

def gradient_bowl(x, y):
    return (200 * x, 2 * y)

def vanilla_gd(x, y, learning_rate=0.005, tolerance=1e-6, max_iters=10000):
    for i in range(max_iters):
        fx = quadratic_bowl(x, y)
        if fx < tolerance: return i, fx
        gx, gy = gradient_bowl(x, y)
        x -= learning_rate * gx
        y -= learning_rate * gy
    return max_iters, quadratic_bowl(x, y)

def momentum_gd(x, y, learning_rate=0.005, momentum=0.9, tolerance=1e-6, max_iters=10000):
    vx, vy = 0.0, 0.0
    for i in range(max_iters):
        fx = quadratic_bowl(x, y)
        if fx < tolerance: return i, fx
        # Nesterov lookahead
        lx = x - momentum * vx
        ly = y - momentum * vy
        gx, gy = gradient_bowl(lx, ly)
        vx = momentum * vx + learning_rate * gx
        vy = momentum * vy + learning_rate * gy
        x -= vx
        y -= vy
    return max_iters, quadratic_bowl(x, y)
