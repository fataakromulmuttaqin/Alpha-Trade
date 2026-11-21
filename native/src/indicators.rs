//! Indikator dengan semantik persis pandas (pandas ewm adjust=False dsb).
//! Semua beroperasi pada slice f64; NaN mengikuti perilaku pandas.

pub fn ewm_alpha(x: &[f64], alpha: f64, min_periods: usize) -> Vec<f64> {
    let mut out = vec![f64::NAN; x.len()];
    let mut prev = f64::NAN;
    let mut seen: usize = 0;
    for (i, &v) in x.iter().enumerate() {
        if v.is_nan() {
            out[i] = prev; // pandas: NaN dilewati, mean dibawa maju
            continue;
        }
        seen += 1;
        prev = if seen == 1 { v } else { (1.0 - alpha) * prev + alpha * v };
        out[i] = if seen >= min_periods { prev } else { f64::NAN };
    }
    out
}

pub fn ema(x: &[f64], n: usize) -> Vec<f64> {
    ewm_alpha(x, 2.0 / (n as f64 + 1.0), 1)
}

/// pandas ewm default (adjust=True, ignore_na=False): bobot (1-a)^(t-i) pada
/// posisi absolut — posisi NaN tetap meredam bobot. min_periods = jumlah
/// observasi valid minimum.
pub fn ewm_adjust(x: &[f64], alpha: f64, min_periods: usize) -> Vec<f64> {
    let mut out = vec![f64::NAN; x.len()];
    let mut num = 0.0f64;
    let mut den = 0.0f64;
    let mut seen = 0usize;
    for (i, &v) in x.iter().enumerate() {
        if v.is_nan() {
            num *= 1.0 - alpha;
            den *= 1.0 - alpha;
        } else {
            seen += 1;
            num = num * (1.0 - alpha) + v;
            den = den * (1.0 - alpha) + 1.0;
        }
        out[i] = if seen >= min_periods && den > 0.0 { num / den } else { f64::NAN };
    }
    out
}

pub fn rsi(close: &[f64], n: usize) -> Vec<f64> {
    let alpha = 1.0 / n as f64;
    let mut up = vec![f64::NAN; close.len()];
    let mut dn = vec![f64::NAN; close.len()];
    let mut pu = f64::NAN;
    let mut pd = f64::NAN;
    for i in 1..close.len() {
        let d = close[i] - close[i - 1];
        let u = if d > 0.0 { d } else { 0.0 };
        let w = if d < 0.0 { -d } else { 0.0 };
        pu = if pu.is_nan() { u } else { (1.0 - alpha) * pu + alpha * u };
        pd = if pd.is_nan() { w } else { (1.0 - alpha) * pd + alpha * w };
        up[i] = pu;
        dn[i] = pd;
    }
    up.iter().zip(dn.iter())
        .map(|(&u, &d)| {
            if d == 0.0 || d.is_nan() || u.is_nan() { f64::NAN } else { 100.0 - 100.0 / (1.0 + u / d) }
        })
        .collect()
}

pub fn macd(close: &[f64], f: usize, s: usize, sig: usize) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
    let ef = ema(close, f);
    let es = ema(close, s);
    let line: Vec<f64> = ef.iter().zip(es.iter()).map(|(a, b)| a - b).collect();
    let signal = ema(&line, sig);
    let hist: Vec<f64> = line.iter().zip(signal.iter()).map(|(a, b)| a - b).collect();
    (line, signal, hist)
}

pub fn true_range(high: &[f64], low: &[f64], close: &[f64]) -> Vec<f64> {
    let n = high.len();
    let mut tr = vec![0.0; n];
    for i in 0..n {
        let hl = high[i] - low[i];
        if i == 0 || close[i - 1].is_nan() {
            tr[i] = hl;
        } else {
            tr[i] = hl.max((high[i] - close[i - 1]).abs()).max((low[i] - close[i - 1]).abs());
        }
    }
    tr
}

pub fn atr(high: &[f64], low: &[f64], close: &[f64], n: usize) -> Vec<f64> {
    let tr = true_range(high, low, close);
    ewm_alpha(&tr, 1.0 / n as f64, 1)
}

