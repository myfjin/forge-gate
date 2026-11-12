// @forge-family linreg
/**
 * Weak renamed fixture: entry is named honestly, aliased to the harness
 * contract, but the self-test only asserts the result exists.  This survives
 * both zero and double mutants, so gate 3 must reject it.
 */
function univariateBatchGD(X, y, lr, iters) {
    const n = X.length;
    const nf = X[0].length;
    const weights = new Array(nf).fill(0.0);
    let bias = 0.0;

    for (let i = 0; i < iters; i++) {
        const dw = new Array(nf).fill(0.0);
        let db = 0.0;
        for (let j = 0; j < n; j++) {
            let pred = bias;
            for (let k = 0; k < nf; k++) {
                pred += weights[k] * X[j][k];
            }
            const err = pred - y[j];
            db += err;
            for (let k = 0; k < nf; k++) {
                dw[k] += err * X[j][k];
            }
        }
        db /= n;
        for (let k = 0; k < nf; k++) {
            dw[k] /= n;
        }
        for (let k = 0; k < nf; k++) {
            weights[k] -= lr * dw[k];
        }
        bias -= lr * db;
    }

    return { weights: weights, bias: bias };
}

const linearRegressionGD = univariateBatchGD;

const X = [[1], [2], [3], [4], [5]];
const y = [3, 5, 7, 9, 11];
const result = linearRegressionGD(X, y, 0.05, 500);
console.log(JSON.stringify(result));

const assert = require('assert');
assert(result !== undefined, 'result is defined');
