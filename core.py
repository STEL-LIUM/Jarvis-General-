"""
PROMETHEUS Core — OscillatorNetwork v3

Physics-inspired computation: neurons are Kuramoto phase oscillators.
Information lives in phase relationships, not dot products.

Architecture:
  input (clamped) → W_ih (CHL) → hidden (Kuramoto) → W_out (momentum Perceptron) → class

v3 changes:
  - Contrastive Hebbian Learning (CHL) for W_ih: replaces plain phase Hebbian.
    Free phase settles normally; clamped phase re-settles with a teaching current
    (gradient of cross-entropy loss w.r.t. hidden phases) injected into each oscillator.
    Update: ΔW_ih = lr_ih * (clamp_corr − free_corr). Provably closer to backprop
    than the sign-flipped Hebbian used in v2 — should break the 40% accuracy ceiling.
  - Softmax confidence: _confidence() uses softmax probability margin (always in [0,1])
    instead of raw logit margin. Scale-invariant across network sizes.
  - Adaptive inference unchanged (conf_thresh in [0,1] space).
"""

import numpy as np


class OscillatorNetwork:
    def __init__(
        self,
        n_input:        int,
        n_hidden:       int,
        n_output:       int,
        dt:             float = 0.10,
        lr_ih:          float = 0.012,
        lr_out:         float = 0.06,
        momentum:       float = 0.85,
        freq_std:       float = 0.20,
        noise:          float = 0.004,
        decay:          float = 0.0002,
        K_lat:          float = 0.35,
        conf_thresh:    float = 0.60,
        ood_floor:      float = 0.10,
        clamp_strength: float = 0.50,   # teaching current magnitude in clamped phase
        # ── Experiment hooks (defaults reproduce v3 behavior exactly) ──
        phase_init:     str   = "random",  # "random" | "zeros" — hidden init each inference
        lr_ih_mom:      float = 0.0,       # momentum on W_ih CHL updates (0 = off)
        n_harmonics:    int   = 2,         # Fourier harmonics in readout (2 = cos,sin,cos2,sin2)
        rng:            np.random.Generator | None = None,
    ):
        self.n_in  = n_input
        self.n_hid = n_hidden
        self.n_out = n_output
        self.dt    = dt
        self.lr_ih  = lr_ih
        self.lr_out = lr_out
        self.momentum       = momentum
        self.noise          = noise
        self.decay          = decay
        self.K_lat          = K_lat
        self.conf_thresh    = conf_thresh
        self.ood_floor      = ood_floor
        self.clamp_strength = clamp_strength
        self.phase_init     = phase_init
        self.lr_ih_mom      = lr_ih_mom
        self.n_harmonics    = n_harmonics
        self._W_ih_velocity = None   # lazily created if lr_ih_mom > 0
        # Optional proper readout: a convex classifier (ridge/logistic) fit on
        # frozen Fourier features. When set, infer() uses it instead of the
        # local-learning perceptron W_out — the experiment sweep showed this
        # lifts single-agent accuracy from ~88% to ~97%.
        self.readout_clf       = None
        self._readout_samples  = 5
        self._readout_settle   = 50
        # Keep the rng on self so noise / reset use the same per-agent seed.
        # Without this, oscillator noise & hidden-state reset go through the
        # global default RNG, which breaks reproducibility (different agents
        # ended up seeing identical-but-coupled-by-call-order noise streams).
        self.rng = rng or np.random.default_rng()

        self.omega_hid = self.rng.normal(0, freq_std, n_hidden)

        self.W_ih  = self.rng.normal(0, 2.5 / np.sqrt(n_input), (n_input, n_hidden))

        # 2 features (cos, sin) per harmonic per hidden oscillator
        n_feat = 2 * n_harmonics * n_hidden
        self.W_out = self.rng.normal(0, 0.05 / np.sqrt(n_feat), (n_feat, n_output))
        self._W_out_velocity = np.zeros_like(self.W_out)

        self.theta_in  = np.zeros(n_input)
        self.theta_hid = self.rng.uniform(-np.pi, np.pi, n_hidden)

        self.train_losses: list[float] = []

    # ── Encoding ──────────────────────────────────────────────────────────────

    def encode_input(self, x: np.ndarray) -> None:
        self.theta_in = np.pi * (np.asarray(x, dtype=float) - 0.5)

    # ── Kuramoto Dynamics ─────────────────────────────────────────────────────

    def _step_hidden(self) -> None:
        delta_ih = self.theta_in[:, None] - self.theta_hid[None, :]
        d_input  = np.sum(self.W_ih * np.sin(delta_ih), axis=0)
        z_mean   = np.mean(np.exp(1j * self.theta_hid))
        R, Psi   = float(np.abs(z_mean)), float(np.angle(z_mean))
        d_lat    = self.K_lat * R * np.sin(Psi - self.theta_hid)
        dtheta   = self.dt * (self.omega_hid + d_input + d_lat)
        dtheta  += self.noise * self.rng.standard_normal(self.n_hid)
        self.theta_hid = _wrap(self.theta_hid + dtheta)

    def _step_hidden_clamped(self, error: np.ndarray) -> None:
        """Kuramoto step with a teaching current injected into each oscillator.

        error: softmax gradient (probs - one_hot(y)), shape (n_out,).
        The teaching current is the negative gradient of cross-entropy loss
        w.r.t. each hidden phase — it pushes phases toward configurations
        that increase the correct class logit.
        """
        delta_ih = self.theta_in[:, None] - self.theta_hid[None, :]
        d_input  = np.sum(self.W_ih * np.sin(delta_ih), axis=0)
        z_mean   = np.mean(np.exp(1j * self.theta_hid))
        R, Psi   = float(np.abs(z_mean)), float(np.angle(z_mean))
        d_lat    = self.K_lat * R * np.sin(Psi - self.theta_hid)

        # d(features)/d(theta_i) for each Fourier component.
        # Features are [cos(kt), sin(kt)] for k = 1..n_harmonics, so the
        # derivatives are [-k·sin(kt), k·cos(kt)]. Shape (2·n_harmonics, n_hid).
        t  = self.theta_hid
        n  = self.n_hid
        n_feat_rows = 2 * self.n_harmonics
        df_rows = []
        for k in range(1, self.n_harmonics + 1):
            df_rows.append(-k * np.sin(k * t))   # d/dt cos(kt)
            df_rows.append( k * np.cos(k * t))   # d/dt sin(kt)
        df = np.array(df_rows)                    # (2·n_harmonics, n_hid)

        # d(logit[k])/d(theta_i) = Σ_m df[m,i] * W_out[m*n+i, k]
        W_r     = self.W_out.reshape(n_feat_rows, n, self.n_out)
        d_logit = (df[:, :, None] * W_r).sum(axis=0)     # (n, n_out)

        # Teaching current: negative gradient of CE loss w.r.t. theta
        # = -d_logit @ error  (ascent toward correct class)
        teach = -(d_logit @ error)

        # Normalise so clamp_strength is interpretable regardless of scale
        teach_max = np.abs(teach).max()
        if teach_max > 1e-8:
            teach = teach / teach_max

        dtheta  = self.dt * (self.omega_hid + d_input + d_lat
                             + self.clamp_strength * teach)
        dtheta += self.noise * self.rng.standard_normal(self.n_hid)
        self.theta_hid = _wrap(self.theta_hid + dtheta)

    def _reset_hidden(self) -> None:
        if self.phase_init == "zeros":
            # Deterministic aligned start. Removes the per-inference random
            # init variance that may be the source of the train-loss drift —
            # same input always produces the same free-phase trajectory.
            self.theta_hid = np.zeros(self.n_hid)
        else:
            self.theta_hid = self.rng.uniform(-np.pi, np.pi, self.n_hid)

    # ── Fourier Feature Readout ───────────────────────────────────────────────

    def _features(self) -> np.ndarray:
        t = self.theta_hid
        # cos(kt), sin(kt) for k = 1 .. n_harmonics. Default n_harmonics=2 gives
        # the original [cos, sin, cos2, sin2]. Higher harmonics capture finer
        # phase structure at the cost of more readout weights.
        parts = []
        for k in range(1, self.n_harmonics + 1):
            parts.append(np.cos(k * t))
            parts.append(np.sin(k * t))
        return np.concatenate(parts)

    def _logits(self, h: np.ndarray | None = None) -> np.ndarray:
        return self.W_out.T @ (h if h is not None else self._features())

    def _confidence(self, logits: np.ndarray) -> float:
        """Margin between top-2 softmax probabilities. Always in [0, 1]."""
        if self.n_out < 2:
            e = np.exp(logits - logits.max())
            return float(e[0] / e.sum())
        e = np.exp(logits - logits.max())
        probs = e / e.sum()
        top2 = np.partition(probs, -2)[-2:]
        return float(top2[1] - top2[0])

    def _phase_coherence(self) -> float:
        """Kuramoto order parameter R = |mean(exp(i·θ))| in [0, 1].

        Physics-based OOD signal: in-distribution inputs drive the oscillators
        into a synchronized regime (high R, typically 0.7-1.0). Random/OOD
        inputs fail to lock the network — phases stay scattered (low R, ~0.1-0.5).

        Confidence alone confuses overconfident softmax outputs on garbage with
        real signals. R doesn't lie: if the dynamics aren't coherent, the
        network didn't "recognize" anything regardless of what the readout says.
        """
        return float(np.abs(np.mean(np.exp(1j * self.theta_hid))))

    # ── Inference ─────────────────────────────────────────────────────────────

    def infer(
        self,
        x:          np.ndarray,
        max_cycles: int = 80,
        min_cycles: int = 8,
        n_samples:  int = 1,
    ) -> tuple[int, int, float, float, bool]:
        """Adaptive inference. Stops when confidence > conf_thresh or max_cycles.

        n_samples > 1 runs inference repeatedly from different random phase
        inits and averages the logits before deciding. Hypothesis: the readout
        is noisy because the free-phase trajectory depends on the random init;
        averaging several inits should cancel that variance and lift accuracy.
        (No effect when phase_init='zeros', since every run is identical.)

        Returns: (pred, cycles_used, final_conf, final_R, is_ood)
        """
        if n_samples > 1 and self.phase_init != "zeros":
            logit_acc = np.zeros(self.n_out)
            total_cycles = 0
            last_R = 0.0
            for _ in range(n_samples):
                lg, cyc, R = self._infer_once(x, max_cycles, min_cycles)
                logit_acc += lg
                total_cycles += cyc
                last_R = R
            logits = logit_acc / n_samples
            conf   = self._confidence(logits)
            pred   = int(np.argmax(logits))
            is_ood = conf < self.ood_floor
            return pred, total_cycles // n_samples, conf, last_R, is_ood

        logits, cycle, R = self._infer_once(x, max_cycles, min_cycles)
        conf   = self._confidence(logits)
        pred   = int(np.argmax(logits))
        is_ood = conf < self.ood_floor
        return pred, cycle, conf, R, is_ood

    def _infer_once(self, x, max_cycles, min_cycles) -> tuple[np.ndarray, int, float]:
        """One settling run. Returns (final_logits, cycles_used, final_R)."""
        self.encode_input(x)
        self._reset_hidden()
        best_conf = 0.0
        logits    = np.zeros(self.n_out)
        cycle     = 0
        for cycle in range(max_cycles):
            self._step_hidden()
            if cycle >= min_cycles:
                logits = self._logits()
                conf   = self._confidence(logits)
                if conf > best_conf:
                    best_conf = conf
                if conf >= self.conf_thresh:
                    break
        return logits, cycle + 1, self._phase_coherence()

    # ── Learning ──────────────────────────────────────────────────────────────

    def learn(
        self,
        x:             np.ndarray,
        y:             int,
        cycles_settle: int = 60,
    ) -> float:
        """Contrastive Hebbian Learning.

        W_out: momentum Perceptron (unchanged from v2).

        W_ih: Contrastive Hebbian Learning.
          Free phase  — oscillators settle under input drive (no teaching).
          Clamped phase — same init, re-settle with teaching current injected
                          (negative gradient of CE loss w.r.t. hidden phases).
          Update: ΔW_ih = lr_ih * (clamp_corr − free_corr)
          This creates a contrastive signal: pulls hidden toward the correct
          class configuration, pushes it away from the wrong one. Approximates
          backprop without any global gradient computation.
        """
        self.encode_input(x)
        self._reset_hidden()

        # ── Free phase ──
        for _ in range(cycles_settle):
            self._step_hidden()
        theta_free = self.theta_hid.copy()

        h      = self._features()
        logits = self._logits(h)
        pred   = int(np.argmax(logits))

        # ── W_out: momentum Perceptron ──
        if pred != int(y):
            grad = np.zeros_like(self.W_out)
            grad[:, int(y)] =  h
            grad[:, pred]   = -h
            self._W_out_velocity = (
                self.momentum * self._W_out_velocity
                + (1.0 - self.momentum) * grad
            )
            self.W_out = np.clip(
                self.W_out + self.lr_out * self._W_out_velocity,
                -8.0, 8.0
            )

        # ── Clamped phase ──
        # Softmax gradient: error[y] < 0 (want to increase), error[pred] > 0 (decrease)
        e     = np.exp(logits - logits.max())
        probs = e / e.sum()
        error = probs.copy()
        error[int(y)] -= 1.0

        # Re-settle from the free-phase state with teaching current
        self.theta_hid = theta_free.copy()
        cycles_clamp   = max(8, cycles_settle // 3)
        for _ in range(cycles_clamp):
            self._step_hidden_clamped(error)
        theta_clamped = self.theta_hid.copy()

        # ── W_ih: Contrastive Hebbian update ──
        free_corr  = np.cos(self.theta_in[:, None] - theta_free[None, :])
        clamp_corr = np.cos(self.theta_in[:, None] - theta_clamped[None, :])
        delta = clamp_corr - free_corr
        if self.lr_ih_mom > 0.0:
            # EMA momentum smooths the noisy per-example CHL signal. Hypothesis:
            # the train-loss drift is from noisy updates fighting each other;
            # momentum should average them into a more stable descent direction.
            if self._W_ih_velocity is None:
                self._W_ih_velocity = np.zeros_like(self.W_ih)
            self._W_ih_velocity = (self.lr_ih_mom * self._W_ih_velocity
                                   + (1.0 - self.lr_ih_mom) * delta)
            delta = self._W_ih_velocity
        self.W_ih  = np.clip(self.W_ih + self.lr_ih * delta, -6.0, 6.0)
        self.W_ih *= (1.0 - self.decay)

        # Cross-entropy loss on free-phase prediction
        return float(-np.log(probs[int(y)] + 1e-9))

    # ── Proper readout (frozen features + convex classifier) ───────────────────

    def _settle_features(self, x: np.ndarray, settle_cycles: int) -> np.ndarray:
        """Run the (frozen) dynamics for a fixed number of cycles and return
        the Fourier feature vector. Fixed-cycle (not adaptive) so every sample
        gets the same settling budget — the readout classifier wants consistent
        features, not confidence-gated ones."""
        self.encode_input(x)
        self._reset_hidden()
        for _ in range(settle_cycles):
            self._step_hidden()
        return self._features()

    def extract_features(self, x: np.ndarray,
                         n_samples: int | None = None,
                         settle_cycles: int | None = None) -> np.ndarray:
        """Feature vector for one input. Averages over n_samples random inits
        to cancel init-dependent variance (no effect if phase_init='zeros')."""
        n_samples     = self._readout_samples if n_samples is None else n_samples
        settle_cycles = self._readout_settle  if settle_cycles is None else settle_cycles
        if n_samples > 1 and self.phase_init != "zeros":
            acc = np.zeros(self.W_out.shape[0])
            for _ in range(n_samples):
                acc += self._settle_features(x, settle_cycles)
            return acc / n_samples
        return self._settle_features(x, settle_cycles)

    def fit_readout(self, X: np.ndarray, Y: np.ndarray,
                    clf: str = "ridge",
                    n_samples: int = 5,
                    settle_cycles: int = 50,
                    svm_C: float = 10.0,
                    svm_gamma="scale",
                    grid_search: bool = False) -> None:
        """Freeze the trained dynamics, extract features for all of X, and fit a
        convex classifier on them. Call AFTER fit(). This is NOT backprop through
        the oscillators — only the final linear layer is fit optimally.

        grid_search (SVM only): 3-fold CV search over C/gamma to tune each agent
        individually. Search uses a fast non-probability SVC; the winning params
        are then refit once with probability=True for soft-voting support."""
        from sklearn.linear_model import LogisticRegression, RidgeClassifier
        self._readout_samples = n_samples
        self._readout_settle  = settle_cycles
        F = np.zeros((len(X), self.W_out.shape[0]))
        for i, x in enumerate(X):
            F[i] = self.extract_features(x, n_samples, settle_cycles)
        if clf == "logistic":
            self.readout_clf = LogisticRegression(max_iter=2000, C=1.0)
            self.readout_clf.fit(F, Y)
        elif clf == "svm":
            from sklearn.svm import SVC
            if grid_search:
                from sklearn.model_selection import GridSearchCV
                grid = {"C": [1, 10, 100, 300],
                        "gamma": ["scale", 0.02, 0.01, 0.005, 0.002]}
                gs = GridSearchCV(SVC(kernel="rbf", random_state=0), grid,
                                  cv=3, n_jobs=-1)
                gs.fit(F, Y)
                svm_C, svm_gamma = gs.best_params_["C"], gs.best_params_["gamma"]
                self.readout_best_params = gs.best_params_
            self.readout_clf = SVC(kernel="rbf", C=svm_C, gamma=svm_gamma,
                                   probability=True, random_state=0)
            self.readout_clf.fit(F, Y)
        else:
            self.readout_clf = RidgeClassifier(alpha=1.0)
            self.readout_clf.fit(F, Y)

    def readout_proba(self, x: np.ndarray) -> np.ndarray:
        """Full class-probability vector from the proper readout. Logistic gives
        predict_proba directly; ridge has no probabilities, so we softmax its
        decision_function. Used for soft-voting at the council level."""
        f = self.extract_features(x).reshape(1, -1)
        if hasattr(self.readout_clf, "predict_proba"):
            return self.readout_clf.predict_proba(f)[0]
        scores = self.readout_clf.decision_function(f)[0]
        e = np.exp(scores - scores.max())
        return e / e.sum()

    def infer_readout(self, x: np.ndarray) -> tuple[int, float]:
        """Predict via the proper readout. Returns (pred, confidence).
        Confidence = top-2 probability margin (always in [0,1])."""
        probs = self.readout_proba(x)
        pred  = int(np.argmax(probs))
        top2  = np.partition(probs, -2)[-2:]
        conf  = float(top2[1] - top2[0])
        return pred, conf

    # ── Evaluation ────────────────────────────────────────────────────────────

    def accuracy(
        self,
        X:          np.ndarray,
        Y:          np.ndarray,
        max_cycles: int = 80,
    ) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
        """Returns (acc, cycles_per_sample, conf_per_sample, ood_mask).
        Uses the proper readout if one has been fit, else the perceptron."""
        n = len(X)
        correct  = 0
        cycles   = np.zeros(n, dtype=int)
        confs    = np.zeros(n)
        ood_mask = np.zeros(n, dtype=bool)
        for i, (x, y) in enumerate(zip(X, Y)):
            if self.readout_clf is not None:
                pred, conf = self.infer_readout(x)
                c, ood = self._readout_settle, False
            else:
                pred, c, conf, _R, ood = self.infer(x, max_cycles=max_cycles)
            correct    += int(pred == int(y))
            cycles[i]   = c
            confs[i]    = conf
            ood_mask[i] = ood
        return correct / n, cycles, confs, ood_mask

    def fit(
        self,
        X:             np.ndarray,
        Y:             np.ndarray,
        epochs:        int   = 40,
        cycles_settle: int   = 60,
        shuffle:       bool  = True,
        verbose:       bool  = True,
        eval_every:    int   = 5,
        val_frac:      float = 0.15,
        patience:      int   = 3,
        lr_schedule:   str   = "cosine",
        lr_min_frac:   float = 0.05,
        noise_anneal:  bool  = False,
    ) -> None:
        """Train with val-checkpoint + early stopping + LR decay.

        Local-learning agents on digits typically peak around epoch 10/60 then
        drift downward. Without early stopping, 75-83% of training compute is
        thrown away. `patience` = number of consecutive evals without val
        improvement before we stop. Set patience=0 to disable early stopping.

        `lr_schedule` controls learning-rate decay across training:
          - "cosine": smooth cosine decay from base LR → base * lr_min_frac.
            Reduces post-peak oscillation that was wasting 75% of compute on v3.
          - "none": no decay; lr_ih and lr_out stay at their __init__ values.
        Base LRs are restored at the end of fit() so re-fitting the same model
        starts from the same place.
        """
        rng = np.random.default_rng()
        # Snapshot base LRs so we can restore after fit() and so the schedule
        # has a stable reference point regardless of how many times fit() runs.
        base_lr_ih  = self.lr_ih
        base_lr_out = self.lr_out
        base_noise  = self.noise

        # Hold out a validation split so checkpointing tracks generalisation,
        # not training-set memorisation.
        n      = len(X)
        n_val  = max(1, int(n * val_frac))
        perm   = rng.permutation(n)
        val_idx   = perm[:n_val]
        train_idx = perm[n_val:]
        X_tr, Y_tr = X[train_idx], Y[train_idx]
        X_val, Y_val = X[val_idx],   Y[val_idx]

        best_val_acc  = 0.0
        best_W_ih     = self.W_ih.copy()
        best_W_out    = self.W_out.copy()
        best_epoch    = 0
        evals_since_improvement = 0
        stopped_early = False

        for epoch in range(1, epochs + 1):
            # Decay LR before the epoch's pass over the data. Cosine goes from
            # 1.0 at epoch 0 → lr_min_frac at epoch=epochs, smoothly. This is
            # the standard "anneal near optimum" trick — letting the network
            # take smaller steps as it approaches its best weights stops the
            # epoch-10 peak/drift pattern seen in v3 runs.
            if lr_schedule == "cosine" and epochs > 1:
                progress = (epoch - 1) / (epochs - 1)
                scale = lr_min_frac + 0.5 * (1.0 - lr_min_frac) * (1.0 + np.cos(np.pi * progress))
                self.lr_ih  = base_lr_ih  * scale
                self.lr_out = base_lr_out * scale
                # Optionally anneal exploration noise on the same cosine curve.
                # Hypothesis: late-training noise drowns the signal and causes
                # the post-peak drift; shrinking it lets the network settle.
                if noise_anneal:
                    self.noise = base_noise * scale

            idx  = rng.permutation(len(X_tr)) if shuffle else np.arange(len(X_tr))
            loss = sum(self.learn(X_tr[i], int(Y_tr[i]), cycles_settle) for i in idx) / len(X_tr)
            self.train_losses.append(loss)

            # Eval cadence runs regardless of verbose, because early stopping
            # needs val_acc to make its decision. Verbose only controls printing.
            if epoch % eval_every == 0:
                val_acc, _, _, _ = self.accuracy(X_val, Y_val)
                improved = val_acc > best_val_acc
                if improved:
                    best_val_acc = val_acc
                    best_W_ih    = self.W_ih.copy()
                    best_W_out   = self.W_out.copy()
                    best_epoch   = epoch
                    evals_since_improvement = 0
                else:
                    evals_since_improvement += 1

                if verbose:
                    tr_acc, cyc, conf, ood = self.accuracy(X_tr, Y_tr)
                    marker = " ← best" if improved else ""
                    print(
                        f"  Epoch {epoch:3d}/{epochs} | loss {loss:.4f} "
                        f"| tr {tr_acc*100:.1f}% val {val_acc*100:.1f}% "
                        f"| cycles {cyc.mean():.1f} "
                        f"| conf {conf.mean():.2f} "
                        f"| OOD {ood.mean()*100:.1f}%"
                        + marker
                    )

                if patience > 0 and evals_since_improvement >= patience:
                    stopped_early = True
                    if verbose:
                        print(
                            f"  Early stop at epoch {epoch} — val hasn't improved "
                            f"in {patience} consecutive evals ({patience * eval_every} epochs)."
                        )
                    break

        # Restore the checkpoint with the best validation accuracy
        self.W_ih  = best_W_ih
        self.W_out = best_W_out
        # Restore base LRs/noise so subsequent fit() calls don't inherit the
        # decayed values from this run.
        self.lr_ih  = base_lr_ih
        self.lr_out = base_lr_out
        self.noise  = base_noise
        if verbose:
            tag = " (early-stopped)" if stopped_early else ""
            print(f"  Restored best checkpoint from epoch {best_epoch} (val {best_val_acc*100:.1f}%){tag}")


def _wrap(theta: np.ndarray) -> np.ndarray:
    return (theta + np.pi) % (2 * np.pi) - np.pi