/// ADX Wilder — rantai ewm pakai pandas DEFAULT (adjust=True, min_periods=n)
/// sesuai backend.confluence_engine.adx_wilder.
pub fn adx_wilder(high: &[f64], low: &[f64], close: &[f64], n: usize) -> Vec<f64> {
    let len = high.len();
    let mut plus_dm = vec![0.0; len];
    let mut minus_dm = vec![0.0; len];
    for i in 1..len {
        let up = high[i] - high[i - 1];
        let dn = low[i - 1] - low[i];
        if up > dn && up > 0.0 { plus_dm[i] = up; }
        if dn > up && dn > 0.0 { minus_dm[i] = dn; }
    }
    let tr = true_range(high, low, close);
    let atr_w = ewm_adjust(&tr, 1.0 / n as f64, n);
    let pdi = ewm_adjust(&plus_dm, 1.0 / n as f64, n)
        .iter().zip(atr_w.iter()).map(|(&p, &a)| if a == 0.0 || a.is_nan() { f64::NAN } else { 100.0 * p / a }).collect::<Vec<_>>();
    let mdi = ewm_adjust(&minus_dm, 1.0 / n as f64, n)
        .iter().zip(atr_w.iter()).map(|(&m, &a)| if a == 0.0 || a.is_nan() { f64::NAN } else { 100.0 * m / a }).collect::<Vec<_>>();
    let dx: Vec<f64> = pdi.iter().zip(mdi.iter())
        .map(|(&p, &m)| {
            if p.is_nan() || m.is_nan() || (p + m) == 0.0 { f64::NAN }
            else { 100.0 * (p - m).abs() / (p + m) }
        }).collect();
    ewm_adjust(&dx, 1.0 / n as f64, n)
}

/// POC/VAL/VAH rolling — window SELALU bar sebelumnya (tanpa lookahead),
/// replikasi np.histogram + value-area expansion (tie: `above >= below`).
pub fn volume_profile_levels(
    close: &[f64], volume: &[f64], lookback: usize, bins: usize, value_area: f64,
) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
    let n = close.len();
    let mut poc = vec![f64::NAN; n];
    let mut val = vec![f64::NAN; n];
    let mut vah = vec![f64::NAN; n];
    if bins == 0 { return (poc, val, vah); }
    for i in lookback..n {
        let (wc, wv) = (&close[i - lookback..i], &volume[i - lookback..i]);
        let mut lo_w = f64::INFINITY;
        let mut hi_w = f64::NEG_INFINITY;
        for (&cc, _) in wc.iter().zip(wv.iter()) {
            if cc < lo_w { lo_w = cc; }
            if cc > hi_w { hi_w = cc; }
        }
        if !lo_w.is_finite() || hi_w <= lo_w { continue; }
        // replikasi np.histogram: edges = linspace(min, max, bins+1), nilai
        // ditugaskan via binary search (bin kanan utk nilai tepat pada edge)
        let delta = (hi_w - lo_w) / bins as f64;
        let mut edges: Vec<f64> = (0..=bins).map(|k| lo_w + k as f64 * delta).collect();
        edges[bins] = hi_w; // np.linspace: titik akhir persis stop
        let mut hist = vec![0.0f64; bins];
        for (&cc, &vv) in wc.iter().zip(wv.iter()) {
            // upper_bound: indeks pertama edge > cc; bin = itu - 1
            let mut a_ = 0usize;
            let mut z = edges.len();
            while a_ < z {
                let mid = (a_ + z) / 2;
                if edges[mid] <= cc { a_ = mid + 1; } else { z = mid; }
            }
            let mut b = a_.saturating_sub(1);
            if b >= bins { b = bins - 1; } // x == edge terakhir
            hist[b] += vv;
        }
        // target = hist.sum() * value_area — jumlah PER-BIN (urutan float
        // numpy), bukan jumlah nilai mentah; beda urutan menggeser walk
        let vsum_tot: f64 = hist.iter().sum();
        if vsum_tot == 0.0 { continue; }
        let mut p = 0usize;
        for k in 1..bins { if hist[k] > hist[p] { p = k; } } // argmax: max pertama
        let width = (hi_w - lo_w) / bins as f64;
        let center = |k: usize| lo_w + width * (k as f64 + 0.5);
        let (mut lo, mut hi) = (p, p);
        let mut acc = hist[p];
        let target = vsum_tot * value_area;
        while acc < target && (lo > 0 || hi < bins - 1) {
            let below = if lo > 0 { hist[lo - 1] } else { -1.0 };
            let above = if hi < bins - 1 { hist[hi + 1] } else { -1.0 };
            if above >= below { hi += 1; acc += hist[hi]; } else { lo -= 1; acc += hist[lo]; }
        }
        poc[i] = center(p);
        val[i] = center(lo);
        vah[i] = center(hi);
    }
    (poc, val, vah)
}

