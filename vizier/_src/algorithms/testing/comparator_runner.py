# Copyright 2022 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Comparison test for algorithms using score analysis.

Ex: Typical Efficiency Convergence Test Example
-----------------------------------------------
baseline_factory = benchmarks.BenchmarkStateFactory(...)
candidate_factory = benchmarks.BenchmarkStateFactory(...)

# Run each algorithm for 100 Trials with 5 repeats each.
comparator = comparator_runner.EfficiencyComparisonTester(
        num_trials=100, num_repeats=5)
comparator.assert_better_efficiency(candidate_factory, baseline_factory)

NOTE: assert_converges_faster is a generic method name that conveys the general
use of the class.
"""

import logging

import attr
import numpy as np
from vizier import benchmarks
from vizier._src.algorithms.optimizers import vectorized_base as vb
from vizier._src.benchmarks.analyzers import simple_regret_score
from vizier.pyvizier import converters


class FailedComparisonTestError(Exception):
  """Exception raised for comparison test fails."""


class FailedSimpleRegretConvergenceTestError(Exception):
  """Exception raised for simple-regret convergence test fails."""


@attr.define
class EfficiencyComparisonTester:
  """Comparison test between algorithms using analysis scores."""
  num_trials: int = attr.field(
      default=1, validator=attr.validators.instance_of(int))
  num_repeats: int = attr.field(
      default=1, validator=attr.validators.instance_of(int))

  def assert_better_efficiency(
      self,
      candidate_state_factory: benchmarks.BenchmarkStateFactory,
      baseline_state_factory: benchmarks.BenchmarkStateFactory,
      score_threshold: float = 0.0) -> None:
    """Asserts that candidate is better than baseline via log_eff_score."""
    # TODO: Consider making this more flexible with more runners
    # And enable multimetric.
    runner = benchmarks.BenchmarkRunner(
        benchmark_subroutines=[benchmarks.GenerateAndEvaluate()],
        num_repeats=self.num_trials)

    baseline_curves = []
    candidate_curves = []
    for _ in range(self.num_repeats):
      baseline_state = baseline_state_factory()
      candidate_state = candidate_state_factory()

      baseline_statement = baseline_state.experimenter.problem_statement()
      if len(baseline_statement.metric_information) > 1:
        raise ValueError('Support for multimetric is not yet')
      if baseline_statement != (
          candidate_statement :=
          candidate_state.experimenter.problem_statement()):
        raise ValueError('Comparison tests done for different statements: '
                         f'{baseline_statement} vs {candidate_statement}')

      runner.run(baseline_state)
      runner.run(candidate_state)
      baseline_curves.append(
          benchmarks.ConvergenceCurveConverter(
              baseline_statement.metric_information.item()).convert(
                  baseline_state.algorithm.supporter.GetTrials()))
      candidate_curves.append(
          benchmarks.ConvergenceCurveConverter(
              baseline_statement.metric_information.item()).convert(
                  candidate_state.algorithm.supporter.GetTrials()))

    baseline_curve = benchmarks.ConvergenceCurve.align_xs(baseline_curves)
    candidate_curve = benchmarks.ConvergenceCurve.align_xs(candidate_curves)
    comparator = benchmarks.ConvergenceCurveComparator(baseline_curve)

    if (log_eff_score :=
        comparator.get_log_efficiency_score(candidate_curve)) < score_threshold:
      raise FailedComparisonTestError(
          f'Log efficiency score {log_eff_score} is less than {score_threshold}'
          f' when comparing algorithms: {candidate_state_factory} '
          f'vs baseline of {baseline_state_factory} for {self.num_trials} '
          f' Trials with {self.num_repeats} repeats')


@attr.define
class SimpleRegretComparisonTester:
  """Compare two algorithms by their simple regrets.

  The test runs the baseline algorithm 'baseline_num_repeats' times each with
  'baseline_num_trials' trials and computes the simple regret in each trial,
  and similarly for the candidate algorithm.

  A one-sided T-test is performed to compute the p-value of observing the
  difference in the simple regret sample means. The T-test score (p-value) is
  compared against the significance level (alpha) to determine if the test
  passed.

  The test assumes MAXIMIZATION optimization problem. For MINIMIZATION, invert
  the sign of the score function.
  """
  baseline_num_trials: int
  candidate_num_trials: int
  baseline_num_repeats: int
  candidate_num_repeats: int
  alpha: float = attr.field(
      validator=attr.validators.and_(
          attr.validators.ge(0), attr.validators.le(0.1)),
      default=0.05)

  def assert_optimizer_better_simple_regret(
      self,
      converter: converters.TrialToArrayConverter,
      score_fn: vb.BatchArrayScoreFunction,
      baseline_optimizer: vb.VectorizedOptimizer,
      candidate_optimizer: vb.VectorizedOptimizer,
  ) -> None:
    """Assert if candidate optimizer has better simple regret than the baseline.
    """
    baseline_simple_regrets = []
    candidate_simple_regrets = []

    for _ in range(self.baseline_num_repeats):
      trial = baseline_optimizer.optimize(
          converter,
          score_fn,
          count=1,
          max_evaluations=self.baseline_num_trials)
      simple_regret = trial[0].final_measurement.metrics['acquisition'].value
      baseline_simple_regrets.append(simple_regret)

    for _ in range(self.candidate_num_repeats):
      trial = candidate_optimizer.optimize(
          converter,
          score_fn,
          count=1,
          max_evaluations=self.candidate_num_trials)
      simple_regret = trial[0].final_measurement.metrics['acquisition'].value
      candidate_simple_regrets.append(simple_regret)

    p_value = simple_regret_score.t_test_less_mean_score(
        baseline_simple_regrets, candidate_simple_regrets)
    msg = self._generate_summary(baseline_simple_regrets,
                                 candidate_simple_regrets, p_value)

    if p_value <= self.alpha:
      logging.info('Convergence test PASSED:\n %s', msg)

    else:
      raise FailedSimpleRegretConvergenceTestError(msg)

  def assert_benchmark_state_better_simple_regret(
      self,
      baseline_benchmark_state_factory: benchmarks.BenchmarkStateFactory,
      candidate_benchmark_state_factory: benchmarks.BenchmarkStateFactory,
      *,
      baseline_batch_size: int = 1,
      candidate_batch_size: int = 1,
  ) -> None:
    """Runs simple-regret convergence test for benchmark state."""

    def _run_one(benchmark_state_factory: benchmarks.BenchmarkStateFactory,
                 num_trials: int, batch_size: int) -> float:
      """Run one benchmark run and returns simple regret."""
      benchmark_state = benchmark_state_factory()
      baseline_runner = benchmarks.BenchmarkRunner(
          benchmark_subroutines=[benchmarks.GenerateAndEvaluate(batch_size)],
          num_repeats=num_trials // batch_size)
      baseline_runner.run(benchmark_state)
      # Extract best metric
      best_trial = benchmark_state.algorithm.supporter.GetBestTrials(count=1)[0]
      metric_name = benchmark_state.experimenter.problem_statement(
      ).single_objective_metric_name
      return best_trial.final_measurement.metrics[metric_name].value

    baseline_simple_regrets = []
    candidate_simple_regrets = []

    for _ in range(self.baseline_num_repeats):
      baseline_simple_regrets.append(
          _run_one(baseline_benchmark_state_factory, self.baseline_num_trials,
                   baseline_batch_size))
    for _ in range(self.candidate_num_repeats):
      candidate_simple_regrets.append(
          _run_one(candidate_benchmark_state_factory, self.candidate_num_trials,
                   candidate_batch_size))

    p_value = simple_regret_score.t_test_less_mean_score(
        baseline_simple_regrets, candidate_simple_regrets)
    msg = self._generate_summary(baseline_simple_regrets,
                                 candidate_simple_regrets, p_value)
    if p_value <= self.alpha:
      logging.info('Convergence test PASSED:\n %s', msg)

    else:
      raise FailedSimpleRegretConvergenceTestError(msg)

  def _generate_summary(
      self,
      baseline_simple_regrets: list[float],
      candidate_simple_regrets: list[float],
      p_value: float,
  ) -> str:
    """Generate summary message."""
    baseline_mean = np.mean(baseline_simple_regrets)
    baseline_std = np.std(baseline_simple_regrets)
    candidate_mean = np.mean(candidate_simple_regrets)
    candidate_std = np.std(candidate_simple_regrets)
    return (f'P-value={p_value}. Alpha={self.alpha}.'
            f'\nBaseline Simple Regret Mean: {baseline_mean}.'
            f'\nBaseline Simple Regret Std: {baseline_std}.'
            f'\nCandidate Simple Regret Mean: {candidate_mean}.'
            f'\nCandidate Simple Regret Std: {candidate_std}.'
            f'\nBaseline Simple Regret Scores: {baseline_simple_regrets}'
            f'\nCandidate Simple Regret Scores: {candidate_simple_regrets}')
