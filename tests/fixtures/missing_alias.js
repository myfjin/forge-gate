// @forge-family linreg
/**
 * Fixture with renamed entry but NO harness alias.  The ruler must reject this
 * with the missing-alias message, not fall back to the old hardcoded name.
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

const X = [[1], [2], [3], [4], [5]];
const y = [3, 5, 7, 9, 11];
const result = univariateBatchGD(X, y, 0.05, 500);
console.log(JSON.stringify(result));

const assert = require('assert');
assert.strictEqual(result.weights.length, 1, 'weights length');
assert(Math.abs(result.weights[0] - 2) < 0.05, 'weight close to 2');
assert(Math.abs(result.bias - 1) < 0.05, 'bias close to 1');