/// Regular divergence berbasis pivot (konfirmasi L bar setelah pivot).
pub fn divergence(
    high: &[f64], low: &[f64], rsi_s: &[f64], l: usize,
) -> (Vec<bool>, Vec<bool>) {
    let n = high.len();
    let mut bull = vec![false; n];
    let mut bear = vec![false; n];
    let mut last_lo: Option<usize> = None;
    let mut last_hi: Option<usize> = None;
    if n < 2 * l + 1 { return (bull, bear); }
    for p in l..n - l {
        let conf = p + l;
        // pivot low: low[p] == min(low[p-L .. p+L])
        let lo_win_min = (p - l..=p + l).map(|k| low[k]).fold(f64::INFINITY, f64::min);
        if low[p] == lo_win_min {
            if let Some(ll) = last_lo {
                if low[p] < low[ll] && rsi_s[p] > rsi_s[ll] { bull[conf] = true; }
            }
            last_lo = Some(p);
        }
        let hi_win_max = (p - l..=p + l).map(|k| high[k]).fold(f64::NEG_INFINITY, f64::max);
        if high[p] == hi_win_max {
            if let Some(lh) = last_hi {
                if high[p] > high[lh] && rsi_s[p] < rsi_s[lh] { bear[conf] = true; }
            }
            last_hi = Some(p);
        }
    }
    (bull, bear)
}

/// Bull/bear engulfing, hammer, shooting star, liquidity sweep.
pub struct PriceAction {
    pub bull: Vec<bool>, pub bear: Vec<bool>,
    pub sweep_bull: Vec<bool>, pub sweep_bear: Vec<bool>,
}
pub fn price_action(
    open: &[f64], high: &[f64], low: &[f64], close: &[f64], sweep_lookback: usize,
) -> PriceAction {
    let n = open.len();
    let mut bull = vec![false; n];
    let mut bear = vec![false; n];
    let mut sweep_bull = vec![false; n];
    let mut sweep_bear = vec![false; n];
    for i in 1..n {
        let body = (close[i] - open[i]).abs();
        let rng = high[i] - low[i];
        let up_w = high[i] - open[i].max(close[i]);
        let lo_w = open[i].min(close[i]) - low[i];

        let be = close[i] > open[i]
            && close[i - 1] < open[i - 1]
            && close[i] >= open[i - 1]
            && open[i] <= close[i - 1];
        let se = close[i] < open[i]
            && close[i - 1] > open[i - 1]
            && close[i] <= open[i - 1]
            && open[i] >= close[i - 1];
        let hammer = rng > 0.0
            && lo_w >= 2.0 * body && up_w <= body && (lo_w / rng) > 0.55;
        let shooting = rng > 0.0
            && up_w >= 2.0 * body && lo_w <= body && (up_w / rng) > 0.55;

        // sweep: window lookback bar SEBELUMnya (shift lalu rolling min/max)
        if i >= sweep_lookback {
            let prev_low = low[i - sweep_lookback..i].iter().cloned()
                .fold(f64::INFINITY, f64::min);
            let prev_high = high[i - sweep_lookback..i].iter().cloned()
                .fold(f64::NEG_INFINITY, f64::max);
            let sb = low[i] < prev_low && close[i] > prev_low;
            let ss = high[i] > prev_high && close[i] < prev_high;
            sweep_bull[i] = sb;
            sweep_bear[i] = ss;
            bull[i] = be || hammer || sb;
            bear[i] = se || shooting || ss;
        } else {
            bull[i] = be || hammer;
            bear[i] = se || shooting;
        }
    }
    PriceAction { bull, bear, sweep_bull, sweep_bear }
}

/// Rolling rank percentile (pandas rolling.rank(pct=True)); window penuh,
/// NaN sebelum window terisi (min_periods=100 di Python untuk ranking).
pub fn rolling_rank_pct(x: &[f64], window: usize, min_periods: usize) -> Vec<f64> {
    let n = x.len();
    let mut out = vec![f64::NAN; n];
    for i in 0..n {
        let st = if i + 1 >= window { i + 1 - window } else { 0 };
        let win = &x[st..=i];
        let valid: Vec<f64> = win.iter().cloned().filter(|v| !v.is_nan()).collect();
        if valid.len() < min_periods.min(window) { continue; }
        if x[i].is_nan() { continue; }
        let below = valid.iter().filter(|&&v| v <= x[i]).count();
        out[i] = below as f64 / valid.len() as f64;
    }
    out
}
