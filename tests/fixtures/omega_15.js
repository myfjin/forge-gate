// @forge-family linreg
/**
 * Numeric-constant fixture A: omega = 1.5.
 * Differs from omega_19.js only in the numeric constant, so a whitespace-only
 * duplicate-body detector must keep them distinct.
 */
function omegaSolver(X, y, lr, iters) {
    const omega = 1.5;
    let value = 0.0;
    for (let i = 0; i < iters; i++) {
        value += (X[0][0] + y[0]) * omega;
    }
    return { value: value, omega: omega };
}

const linearRegressionGD = omegaSolver;

console.log(JSON.stringify(linearRegressionGD([[1]], [2], 0.1, 5)));
