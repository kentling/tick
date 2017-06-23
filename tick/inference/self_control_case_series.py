from abc import ABC
import itertools
import numpy as np
from sklearn.model_selection import KFold, StratifiedKFold
from operator import itemgetter
from scipy.misc import comb
from itertools import chain
from tick.base import Base
from tick.optim.prox import ProxTV, ProxMulti, ProxZero, ProxEquality, ProxL1
from tick.optim.solver import SVRG
from tick.optim.model import ModelSCCS
from tick.preprocessing import LongitudinalFeaturesProduct,\
    LongitudinalFeaturesLagger
from tick.simulation import SimuSCCS


class LearnerSCCS(ABC, Base):
    _attrinfos = {
        "solver": {
            "writable": False
        },
        "penalty": {
            "writable": False
        },
        "_solver_obj": {
            "writable": False
        },
        "_prox_obj": {
            "writable": False
        },
        "_model_obj": {
            "writable": False
        },
        "_preprocessor_obj": {
            "writable": False
        },
        "_fitted": {
            "writable": False
        },
        "_refitted": {
            "writable": False
        },
        "random_state": {
            "writable": False
        },
        "_warm_start": {
            "writable": False
        },
        "coeffs": {
            "writable": False
        },
        "refit_coeffs": {
            "writable": False
        },
        "refit_CI": {
            "writable": False
        },
        "n_intervals": {
            "writable": False
        },
        "n_lags": {
            "writable": False
        },
        "feature_products": {
            "writable": False
        },
        "feature_type": {
            "writable": False
        },
        "n_features": {
            "writable": False
        },
        "n_coeffs": {
            "writable": False
        },
    }

    _penalties = {
        'None': ProxZero,
        'TV': ProxTV,
        'Equality': ProxEquality,
        'L1-TV': [ProxL1, ProxTV]
    }

    def __init__(self, n_lags: int=0, feature_products=False,
                 feature_type="infinite", penalty='TV', strength_TV: float=0,
                 strength_L1: float=0, step=None, tol=1e-5, max_iter=100,
                 verbose=True, print_every=10, record_every=10,
                 warm_start=False, random_state=None):
        Base.__init__(self)

        # Check args
        if feature_type not in ["infinite", "short"]:
            raise ValueError("``feature_type`` should be either ``infinite`` or\
                         ``short``.")
        allowed_penalties = list(self._penalties.keys())
        allowed_penalties.sort()
        if penalty not in allowed_penalties:
            raise ValueError("``penalty`` must be one of %s, got %s" %
                             (', '.join(allowed_penalties), penalty))

        self.n_intervals = None
        self.n_features = None
        self.n_coeffs = None
        self.n_lags = int(n_lags)
        self.feature_products = feature_products
        self.feature_type = feature_type
        self.penalty = penalty
        self.strength_TV = strength_TV
        self.strength_L1 = strength_L1
        self._preprocessor_obj = self._construct_preprocessor_obj()
        self._model_obj = self._construct_model_obj()
        self._solver_obj = self._construct_solver_obj(step, max_iter, tol,
                                                      print_every, record_every,
                                                      verbose, random_state)
        self.refit_coeffs = None
        self.refit_CI = None
        self.coeffs = None
        self._fitted = False
        self._refitted = False
        self._prox_obj = None
        self.warm_start = warm_start

    def fit(self, features: np.ndarray, labels: np.array,
            censoring: np.array):
        """Fit the model according to the given training data.

        Parameters
        ----------
        features : `list` of `numpy.ndarray` or `list` of `scipy.sparse.csr_matrix`,
            list of length n_samples, each element of the list of
            shape=(n_intervals, n_features)
            The list of features matrices.

        labels : `list` of `numpy.ndarray`,
            list of length n_samples, each element of the list of
            shape=(n_intervals,)
            The labels vector

        censoring : `numpy.ndarray`, shape=(n_samples,), dtype="uint64"
            The censoring data. This array should contain integers in
            [1, n_intervals]. If the value i is equal to n_intervals, then there
            is no censoring for sample i. If censoring = c < n_intervals, then
            the observation of sample i is stopped at interval c, that is, the
            row c - 1 of the corresponding matrix. The last n_intervals - c rows
            are then set to 0.

        Returns
        -------
        output : `LearnerSCCS`
            The current instance with given data
        """
        features, labels, censoring = self._preprocess(features,
                                                       labels,
                                                       censoring)

        self._compute_step(features, labels, censoring)

        groups = self._coefficient_groups(self.penalty)
        prox_obj = self._construct_prox_obj(self.penalty, groups)
        self._set("_prox_obj", prox_obj)

        coeffs = self._fit(prox_obj)
        self._set("coeffs", coeffs)
        self._set("_fitted", True)

        return self

    def score(self, features=None, labels=None, censoring=None):
        """Returns the negative log-likelihood of the model, using the current
        fitted coefficients on the passed data.
        If no data is passed, the negative log-likelihood is computed using the
        data used for training.

        Parameters
        ----------
        features : `None` or `list` of `numpy.ndarray` or `list` of `scipy.sparse.csr_matrix`,
            list of length n_samples, each element of the list of
            shape=(n_intervals, n_features)
            The list of features matrices.

        labels : `None` or `list` of `numpy.ndarray`,
            list of length n_samples, each element of the list of
            shape=(n_intervals,)
            The labels vector

        censoring : `None` or `numpy.ndarray`, shape=(n_samples,), dtype="uint64"
            The censoring data. This array should contain integers in
            [1, n_intervals]. If the value i is equal to n_intervals, then there
            is no censoring for sample i. If censoring = c < n_intervals, then
            the observation of sample i is stopped at interval c, that is, the
            row c - 1 of the corresponding matrix. The last n_intervals - c rows
            are then set to 0.

        Returns
        -------
        output : `float`
            The value of the negative log-likelihood
        """
        if not self._fitted:
            raise RuntimeError('You must fit the model first')

        all_none = all(e is None for e in [features, labels, censoring])
        if all_none:
            loss = self._model_obj.loss(self.coeffs)
        else:
            if features is None:
                raise ValueError('Passed ``features`` is None')
            elif labels is None:
                raise ValueError('Passed ``labels`` is None')
            elif censoring is None:
                raise ValueError('Passed ``censoring`` is None')
            else:
                model = self._construct_model_obj().fit(features, labels,
                                                        censoring)
                loss = model.loss(self.coeffs)

        return loss

    def fit_KFold_CV(self, features, labels, censoring, strength_TV_list,
                     strength_L1_list=[0], n_splits=3, stratified=True,
                     shuffle=False, random_state=None, refit=False,
                     bootstrap_rep=200, bootstrap_confidence=.95, ):
        features, labels, censoring = self._preprocess(features,
                                                       labels,
                                                       censoring)
        # Compute lip const on all the data
        self._compute_step(features, labels, censoring)

        # split the data
        kf = StratifiedKFold(n_splits, shuffle, random_state) if stratified\
            else KFold(n_splits, shuffle, random_state)

        # Construct prox here
        groups = self._coefficient_groups(self.penalty)

        # Training loop
        scores = []
        strength_list = itertools.product(strength_L1_list, strength_TV_list)
        for strength_L1, strength_TV in strength_list:
            # create prox instance
            self._set("strength_L1", strength_L1)
            self._set("strength_TV", strength_TV)
            prox_obj = self._construct_prox_obj(self.penalty, groups)

            kfold_scores_train = []
            kfold_scores_test = []
            for train_index, test_index in kf.split(features, labels):
                train = itemgetter(*train_index.tolist())
                test = itemgetter(*test_index.tolist())
                X_train, X_test = list(train(features)), list(test(features))
                y_train, y_test = list(train(labels)), list(test(labels))
                censoring_train, censoring_test = censoring[train_index], \
                    censoring[test_index]
                self._model_obj.fit(X_train, y_train, censoring_train)
                self._fit(prox_obj)

                kfold_scores_train.append(self.score())
                kfold_scores_test.append(self.score(X_test, y_test,
                                                    censoring_test))

            scores.append({
                "n_intervals": self.n_intervals,
                "n_lags": self.n_lags,
                "n_features": self.n_features,
                "feature_products": self.feature_products,
                "feature_type": self.feature_type,
                "strength_L1": self.strength_L1,
                "strength_TV": self.strength_TV,
                "train": {
                    "mean": np.mean(kfold_scores_train),
                    "var": np.var(kfold_scores_train),
                    "sd": np.sqrt(np.var(kfold_scores_train)),
                    "kfold_scores": kfold_scores_train
                },
                "test": {
                    "mean": np.mean(kfold_scores_test),
                    "var": np.var(kfold_scores_test),
                    "sd": np.sqrt(np.var(kfold_scores_test)),
                    "kfold_scores": kfold_scores_test
                }
            })
        # Find best parameters and refit on full data
        best_idx = np.argmin([s["test"]["mean"] for s in scores])[0]
        # TODO : get min with smaller penalization
        best_parameters = scores[best_idx]
        best_strength_L1 = best_parameters["strength_L1"]
        best_strength_TV = best_parameters["strength_TV"]

        # refit best model on all the data
        self._set("strength_L1", best_strength_L1)
        self._set("strength_TV", best_strength_TV)
        self._set('prox_obj', self._construct_prox_obj(self.penalty, groups))

        self._model_obj.fit(features, labels, censoring)
        coeffs = self._fit(self._prox_obj)
        self._set("coeffs", coeffs)
        self._set("_fitted", True)
        
        if refit:
            self._set('refit_coeffs', self._refit(coeffs, features,
                                                  labels, censoring))
            self._set('refit_CI', self.bootstrap_CI(coeffs, features, censoring,
                                                    bootstrap_rep,
                                                    bootstrap_confidence, # TODO: ???
                                                    random_state))

        best_model = {
            "n_intervals": self.n_intervals,
            "n_lags": self.n_lags,
            "n_features": self.n_features,
            "feature_products": self.feature_products,
            "feature_type": self.feature_type,
            "strength_L1": self.strength_L1,
            "strength_TV": self.strength_TV,
            "coeffs": coeffs.tolist()
        }

        return coeffs, scores, best_model

    def _refit(self, coeffs, features, labels, censoring):
        self._model_obj.fit(features, labels, censoring)
        # We do not recompute Lispchitz constant as it might be very similar to
        # the one used in fit. Thus, computing this constant (which takes some
        # time), is not worth it.
        groups = self._detect_change_points(coeffs)
        prox_obj = self._construct_prox_obj('Equality', groups)
        coeffs = self._fit(prox_obj)
        return coeffs

    def bootstrap_CI(self, coeffs, features, censoring, rep, confidence,
                     random_state=None):
        bootstrap_coeffs = []
        n_samples = len(features)
        for k in range(rep):
            sim = SimuSCCS(n_samples, self.n_intervals, self.n_features,
                           self.n_lags, coeffs=coeffs, sparse=True,
                           verbose=False, exposure_type=self.feature_type,
                           distribution="multinomial", seed=random_state)
            y = sim._simulate_outcomes(features, censoring)
            bootstrap_coeffs.append(self._refit(coeffs, features, y, censoring))
        bootstrap_coeffs = np.array(bootstrap_coeffs)
        bootstrap_coeffs.sort(axis=1)  # TODO check axis
        lower_bound = bootstrap_coeffs[np.ceil(rep * confidence / 2)]
        upper_bound = bootstrap_coeffs[np.ceil(rep * (1 - confidence / 2))]
        return lower_bound, upper_bound

    def _preprocess(self, features, labels, censoring):
        preprocessors = self._preprocessor_obj

        n_intervals, n_features = features[0].shape
        n_features = int(comb(n_features, 2) + n_features
                         if self.feature_products else n_features)
        n_coeffs = n_features * (self.n_lags + 1)
        self._set('n_features', n_features)
        self._set('n_coeffs', n_coeffs)
        self._set('n_intervals', n_intervals)

        features, labels, censoring, _ = SimuSCCS\
            ._filter_non_positive_samples(features, labels, censoring)

        # Feature products
        features = preprocessors[0].transform(features)

        # Lagger
        features = preprocessors[1].transform(features, censoring)

        return features, labels, censoring

    def _compute_step(self, features, labels, censoring):
        self._model_obj.fit(features, labels, censoring)
        if self.step is None:
            step = 1 / self._model_obj.get_lip_max()
            self.step = step
        return self.step

    def _fit(self, prox_obj):
        solver_obj = self._solver_obj
        model_obj = self._model_obj

        # Now, we can pass the model and prox objects to the solver
        solver_obj.set_model(model_obj).set_prox(prox_obj)

        coeffs_start = None
        if self.warm_start and self.coeffs is not None:
            coeffs = self.coeffs
            # ensure starting point has the right format
            if coeffs.shape == (model_obj.n_coeffs,):
                coeffs_start = coeffs

        # Launch the solver
        coeffs = solver_obj.solve(coeffs_start, step=self.step)

        return coeffs

    def _coefficient_groups(self, penalty, coeffs):
        if penalty in ["TV", "L1-TV"]:
            n_grouped_cols = self.n_lags + 1
            groups = [(int(n_grouped_cols * i), int(n_grouped_cols * (i + 1)))
                      for i in range(self.n_features)]
        elif penalty == "Equality":
            groups = self._detect_change_points(coeffs)
        else:
            raise ValueError("`penalty` should be `TV`, `L1-TV` or `Equality`")

        return groups

    def _detect_change_points(self, coeffs):
        coeffs = coeffs.reshape((self.n_features, self.n_lags + 1))
        kernel = np.array([1, -1])
        groups = []
        for l in range(self.n_features):
            idx = 0
            acc = 1
            for change in np.convolve(coeffs[l, :], kernel, 'valid') != 0:
                if change:
                    groups.append((idx, idx + acc))
                    idx += acc
                    acc = 1
                else:
                    acc += 1
            groups.append((idx, 4))
        return groups

    def _construct_preprocessor_obj(self):
        # TODO: add a filter for useless cases here ?
        # TODO: WARNING, TWO PP OBJECTS, should be used in the right order
        features_product = LongitudinalFeaturesProduct(self.feature_type)
        lagger = LongitudinalFeaturesLagger(self.n_lags)
        return features_product, lagger

    def _construct_model_obj(self):
        return ModelSCCS(self.n_intervals, self.n_lags)

    def _construct_prox_obj(self, penalty, groups):
        if penalty == "None":
            proxs = [ProxZero()]
        elif penalty == "TV":
            proxs = (ProxTV(self.strength_TV, range=group) for group in groups)
        elif penalty == "L1_TV":
            # This is a flatmap
            proxs = chain.from_iterable(
                self._prox_L1_TV(self.strength_L1, self.strength_TV, group)
                for group in groups) # TODO alternative: ProxMulti(ProxMulti(ProxL1), ProxMulti(ProxTV))
        elif penalty == "Equality":
            proxs = (ProxEquality(0, range=group) for group in groups)
        else:
            raise ValueError("`penalty` should be either `TV`, `L1-TV`, \
            `Equality` or `None`")

        prox_obj = ProxMulti(tuple(proxs))

        return prox_obj

    def _construct_solver_obj(self, step, max_iter, tol, print_every,
                              record_every, verbose, seed):
        # Parameters of the solver
        solver_args = []
        solver_kwargs = {'max_iter': max_iter, 'tol': tol,
                         'print_every': print_every,
                         'record_every': record_every,
                         'verbose': verbose, 'step': step,
                         'seed': seed}

        solver_obj = SVRG(*solver_args, **solver_kwargs)

        return solver_obj

    @staticmethod
    def _prox_L1_TV(strength_L1, strength_TV, range):
        if range[1] < (range[0] + 1):
            raise ValueError("range[1] should be > range[0]")  # TODO: useless check ?

        proxL1 = ProxL1(strength_L1, range=(range[0], range[0]+1))
        proxTV = ProxTV(strength_TV, range=range)

        return proxL1, proxTV
