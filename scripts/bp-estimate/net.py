"""Numpy forward pass of the Schrumpf et al. 2021 blood pressure LSTM (lstm_ppg_nonmixed.h5)."""
import h5py, numpy as np
P = 'model_weights/'
def load(path):
    w = h5py.File(path)
    g = lambda n: np.array(w[P + n], dtype=np.float64)
    def lstm(layer, i, j):
        out = {}
        for d, cell in (('forward', i), ('backward', j)):
            base = f'{layer}/{layer}/{d}_lstm{layer[13:]}/lstm_cell_{cell}/'
            out[d] = (g(base + 'kernel:0'), g(base + 'recurrent_kernel:0'), g(base + 'bias:0'))
        return out
    return {
        'conv': (g('conv1d_48/conv1d_48/kernel:0'), g('conv1d_48/conv1d_48/bias:0')),
        'l0': lstm('bidirectional', 1, 2), 'l1': lstm('bidirectional_1', 4, 5), 'l2': lstm('bidirectional_2', 7, 8),
        'dense': (g('dense_5/dense_5/kernel:0'), g('dense_5/dense_5/bias:0')),
        'sbp': (g('SBP/SBP/kernel:0'), g('SBP/SBP/bias:0')), 'dbp': (g('DBP/DBP/kernel:0'), g('DBP/DBP/bias:0')),
    }
sig = lambda x: 1 / (1 + np.exp(-x))
def run_lstm(x, k, r, b, seqs):
    n, t, _ = x.shape; u = r.shape[0]
    h = np.zeros((n, u)); c = np.zeros((n, u)); xs = x @ k + b; out = np.empty((n, t, u)) if seqs else None
    for s in range(t):
        z = xs[:, s] + h @ r
        i, f, g, o = sig(z[:, :u]), sig(z[:, u:2*u]), np.tanh(z[:, 2*u:3*u]), sig(z[:, 3*u:])
        c = f * c + i * g; h = o * np.tanh(c)
        if seqs: out[:, s] = h
    return out if seqs else h
def bi(x, layer, seqs):
    f = run_lstm(x, *layer['forward'], seqs)
    bk = run_lstm(x[:, ::-1], *layer['backward'], seqs)
    if seqs: bk = bk[:, ::-1]
    return np.concatenate([f, bk], -1)
def features(m, x):
    """x: (n, 875) -> (n, 64) last BiLSTM state."""
    k, b = m['conv']; x = x[..., None]; t = x.shape[1]
    pad = np.concatenate([np.zeros((x.shape[0], 4, 1)), x], 1)
    y = sum(pad[:, j:j + t] @ k[j] for j in range(5)) + b
    y = np.maximum(y, 0)
    y = bi(y, m['l0'], True); y = bi(y, m['l1'], True); return bi(y, m['l2'], False)
def heads(m, h):
    d = h @ m['dense'][0] + m['dense'][1]
    return np.c_[d @ m['sbp'][0] + m['sbp'][1], d @ m['dbp'][0] + m['dbp'][1]]
