// @forge-family linreg
/**
 * Crash fixture: the self-test triggers a genuine runtime crash with no
 * 'self-test:' marker in stderr.  The ruler must classify this as CRASH,
 * never as DETECTED.
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

// Self-test path: on clean data the condition is false, so we reach the
// explicit process.exit(0).  On a zero/double mutant the weight is far from 2,
// and we deliberately crash by reading a property of undefined.  The
// resulting TypeError has no 'self-test:' marker, so the ruler must classify
// this as CRASH.
if (Math.abs(result.weights[0] - 2) >= 0.05) {
    const bad = undefined;
    const crash = bad.weights;
}
process.exit(0);
