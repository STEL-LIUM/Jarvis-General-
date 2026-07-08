"""
PROMETHEUS Council — Multi-Swarm Agent System v3

OOD detection: inter-agent disagreement.
  In-distribution inputs → agents agree on the predicted class.
  OOD inputs → agents fire randomly → high disagreement.
  Agreement = (agents voting for plurality class) / n_agents.
  council_ood = True if agreement < ood_agree_thresh.

This is immune to the softmax overconfidence problem that breaks
confidence-threshold OOD detection on unseen inputs.
"""

import numpy as np
try:
    from .core import OscillatorNetwork
except ImportError:
    # Allow running council.py / demo scripts directly (not as a package)
    from core import OscillatorNetwork


class SwarmCouncil:
    def __init__(
        self,
        n_agents:        int,
        n_input:         int,
        n_hidden:        int,
        n_output:        int,
        dt:              float = 0.10,
        lr_ih:           float = 0.012,
        lr_out:          float = 0.06,
        momentum:        float = 0.85,
        noise:           float = 0.004,
        decay:           float = 0.0002,
        K_lat:           float = 0.35,
        conf_thresh:      float = 0.60,
        ood_floor:        float = 0.10,
        clamp_strength:   float = 0.50,
        ood_agree_thresh: float = 0.60,  # primary OOD signal: flag if agreement < this
        ood_conf_thresh:  float = 0.70,  # secondary (weak) — see ood_use_conf
        ood_use_conf:     bool  = False, # confidence separation is only ~0.06; off by default
        # Forwarded to each agent (experiment hooks from core.py)
        phase_init:       str   = "random",
        lr_ih_mom:        float = 0.0,
        n_harmonics:      int   = 2,
    ):
        self.n_agents         = n_agents
        self.n_out            = n_output
        self.ood_agree_thresh = ood_agree_thresh
        self.ood_conf_thresh  = ood_conf_thresh
        self.ood_use_conf     = ood_use_conf

        freq_stds = np.linspace(0.10, 0.45, n_agents)

        self.agents = [
            OscillatorNetwork(
                n_input, n_hidden, n_output,
                dt=dt, lr_ih=lr_ih, lr_out=lr_out, momentum=momentum,
                noise=noise, decay=decay, K_lat=K_lat,
                conf_thresh=conf_thresh, ood_floor=ood_floor,
                clamp_strength=clamp_strength,
                phase_init=phase_init, lr_ih_mom=lr_ih_mom, n_harmonics=n_harmonics,
                freq_std=float(freq_stds[i]),
                rng=np.random.default_rng(seed=i * 1337 + 42),
            )
            for i in range(n_agents)
        ]

    # ── Training ──────────────────────────────────────────────────────────────

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
        fit_readout:   bool  = False,
        readout_clf:   str   = "ridge",
        readout_samples: int = 5,
        readout_settle:  int = 50,
        readout_grid_search: bool = False,
    ) -> None:
        for i, agent in enumerate(self.agents):
            if verbose:
                print(f"\n── Agent {i+1}/{self.n_agents} (ω_std={agent.omega_hid.std():.3f}) ──")
            agent.fit(X, Y, epochs=epochs, cycles_settle=cycles_settle,
                      shuffle=shuffle, verbose=verbose, eval_every=eval_every,
                      val_frac=val_frac, patience=patience,
                      lr_schedule=lr_schedule, lr_min_frac=lr_min_frac)
            if fit_readout:
                if verbose:
                    tag = " + grid search" if readout_grid_search else ""
                    print(f"  Fitting proper readout ({readout_clf}{tag}, "
                          f"{readout_samples}-sample feats)…")
                agent.fit_readout(X, Y, clf=readout_clf,
                                  n_samples=readout_samples,
                                  settle_cycles=readout_settle,
                                  grid_search=readout_grid_search)
                if verbose and getattr(agent, "readout_best_params", None):
                    print(f"    best SVM params: {agent.readout_best_params}")

    # ── Inference ─────────────────────────────────────────────────────────────

    def predict_full(
        self,
        x:          np.ndarray,
        max_cycles: int = 80,
    ) -> tuple[int, np.ndarray, np.ndarray, np.ndarray, bool]:
        """
        Returns (final_pred, per_agent_preds, per_agent_conf, per_agent_cycles, council_ood).

        OOD signal: inter-agent agreement (fraction of agents voting for the
        plurality class). This is the only signal with real separation between
        in-dist (~0.73) and OOD noise (~0.50). Confidence separation is weak
        (~0.06) and phase coherence R turned out useless (separation ~ -0.02,
        because this architecture never globally synchronizes — it computes via
        local phase *relationships*, not mean-field coherence). So we flag OOD
        on low agreement, optionally AND-ed with low confidence.
        """
        preds  = np.zeros(self.n_agents, dtype=int)
        confs  = np.zeros(self.n_agents)
        cycles = np.zeros(self.n_agents, dtype=int)
        votes  = np.zeros(self.n_out)

        for i, agent in enumerate(self.agents):
            if agent.readout_clf is not None:
                p, conf = agent.infer_readout(x)
                c = agent._readout_settle
            else:
                p, c, conf, _R, _ = agent.infer(x, max_cycles=max_cycles)
            preds[i]  = p
            confs[i]  = conf
            cycles[i] = c
            votes[p] += max(conf, 0.01)

        vote_counts = np.bincount(preds, minlength=self.n_out)
        agreement   = float(vote_counts.max()) / self.n_agents
        mean_conf   = float(confs.mean())

        council_ood = agreement < self.ood_agree_thresh
        if self.ood_use_conf:
            council_ood = council_ood and (mean_conf < self.ood_conf_thresh)

        return int(np.argmax(votes)), preds, confs, cycles, council_ood

    def predict_soft(self, x: np.ndarray) -> tuple[int, np.ndarray]:
        """Soft-voting prediction: average the agents' probability distributions
        and take the argmax. Requires fitted readouts. When agents are
        individually strong (here ~97-98%), averaging probabilities beats
        confidence-weighted hard voting — a confident-but-wrong agent can't
        flip the result, it just shifts the mean slightly.
        Returns (pred, mean_prob_vector)."""
        acc = np.zeros(self.n_out)
        for agent in self.agents:
            acc += agent.readout_proba(x)
        acc /= self.n_agents
        return int(np.argmax(acc)), acc

    def accuracy_soft(self, X: np.ndarray, Y: np.ndarray) -> float:
        """Council test accuracy using soft voting."""
        correct = sum(int(self.predict_soft(x)[0] == int(y))
                      for x, y in zip(X, Y))
        return correct / len(X)

    def predict(self, x: np.ndarray, max_cycles: int = 80) -> tuple[int, bool]:
        """Returns (predicted_class, is_ood)."""
        pred, _, _, _, ood = self.predict_full(x, max_cycles)
        return pred, ood

    # ── Evaluation ────────────────────────────────────────────────────────────

    def accuracy(
        self,
        X:          np.ndarray,
        Y:          np.ndarray,
        max_cycles: int  = 80,
        verbose:    bool = False,
    ) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
        """Returns (acc, cycles_matrix, conf_matrix, ood_per_sample)."""
        n = len(X)
        cycles_m = np.zeros((n, self.n_agents), dtype=int)
        conf_m   = np.zeros((n, self.n_agents))
        ood_all  = np.zeros(n, dtype=bool)
        correct  = 0

        for i, (x, y) in enumerate(zip(X, Y)):
            pred, _, confs, cycles, ood = self.predict_full(x, max_cycles)
            cycles_m[i] = cycles
            conf_m[i]   = confs
            ood_all[i]  = ood
            correct     += int(pred == int(y))

        acc = correct / n
        if verbose:
            print(f"Council accuracy : {acc*100:.1f}%")
            print(f"OOD rate         : {ood_all.mean()*100:.1f}% of test samples")
            print(f"Mean cycles/agent: {cycles_m.mean(axis=0).round(1)}")
            print(f"Mean conf/agent  : {conf_m.mean(axis=0).round(3)}")
        return acc, cycles_m, conf_m, ood_all

    def agent_accuracies(self, X: np.ndarray, Y: np.ndarray, max_cycles: int = 80) -> np.ndarray:
        return np.array([
            agent.accuracy(X, Y, max_cycles=max_cycles)[0]
            for agent in self.agents
        ])

    def ood_detection(
        self,
        X_in:       np.ndarray,
        X_ood:      np.ndarray,
        max_cycles: int = 80,
    ) -> dict:
        """
        Triple-signal OOD: agreement, confidence, phase coherence R.
        At least 2 of 3 signals must be low to flag OOD.

        Returns dict with TPR/FPR plus per-group means and separations for
        each signal (so we can see which one is doing the work).
        """
        def scores(X):
            agreements = np.zeros(len(X))
            mean_confs = np.zeros(len(X))
            mean_Rs    = np.zeros(len(X))
            for i, x in enumerate(X):
                # Inline what predict_full does so we capture per-agent R too
                preds = np.zeros(self.n_agents, dtype=int)
                confs = np.zeros(self.n_agents)
                Rs    = np.zeros(self.n_agents)
                for j, agent in enumerate(self.agents):
                    p, _c, conf, R, _ = agent.infer(x, max_cycles=max_cycles)
                    preds[j] = p
                    confs[j] = conf
                    Rs[j]    = R
                vote_counts   = np.bincount(preds, minlength=self.n_out)
                agreements[i] = float(vote_counts.max()) / self.n_agents
                mean_confs[i] = float(confs.mean())
                mean_Rs[i]    = float(Rs.mean())
            return agreements, mean_confs, mean_Rs

        agree_in,  conf_in,  R_in  = scores(X_in)
        agree_ood, conf_ood, R_ood = scores(X_ood)

        def flag(agreement, conf):
            f = agreement < self.ood_agree_thresh
            if self.ood_use_conf:
                f = f & (conf < self.ood_conf_thresh)
            return f

        is_ood_in  = flag(agree_in,  conf_in)
        is_ood_ood = flag(agree_ood, conf_ood)

        tp  = float((~is_ood_in).mean())    # in-dist correctly accepted
        tn  = float(is_ood_ood.mean())      # OOD correctly rejected
        fp  = 1.0 - tn
        fn  = 1.0 - tp

        return {
            "in_dist_mean_agreement" : float(agree_in.mean()),
            "ood_mean_agreement"     : float(agree_ood.mean()),
            "agreement_separation"   : float(agree_in.mean() - agree_ood.mean()),
            "in_dist_mean_conf"      : float(conf_in.mean()),
            "ood_mean_conf"          : float(conf_ood.mean()),
            "conf_separation"        : float(conf_in.mean() - conf_ood.mean()),
            "in_dist_mean_R"         : float(R_in.mean()),
            "ood_mean_R"             : float(R_ood.mean()),
            "R_separation"           : float(R_in.mean() - R_ood.mean()),
            "acceptance_rate_in"     : tp,
            "rejection_rate_ood"     : tn,
            "false_accept_rate"      : fp,
            "false_reject_rate"      : fn,
            # Backwards-compat key for old demo scripts
            "separation"             : float(agree_in.mean() - agree_ood.mean()),
        }
