# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0

"""
This module provides a simulation utility for comparing algorithms and hyper-parameter tuning.
"""

import logging
import math
from typing import List, Optional, Union, Dict

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from sklearn.metrics import confusion_matrix
from sklearn.model_selection import train_test_split

from mabwiser._version import __author__, __copyright__, __email__, __version__
from mabwiser.base_mab import BaseMAB
from mabwiser.configs.mab import SimulatorConfig
from mabwiser.mab import MAB
from mabwiser.utilities.converters import convert_array, convert_matrix
from mabwiser.utilities.validators import (
    check_false,
    check_fit_input,
    check_len,
    check_in_arms,
    check_true,
    validate_2d,
)
from mabwiser.neighbors.base import _Neighbors
from mabwiser.neighbors.fixed import _Radius, _KNearest
from mabwiser.neighbors.approximate import _LSHNearest
from mabwiser.simulator.mab import _NeighborsSimulator, _LSHSimulator, _RadiusSimulator, _KNearestSimulator

from mabwiser.utilities.general import effective_jobs
from mabwiser.utilities.types import _T



__author__ = __author__
__email__ = __email__
__version__ = __version__
__copyright__ = __copyright__


class Simulator:
    """Multi-Armed Bandit Simulator.

    This utility runs a simulation using historic data and a collection of multi-armed bandits from the MABWiser
    library or that extends the BaseMAB class in MABWiser.

    It can be used to run a simple simulation with a single bandit or to compare multiple bandits for policy selection,
    hyper-parameter tuning, etc.

    Nearest Neighbor bandits that use the default Radius and KNearest implementations from MABWiser are converted to
    custom versions that share distance calculations to speed up the simulation. These custom versions also track
    statistics about the neighborhoods that can be used in evaluation.

    The results can be accessed as the arms_to_stats, model_to_predictions, model_to_confusion_matrices, and
    models_to_evaluations properties.

    When using partial fitting, an additional confusion matrix is calculated for all predictions after all of the
    batches are processed.

    A log of the simulation tracks the experiment progress.

    Attributes
    ----------
    bandits: list[(str, bandit)]
        A list of tuples of the name of each bandit and the bandit object.
    decisions: array
        The complete decision history to be used in train and test.
    rewards: array
        The complete array history to be used in train and test.
    contexts: array
        The complete context history to be used in train and test.
    scaler: scaler
        A scaler object from sklearn.preprocessing.
    test_size: float
        The size of the test set
    logger: Logger
        The logger object.
    arms: list
        The list of arms used by the bandits.
    arm_to_stats_total: dict
        Descriptive statistics for the complete data set.
    arm_to_stats_train: dict
        Descriptive statistics for the training data.
    arm_to_stats_test: dict
        Descriptive statistics for the test data.
    bandit_to_arm_to_stats_avg: dict
        Descriptive statistics for the predictions made by each bandit based on means from training data.
    bandit_to_arm_to_stats_min: dict
        Descriptive statistics for the predictions made by each bandit based on minimums from training data.
    bandit_to_arm_to_stats_max: dict
        Descriptive statistics for the predictions made by each bandit based on maximums from training data.
    bandit_to_confusion_matrices: dict
        The confusion matrices for each bandit.
    bandit_to_predictions: dict
        The prediction for each item in the test set for each bandit.
    bandit_to_expectations: dict
        The arm_to_expectations for each item in the test set for each bandit.
        For context-free bandits, there is a single dictionary for each batch.
    bandit_to_neighborhood_size: dict
        The number of neighbors in each neighborhood for each row in the test set.
        Calculated when using a Radius neighborhood policy, or a custom class that inherits from it.
        Not calculated when is_quick is True.
    bandit_to_arm_to_stats_neighborhoods: dict
        The arm_to_stats for each neighborhood for each row in the test set.
        Calculated when using Radius or KNearest, or a custom class that inherits from one of them.
        Not calculated when is_quick is True.
    test_indices: list
        The indices of the rows in the test set.
        If input was not zero-indexed, these will reflect their position in the input rather than actual index.

    Example
    -------
        >>> from mabwiser.mab import MAB, LearningPolicy
        >>> arms = ['Arm1', 'Arm2']
        >>> decisions = ['Arm1', 'Arm1', 'Arm2', 'Arm1']
        >>> rewards = [20, 17, 25, 9]
        >>> mab1 = MAB(arms, LearningPolicy.EpsilonGreedy(epsilon=0.25), seed=123456)
        >>> mab2 = MAB(arms, LearningPolicy.EpsilonGreedy(epsilon=0.30), seed=123456)
        >>> bandits = [('EG 25%', mab1), ('EG 30%', mab2)]
        >>> offline_sim = Simulator(bandits, decisions, rewards, test_size=0.5, batch_size=0)
        >>> offline_sim.run()
        >>> offline_sim.bandit_to_arm_to_stats_avg['EG 30%']['Arm1']
        {'count': 1, 'sum': 9, 'min': 9, 'max': 9, 'mean': 9.0, 'std': 0.0}

    """

    def __init__(
        self,
        bandits: List[tuple],
        decisions: Union[List[str], np.ndarray, pd.Series],
        rewards: Union[List[float], np.ndarray, pd.Series],
        config: SimulatorConfig,
        scaler: _T,
        contexts: Optional[
            Union[List[List[float]], np.ndarray, pd.Series, pd.DataFrame]
        ] = None,
    ):
        """Simulator

        Creates a simulator object with a collection of bandits, the history of decisions, rewards, and contexts, and
        the parameters for the simulation.

        Parameters
        ----------
        bandits: list[tuple(str, MAB)]
            The set of bandits to run the simulation with. Must be a list of tuples of an identifier for the bandit and
            the bandit object, of type mabwiser.mab.MAB or that inherits from mabwiser.base_mab.BaseMAB
        decisions : Union[List[Arm], np.ndarray, pd.Series]
            The decisions that are made.
        rewards : Union[List[Num], np.ndarray, pd.Series]
            The rewards that are received corresponding to the decisions.
        contexts : Union[None, List[List[Num]], np.ndarray, pd.Series, pd.DataFrame]
            The context under which each decision is made. Default value is None.
        config: SimulatorConfig
            Spock configuration object for the Simulator class

        Raises
        ------
        TypeError   The bandit objects must be given in a list.
        TypeError   Each bandit object must be identified by a string label.
        TypeError   Each bandit must be of type MAB or inherit from BaseMAB.
        TypeError   The decisions must be given in a list, numpy array, or pandas Series.
        TypeError   The rewards must be given in a list, numpy array, or pandas series.
        TypeError   The contexts must be given in a 2D list, numpy array, pandas dataframe or pandas series.
        TypeError   The test_size size must be a float.
        TypeError   The batch size must be an integer.
        TypeError   The is_ordered flag must be a boolean.
        TypeError   The evaluation function must be callable.
        ValueError  The length of decisions and rewards must match.
        ValueError  The test_size size must be greater than 0 and less than 1.
        ValueError  The batch size cannot exceed the size of the test set.
        """
        # Set the config first
        self.config = config

        self._validate_args(
            bandits=bandits,
            decisions=decisions,
            rewards=rewards,
            contexts=contexts,
            batch_size=self.config.batch_size,
            test_size=self.config.test_size
        )

        # Convert decisions, rewards and contexts to numpy arrays
        decisions = convert_array(decisions)
        rewards = convert_array(rewards)
        contexts = convert_matrix(contexts) if contexts is not None else contexts

        # Save the simulation parameters
        self.bandits = bandits
        self.decisions = decisions
        self.rewards = rewards
        self.contexts = contexts

        self.scaler = scaler

        self._online = self.config.batch_size > 0
        self._chunk_size = 100

        # logger object
        self.logger = logging.getLogger()
        self.logger.setLevel(logging.DEBUG)

        # create console handler and set level to info
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        formatter = logging.Formatter(self.config.log_format)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)

        # create error file handler and set level to debug
        if self.config.log_file is not None:
            handler = logging.FileHandler(
                self.config.log_file, "w", encoding=None, delay=True
            )
            handler.setLevel(logging.DEBUG)
            formatter = logging.Formatter(self.config.log_format)
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

        # set arms
        iter_name, iter_mab = self.bandits[0]
        self.arms = iter_mab.arms

        # Get the number of effective jobs for each bandit
        n_jobs_list = [
            effective_jobs(math.ceil((len(decisions) * self.config.test_size)), mab.config.n_jobs)
            for mab_name, mab in self.bandits
        ]
        # set max n_jobs
        self.max_n_jobs = max(n_jobs_list)

        # Initialize statistic objects
        self.arm_to_stats_total = {}
        self.arm_to_stats_train = {}
        self.arm_to_stats_test = {}
        self.bandit_to_arm_to_stats_min = {}
        self.bandit_to_arm_to_stats_avg = {}
        self.bandit_to_arm_to_stats_max = {}
        self.bandit_to_confusion_matrices = {}

        # Test row metrics
        self.bandit_to_predictions = {}
        self.bandit_to_expectations = {}
        self.bandit_to_neighborhood_size = {}
        self.bandit_to_arm_to_stats_neighborhoods = {}
        self.test_indices = []

        # Log parameters
        self.logger.info("Simulation Parameters")
        self.logger.info("\t bandits: " + str(self.bandits))
        self.logger.info("\t scaler: " + str(self.scaler))
        self.logger.info("\t test_size: " + str(self.config.test_size))
        self.logger.info("\t is_ordered: " + str(self.config.is_ordered))
        self.logger.info("\t batch_size: " + str(self.config.batch_size))
        self.logger.info("\t evaluator: " + str(self.config.evaluator))
        self.logger.info("\t seed: " + str(self.config.seed))
        self.logger.info("\t is_quick: " + str(self.config.is_quick))
        self.logger.info("\t log_file: " + str(self.config.log_file))
        self.logger.info("\t format: " + self.config.log_format)

    # Public Methods
    def get_arm_stats(self, decisions: np.ndarray, rewards: np.ndarray) -> Dict:
        """
        Calculates descriptive statistics for each arm in the provided data set.

        Parameters
        ----------
        decisions: np.ndarray
            The decisions to filter the rewards.
        rewards: np.ndarray
            The rewards to get statistics about.

        Returns
        -------
        Arm_to_stats dictionary.
        Dictionary has the format {arm {'count', 'sum', 'min', 'max', 'mean', 'std'}}
        """
        stats = dict((arm, {}) for arm in self.arms)
        for arm in self.arms:
            indices = np.where(decisions == arm)
            if indices[0].shape[0] > 0:
                arm_rewards = rewards[indices]
                stats[arm] = self.get_stats(arm_rewards)
            else:
                stats[arm] = {
                    "count": 0,
                    "sum": 0,
                    "min": 0,
                    "max": 0,
                    "mean": 0,
                    "std": 0,
                }
                self.logger.info("No historic data for " + str(arm))
        return stats

    def plot(self, metric: str = "avg", is_per_arm: bool = False) -> None:
        """
        Generates a plot of the cumulative sum of the rewards for each bandit.
        Simulation must be run before calling this method.

        Arguments
        ---------
        metric: str
            The bandit_to_arm_to_stats to use to generate the plot. Must be 'avg', 'min', or 'max
        is_per_arm: bool
            Whether to plot each arm separately or use an aggregate statistic.

        Raises
        ------
        AssertionError  Descriptive statics for predictions are missing.
        TypeError       Metric must be a string.
        TypeError       The per_arm flag must be a boolean.
        ValueError      The metric must be one of avg, min or max.

        Returns
        -------
        None
        """
        # Validate args
        check_true(isinstance(metric, str), TypeError("Metric must be a string."))
        check_true(
            metric in ["avg", "min", "max"],
            ValueError("Metric must be one of avg, min or max."),
        )
        check_true(
            isinstance(is_per_arm, bool), TypeError("is_per_arm must be True or False.")
        )

        # Validate that simulation has been run
        complete = "Complete simulation must be run before calling this method."
        check_true(
            bool(self.bandit_to_arm_to_stats_min),
            AssertionError(
                "Descriptive statistics for predictions missing. " + complete
            ),
        )

        if metric == "avg":
            stats = self.bandit_to_arm_to_stats_avg
        elif metric == "min":
            stats = self.bandit_to_arm_to_stats_min
        else:
            stats = self.bandit_to_arm_to_stats_max

        if self.config.batch_size > 0:
            cu_sums = {}
            labels = {}
            mabs = []

            if is_per_arm:
                for mab_name, mab in self.bandits:
                    self.logger.info("Plotting " + str(mab_name))
                    for arm in self.arms:
                        mab_arm_name = str(mab_name) + "_" + str(arm)
                        mabs.append(mab_arm_name)
                        labels[mab_arm_name] = []
                        sums = []
                        cu_sums[mab_arm_name] = []
                        for key in stats[mab_name].keys():
                            if key != "total":
                                labels[mab_arm_name].append(key)
                                if np.isnan(stats[mab_name][key][arm]["sum"]):
                                    sums.append(0)
                                else:
                                    sums.append(stats[mab_name][key][arm]["sum"])
                        cs = 0
                        for item in sums:
                            cs += item
                            cu_sums[mab_arm_name].append(cs)
            else:
                for mab_name, mab in self.bandits:
                    self.logger.info("Plotting " + str(mab_name))

                    mabs.append(mab_name)
                    labels[mab_name] = []
                    sums = []
                    cu_sums[mab_name] = []

                    for key in stats[mab_name].keys():
                        if key != "total":

                            labels[mab_name].append(key)

                            net = 0
                            for arm in self.arms:
                                if np.isnan(stats[mab_name][key][arm]["sum"]):
                                    continue

                                net += stats[mab_name][key][arm]["sum"]
                            sums.append(net)
                    cs = 0

                    for item in sums:
                        cs += item
                        cu_sums[mab_name].append(cs)

            x = [i * self.config.batch_size for i in labels[mabs[0]]]
            for mab in mabs:
                sns.lineplot(x=x, y=cu_sums[mab], label=mab)
            plt.xlabel("Test Rows Predicted")
            plt.ylabel("Cumulative Reward")
            plt.show()

        else:
            x_labels = []
            y_values = []

            if is_per_arm:
                for mab_name, mab in self.bandits:
                    for arm in self.arms:
                        x_labels.append(str(mab_name) + "_" + str(arm))
                        if not np.isnan(stats[mab_name][arm]["sum"]):
                            y_values.append(stats[mab_name][arm]["sum"])
                        else:
                            y_values.append(0)

            else:
                for mab_name, mab in self.bandits:
                    x_labels.append(mab_name)
                    cumulative = 0
                    for arm in self.arms:
                        if not np.isnan(stats[mab_name][arm]["sum"]):
                            cumulative += stats[mab_name][arm]["sum"]
                    y_values.append(cumulative)

            plt.bar(x_labels, y_values)
            plt.xlabel("Bandit")
            plt.ylabel("Cumulative Reward")
            plt.xticks(rotation=45)
            plt.show()

        plt.close("all")

    def run(self) -> None:
        """Run simulator

        Runs a simulation concurrently for all bandits in the bandits list.

        Returns
        -------
        None
        """

        #####################################
        # Total Stats
        #####################################
        self.logger.info("\n")
        self._set_stats("total", self.decisions, self.rewards)

        #####################################
        # Train-Test Split
        #####################################
        self.logger.info("\n")
        self.logger.info("Train/Test Split")
        (
            train_decisions,
            train_rewards,
            train_contexts,
            test_decisions,
            test_rewards,
            test_contexts,
        ) = self._run_train_test_split()

        self.logger.info("Train size: " + str(len(train_decisions)))
        self.logger.info("Test size: " + str(len(test_decisions)))

        #####################################
        # Scale the Data
        #####################################
        if self.scaler is not None:
            self.logger.info("\n")
            train_contexts, test_contexts = self._run_scaler(
                train_contexts, test_contexts
            )

        #####################################
        # Train/Test Stats
        #####################################
        self.logger.info("\n")
        self._set_stats("train", train_decisions, train_rewards)

        self.logger.info("\n")
        self._set_stats("test", test_decisions, test_rewards)

        #####################################
        # Fit the Training Data
        #####################################
        self.logger.info("\n")
        self._train_bandits(train_decisions, train_rewards, train_contexts)

        #####################################
        # Test the bandit simulation
        #####################################
        self.logger.info("\n")
        self.logger.info("Testing Bandits")
        if self._online:
            self._online_test_bandits(test_decisions, test_rewards, test_contexts)

        # If not running an _online simulation, evaluate the entire test set
        else:
            self._offline_test_bandits(test_decisions, test_rewards, test_contexts)

        self.logger.info("Simulation complete")

    # Private Methods
    def _get_partial_evaluation(
        self, name, i, decisions, predictions, rewards, start_index, nn=False
    ):
        cfm = confusion_matrix(decisions, predictions)
        self.bandit_to_confusion_matrices[name].append(cfm)
        self.logger.info(
            str(name) + " batch " + str(i) + " confusion matrix: " + str(cfm)
        )
        if nn and not self.config.is_quick:
            self.bandit_to_arm_to_stats_min[name][i] = self.config.evaluator(
                self.arms,
                decisions,
                rewards,
                predictions,
                (
                    self.arm_to_stats_train,
                    self.bandit_to_arm_to_stats_neighborhoods[name],
                ),
                "min",
                start_index,
                nn,
            )

            self.bandit_to_arm_to_stats_avg[name][i] = self.config.evaluator(
                self.arms,
                decisions,
                rewards,
                predictions,
                (
                    self.arm_to_stats_train,
                    self.bandit_to_arm_to_stats_neighborhoods[name],
                ),
                "mean",
                start_index,
                nn,
            )

            self.bandit_to_arm_to_stats_max[name][i] = self.config.evaluator(
                self.arms,
                decisions,
                rewards,
                predictions,
                (
                    self.arm_to_stats_train,
                    self.bandit_to_arm_to_stats_neighborhoods[name],
                ),
                "max",
                start_index,
                nn,
            )
        else:
            self.bandit_to_arm_to_stats_min[name][i] = self.config.evaluator(
                self.arms,
                decisions,
                rewards,
                predictions,
                self.arm_to_stats_train,
                "min",
                start_index,
                False,
            )

            self.bandit_to_arm_to_stats_avg[name][i] = self.config.evaluator(
                self.arms,
                decisions,
                rewards,
                predictions,
                self.arm_to_stats_train,
                "mean",
                start_index,
                False,
            )

            self.bandit_to_arm_to_stats_max[name][i] = self.config.evaluator(
                self.arms,
                decisions,
                rewards,
                predictions,
                self.arm_to_stats_train,
                "max",
                start_index,
                False,
            )
        self.logger.info(name + " " + str(self.bandit_to_arm_to_stats_min[name][i]))
        self.logger.info(name + " " + str(self.bandit_to_arm_to_stats_avg[name][i]))
        self.logger.info(name + " " + str(self.bandit_to_arm_to_stats_max[name][i]))

    def _offline_test_bandits(self, test_decisions, test_rewards, test_contexts):
        """
        Performs offline prediction.

        Arguments
        ---------
        test_decisions: np.ndarray
            The test set decisions.
        test_rewards: np.ndarray
            The test set rewards.
        test_contexts: np.ndarray
            The test set contexts.
        """

        chunk_start_index = [
            idx for idx in range(int(math.ceil(len(test_decisions) / self._chunk_size)))
        ]
        for idx in chunk_start_index:

            # Set distances to None for new chunk
            distances = None

            # Progress update
            self.logger.info(
                "Chunk " + str(idx + 1) + " out of " + str(len(chunk_start_index))
            )

            start = idx * self._chunk_size
            stop = min((idx + 1) * self._chunk_size, len(test_decisions))
            chunk_decision = test_decisions[start:stop]
            chunk_contexts = (
                test_contexts[start:stop] if test_contexts is not None else None
            )

            for name, mab in self.bandits:

                if mab.is_contextual:
                    if isinstance(mab, (_RadiusSimulator, _KNearestSimulator)):
                        if distances is None:
                            distances = mab.calculate_distances(chunk_contexts)
                        else:
                            mab.set_distances(distances)
                        predictions = mab.predict(chunk_contexts)
                        expectations = mab.row_arm_to_expectation[start:stop].copy()

                    else:
                        predictions = mab.predict(chunk_contexts)
                        if isinstance(mab, _LSHSimulator):
                            expectations = mab.row_arm_to_expectation[start:stop].copy()
                        elif isinstance(mab._imp, _Neighbors):
                            expectations = mab._imp.arm_to_expectation.copy()
                        else:
                            expectations = mab.predict_expectations(chunk_contexts)

                    if not isinstance(expectations, list):
                        expectations = [expectations]
                    self.bandit_to_expectations[name] = (
                        self.bandit_to_expectations[name] + expectations
                    )

                else:
                    predictions = [mab.predict() for _ in range(len(chunk_decision))]

                if not isinstance(predictions, list):
                    predictions = [predictions]

                self.bandit_to_predictions[name] = (
                    self.bandit_to_predictions[name] + predictions
                )

                if isinstance(mab, _NeighborsSimulator) and not self.config.is_quick:
                    self.bandit_to_arm_to_stats_neighborhoods[
                        name
                    ] = mab.neighborhood_arm_to_stat.copy()

        for name, mab in self.bandits:
            nn = isinstance(mab, _NeighborsSimulator)

            if not mab.is_contextual:
                self.bandit_to_expectations[name] = mab._imp.arm_to_expectation.copy()
            if isinstance(mab, _NeighborsSimulator) and not self.config.is_quick:
                self.bandit_to_neighborhood_size[name] = mab.neighborhood_sizes.copy()

            # Evaluate the predictions
            self.bandit_to_confusion_matrices[name].append(
                confusion_matrix(test_decisions, self.bandit_to_predictions[name])
            )

            self.logger.info(
                name
                + " confusion matrix: "
                + str(self.bandit_to_confusion_matrices[name])
            )

            if nn and not self.config.is_quick:
                self.bandit_to_arm_to_stats_min[name] = self.config.evaluator(
                    self.arms,
                    test_decisions,
                    test_rewards,
                    self.bandit_to_predictions[name],
                    (
                        self.arm_to_stats_train,
                        self.bandit_to_arm_to_stats_neighborhoods[name],
                    ),
                    stat="min",
                    start_index=0,
                    nn=nn,
                )

                self.bandit_to_arm_to_stats_avg[name] = self.config.evaluator(
                    self.arms,
                    test_decisions,
                    test_rewards,
                    self.bandit_to_predictions[name],
                    (
                        self.arm_to_stats_train,
                        self.bandit_to_arm_to_stats_neighborhoods[name],
                    ),
                    stat="mean",
                    start_index=0,
                    nn=nn,
                )

                self.bandit_to_arm_to_stats_max[name] = self.config.evaluator(
                    self.arms,
                    test_decisions,
                    test_rewards,
                    self.bandit_to_predictions[name],
                    (
                        self.arm_to_stats_train,
                        self.bandit_to_arm_to_stats_neighborhoods[name],
                    ),
                    stat="max",
                    start_index=0,
                    nn=nn,
                )
            else:
                self.bandit_to_arm_to_stats_min[name] = self.config.evaluator(
                    self.arms,
                    test_decisions,
                    test_rewards,
                    self.bandit_to_predictions[name],
                    self.arm_to_stats_train,
                    stat="min",
                    start_index=0,
                    nn=False,
                )

                self.bandit_to_arm_to_stats_avg[name] = self.config.evaluator(
                    self.arms,
                    test_decisions,
                    test_rewards,
                    self.bandit_to_predictions[name],
                    self.arm_to_stats_train,
                    stat="mean",
                    start_index=0,
                    nn=False,
                )

                self.bandit_to_arm_to_stats_max[name] = self.config.evaluator(
                    self.arms,
                    test_decisions,
                    test_rewards,
                    self.bandit_to_predictions[name],
                    self.arm_to_stats_train,
                    stat="max",
                    start_index=0,
                    nn=False,
                )

            self.logger.info(
                name + " minimum analysis " + str(self.bandit_to_arm_to_stats_min[name])
            )
            self.logger.info(
                name + " average analysis " + str(self.bandit_to_arm_to_stats_avg[name])
            )
            self.logger.info(
                name + " maximum analysis " + str(self.bandit_to_arm_to_stats_max[name])
            )

    def _online_test_bandits(self, test_decisions, test_rewards, test_contexts):
        """
        Performs _online prediction and partial fitting for each model.

        Arguments
        ---------
        test_decisions: np.ndarray
            The test set decisions.
        test_rewards: np.ndarray
            The test set rewards.
        test_contexts: np.ndarray
            The test set contexts.
        """

        # Divide the test data into batches and chunk the batches based on size
        self._online_test_bandits_chunks(test_decisions, test_rewards, test_contexts)

        # Final scores for all predictions
        for name, mab in self.bandits:
            nn = isinstance(mab, _NeighborsSimulator)

            self._get_partial_evaluation(
                name,
                "total",
                test_decisions,
                self.bandit_to_predictions[name],
                test_rewards,
                0,
                nn,
            )

            if isinstance(mab, _NeighborsSimulator) and not self.config.is_quick:
                self.bandit_to_neighborhood_size[name] = mab.neighborhood_sizes.copy()
                self.bandit_to_arm_to_stats_neighborhoods[
                    name
                ] = mab.neighborhood_arm_to_stat.copy()

    def _online_test_bandits_chunks(self, test_decisions, test_rewards, test_contexts):
        """
        Performs _online prediction and partial fitting for each model.

        Arguments
        ---------
        test_decisions: np.ndarray
            The test set decisions.
        test_rewards: np.ndarray
            The test set rewards.
        test_contexts: np.ndarray
            The test set contexts.
        """

        # Divide the test data into batches
        start = 0
        for i in range(0, int(math.ceil(len(test_decisions) / self.config.batch_size))):
            self.logger.info("Starting batch " + str(i))

            # Stop at the next batch_size interval or the end of the test data
            stop = min(start + self.config.batch_size, len(test_decisions) + 1)

            batch_contexts = (
                test_contexts[start:stop] if test_contexts is not None else None
            )
            batch_decisions = test_decisions[start:stop]
            batch_rewards = test_rewards[start:stop]
            batch_predictions = {}
            batch_expectations = {}

            chunk_start = 0

            # Divide the batch into chunks
            for j in range(0, int(math.ceil(self.config.batch_size / self._chunk_size))):
                distances = None
                chunk_stop = min(chunk_start + self._chunk_size, self.config.batch_size)
                chunk_contexts = (
                    batch_contexts[chunk_start:chunk_stop]
                    if batch_contexts is not None
                    else None
                )
                chunk_decisions = batch_decisions[chunk_start:chunk_stop]

                for name, mab in self.bandits:

                    if name not in batch_predictions.keys():
                        batch_predictions[name] = []
                        batch_expectations[name] = []

                    # Predict for the batch
                    if mab.is_contextual:
                        if isinstance(mab, (_RadiusSimulator, _KNearestSimulator)):
                            if distances is None:
                                distances = mab.calculate_distances(chunk_contexts)
                                self.logger.info("Distances calculated")
                            else:
                                mab.set_distances(distances)
                                self.logger.info("Distances set")
                            predictions = mab.predict(chunk_contexts)
                            expectations = mab.row_arm_to_expectation[
                                start + chunk_start : start + chunk_stop
                            ].copy()
                        else:
                            predictions = mab.predict(chunk_contexts)
                            if isinstance(mab, _LSHSimulator):
                                expectations = mab.row_arm_to_expectation[
                                    start:stop
                                ].copy()
                            else:
                                expectations = mab.predict_expectations(chunk_contexts)

                        if self.config.batch_size == 1:
                            predictions = [predictions]

                    else:
                        predictions = [
                            mab.predict() for _ in range(len(chunk_decisions))
                        ]
                        expectations = mab._imp.arm_to_expectation.copy()

                    # If a single prediction was returned, put it into a list
                    if not isinstance(predictions, list):
                        predictions = [predictions]
                    if not isinstance(expectations, list):
                        expectations = [expectations]

                    batch_predictions[name] = batch_predictions[name] + predictions
                    batch_expectations[name] = batch_expectations[name] + expectations

            for name, mab in self.bandits:
                if not mab.is_contextual:
                    batch_expectations[name] = [mab._imp.arm_to_expectation.copy()]

                nn = isinstance(mab, _NeighborsSimulator)

                # Add predictions from this batch
                self.bandit_to_predictions[name] = (
                    self.bandit_to_predictions[name] + batch_predictions[name]
                )
                self.bandit_to_expectations[name] = (
                    self.bandit_to_expectations[name] + batch_expectations[name]
                )

                if (
                    isinstance(mab, (_RadiusSimulator, _LSHSimulator))
                    and not self.config.is_quick
                ):
                    self.bandit_to_neighborhood_size[
                        name
                    ] = mab.neighborhood_sizes.copy()
                if isinstance(mab, _NeighborsSimulator) and not self.config.is_quick:
                    self.bandit_to_arm_to_stats_neighborhoods[
                        name
                    ] = mab.neighborhood_arm_to_stat.copy()

                # Evaluate the predictions
                self._get_partial_evaluation(
                    name,
                    i,
                    batch_decisions,
                    batch_predictions[name],
                    batch_rewards,
                    start,
                    nn,
                )

                # Update the model
                if mab.is_contextual:
                    mab.partial_fit(batch_decisions, batch_rewards, batch_contexts)
                else:
                    mab.partial_fit(batch_decisions, batch_rewards)
                self.logger.info(name + " updated")

            # Update start value for next batch
            start += self.config.batch_size

    def _run_scaler(self, train_contexts, test_contexts):
        """
        Scales the train and test contexts with the scaler provided to the simulator constructor.

        Arguments
        ---------
        train_contexts: np.ndarray
            The training set contexts.
        test_contexts: np.ndarray
            The test set contexts.

        Returns
        -------
            The scaled train_contexts and test_contexts
        """

        self.logger.info("Train/Test Scale")

        train_contexts = self.scaler.fit_transform(train_contexts)
        test_contexts = self.scaler.transform(test_contexts)
        return train_contexts, test_contexts

    def _run_train_test_split(self):
        """
        Performs a train-test split with the test set containing a percentage of the data determined by test_size.

        If is_ordered is true, performs a chronological split.
        Otherwise uses sklearn's train_test_split

        Returns
        -------
            The train and test decisions, rewards and contexts
        """

        if self.config.is_ordered:
            train_size = int(len(self.decisions) * (1 - self.config.test_size))
            train_decisions = self.decisions[:train_size]
            train_rewards = self.rewards[:train_size]
            train_contexts = (
                self.contexts[:train_size] if self.contexts is not None else None
            )
            # The test arrays are re-indexed to 0 automatically
            test_decisions = self.decisions[train_size:]
            test_rewards = self.rewards[train_size:]
            test_contexts = (
                self.contexts[train_size:] if self.contexts is not None else None
            )
            self.test_indices = [x for x in range(train_size, len(self.decisions))]

        else:
            indices = [x for x in range(len(self.decisions))]
            if self.contexts is None:

                train_contexts, test_contexts = None, None

                (
                    train_indices,
                    test_indices,
                    train_decisions,
                    test_decisions,
                    train_rewards,
                    test_rewards,
                ) = train_test_split(
                    indices,
                    self.decisions,
                    self.rewards,
                    test_size=self.config.test_size,
                    random_state=self.config.seed,
                )
            else:

                (
                    train_indices,
                    test_indices,
                    train_decisions,
                    test_decisions,
                    train_rewards,
                    test_rewards,
                    train_contexts,
                    test_contexts,
                ) = train_test_split(
                    indices,
                    self.decisions,
                    self.rewards,
                    self.contexts,
                    test_size=self.config.test_size,
                    random_state=self.config.seed,
                )
            self.test_indices = test_indices

        # Use memory limits for the nearest neighbors shared distance list to determine chunk size.
        # The list without chunking contains len(test_decisions) elements
        # each of which is an np.ndarray with len(train_decisions) distances.
        # Approximate as 8 bytes per element in each numpy array to give the size of the list in GB.
        distance_list_size = len(test_decisions) * (8 * len(train_decisions)) / 1e9

        # If there is more than one test row and contexts have been provided:
        if distance_list_size > 1.0 and train_contexts is not None:

            # Set the chunk size to contain 1GB per job
            gb_chunk_size = (
                int(len(test_decisions) / distance_list_size) * self.max_n_jobs
            )

            # If the length of the test set is less than the chunk size, chunking is unnecessary
            self._chunk_size = min(gb_chunk_size, len(test_decisions))

        # If there is only one test row or all MABs are context-free chunking is unnecessary:
        else:
            self._chunk_size = len(test_decisions)

        return (
            train_decisions,
            train_rewards,
            train_contexts,
            test_decisions,
            test_rewards,
            test_contexts,
        )

    def _set_stats(self, scope, decisions, rewards):
        """
        Calculates descriptive statistics for each arm for the specified data set
        and stores them to the corresponding arm_to_stats dictionary.

        Arguments
        ---------
        scope: str
            The label for which set is being evaluated.
            Accepted values: 'total', 'train', 'test'
        decisions: np.ndarray
            The decisions to filter the rewards.
        rewards: np.ndarray
            The rewards to get statistics about.

        Returns
        -------
        None
        """

        if scope == "total":
            self.arm_to_stats_total = self.get_arm_stats(decisions, rewards)
            self.logger.info("Total Stats")
            self.logger.info(self.arm_to_stats_total)
        elif scope == "train":
            self.arm_to_stats_train = self.get_arm_stats(decisions, rewards)
            self.logger.info("Train Stats")
            self.logger.info(self.arm_to_stats_train)
        elif scope == "test":
            self.arm_to_stats_test = self.get_arm_stats(decisions, rewards)
            self.logger.info("Test Stats")
            self.logger.info(self.arm_to_stats_test)
        else:
            raise ValueError("Unsupported scope name")

    def _train_bandits(self, train_decisions, train_rewards, train_contexts=None):
        """
        Trains each of the bandit models.

        Arguments
        ---------
        train_decisions: np.ndarray
            The training set decisions.
        train_rewards: np.ndarray
            The training set rewards.
        train_contexts: np.ndarray
            The training set contexts.
        """

        self.logger.info("Training Bandits")

        new_bandits = []
        for name, mab in self.bandits:
            # Add the current bandit
            self.bandit_to_predictions[name] = []
            self.bandit_to_expectations[name] = []
            self.bandit_to_neighborhood_size[name] = []
            self.bandit_to_arm_to_stats_neighborhoods[name] = []
            self.bandit_to_confusion_matrices[name] = []
            self.bandit_to_arm_to_stats_min[name] = {}
            self.bandit_to_arm_to_stats_avg[name] = {}
            self.bandit_to_arm_to_stats_max[name] = {}

            if isinstance(mab, MAB):
                imp = mab._imp
            else:
                imp = mab
            if isinstance(imp, _Radius):
                mab = _RadiusSimulator(
                    rng=imp.rng,
                    arms=imp.arms,
                    n_jobs=imp.n_jobs,
                    backend=imp.backend,
                    lp=imp.lp,
                    radius=imp.radius,
                    metric=imp.metric,
                    is_quick=self.config.is_quick,
                    no_nhood_prob_of_arm=imp.no_nhood_prob_of_arm
                )

            elif isinstance(imp, _KNearest):
                mab = _KNearestSimulator(
                    rng=imp.rng,
                    arms=imp.arms,
                    n_jobs=imp.n_jobs,
                    backend=imp.backend,
                    lp=imp.lp,
                    k=imp.k,
                    metric=imp.metric,
                    is_quick=self.config.is_quick
                )
            elif isinstance(imp, _LSHNearest):
                mab = _LSHSimulator(
                    rng=imp.rng,
                    arms=imp.arms,
                    n_jobs=imp.n_jobs,
                    backend=imp.backend,
                    lp=imp.lp,
                    n_dimensions=imp.n_dimensions,
                    n_tables=imp.n_tables,
                    is_quick=self.config.is_quick,
                    no_nhood_prob_of_arm=imp.no_nhood_prob_of_arm,
                )

            new_bandits.append((name, mab))
            if mab.is_contextual:
                mab.fit(train_decisions, train_rewards, train_contexts)
            else:
                mab.fit(train_decisions, train_rewards)
            self.logger.info(name + " trained")

        self.bandits = new_bandits

    # Static Methods
    @staticmethod
    def get_stats(rewards: np.ndarray) -> dict:
        """Calculates descriptive statistics for the given array of rewards.

        Parameters
        ----------
        rewards: nd.nparray
            Array of rewards for a single arm.

        Returns
        -------
        A dictionary of descriptive statistics.
        Dictionary has the format {'count', 'sum', 'min', 'max', 'mean', 'std'}
        """
        return {
            "count": rewards.size,
            "sum": rewards.sum(),
            "min": rewards.min(),
            "max": rewards.max(),
            "mean": rewards.mean(),
            "std": rewards.std(),
        }

    @staticmethod
    def _validate_args(
        bandits,
        decisions,
        rewards,
        contexts,
        test_size,
        batch_size,
    ):
        """
        Validates the simulation parameters.
        """
        check_true(
            isinstance(bandits, list), TypeError("Bandits must be provided in a list.")
        )
        for pair in bandits:
            name, mab = pair
            check_true(
                isinstance(name, str),
                TypeError("All bandits must be identified by strings."),
            )
            check_true(
                isinstance(mab, (MAB, BaseMAB)),
                TypeError("All bandits must be MAB objects or inherit from BaseMab."),
            )

        # Type check for decisions
        check_fit_input((decisions, rewards))
        # Type check for contexts --don't use "if contexts" since it's n-dim array
        if contexts is not None:
            validate_2d(contexts, "contexts")
            # Make sure lengths of decisions and contexts match
            check_len(decisions, contexts, "decisions", "contexts")
        # Length check for decisions and rewards
        check_len(decisions, rewards, "decisions", "rewards")
        # Verify the batch size is less than the total test set
        if batch_size > 0:
            check_true(
                batch_size <= (math.ceil(len(decisions) * test_size)),
                ValueError("Batch size cannot be larger than " "the test set."),
            )
