"""Library functions for testing databases."""
import random
import string
from typing import List

from vizier.service import datastore
from vizier.service import key_value_pb2
from vizier.service import resources
from vizier.service import study_pb2
from vizier.service import vizier_oss_pb2
from vizier.service import vizier_service_pb2

from google.longrunning import operations_pb2
from absl.testing import parameterized

_KeyValuePlus = vizier_service_pb2.UpdateMetadataRequest.KeyValuePlus


def make_random_string() -> str:
  return ''.join(random.choice(string.ascii_lowercase) for _ in range(10))


class DataStoreTestCase(parameterized.TestCase):
  """Base class for testing datastores."""

  def assertStudyAPI(self, ds: datastore.DataStore, study: study_pb2.Study):
    """Tests if the datastore handles studies correctly."""
    ds.create_study(study)
    copied_study = ds.load_study(study.name)
    self.assertEqual(copied_study, study)
    self.assertIsNot(copied_study, study)  # Check pass-by-value.

    owner_name = resources.StudyResource.from_name(
        study.name).owner_resource.name
    list_of_one_study = ds.list_studies(owner_name)
    self.assertLen(list_of_one_study, 1)
    self.assertEqual(list_of_one_study[0], study)
    self.assertIsNot(list_of_one_study[0], study)  # Check pass-by-value.

    study.inactive_reason = make_random_string()
    copied_original_study = ds.load_study(study.name)
    self.assertNotEqual(study, copied_original_study)  # Check pass-by-value.

    ds.delete_study(study.name)
    empty_list = ds.list_studies(owner_name)
    self.assertEmpty(empty_list)

  def assertTrialAPI(self, ds: datastore.DataStore, study: study_pb2.Study,
                     trials: List[study_pb2.Trial]):
    """Tests if the datastore handles trials correctly."""
    ds.create_study(study)
    for trial in trials:
      ds.create_trial(trial)
      copied_trial = ds.get_trial(trial.name)
      self.assertEqual(trial, copied_trial)
      self.assertIsNot(trial, copied_trial)  # Check pass-by-value.

    self.assertLen(trials, ds.max_trial_id(study.name))

    list_of_trials = ds.list_trials(study.name)
    self.assertEqual(list_of_trials, trials)
    self.assertIsNot(list_of_trials, trials)  # Check pass-by-value.

    first_trial = trials[0]
    first_trial.infeasible_reason = make_random_string()
    ds.update_trial(first_trial)
    new_first_trial = ds.get_trial(first_trial.name)
    self.assertEqual(first_trial, new_first_trial)
    self.assertIsNot(first_trial, new_first_trial)  # Check pass-by-value.

    ds.delete_trial(first_trial.name)
    leftover_trials = ds.list_trials(study.name)
    self.assertEqual(leftover_trials, trials[1:])
    self.assertIsNot(leftover_trials, trials[1:])  # Check pass-by-value.

  def assertSuggestOpAPI(self, ds: datastore.DataStore, study: study_pb2.Study,
                         client_id: str,
                         suggestion_ops: List[operations_pb2.Operation]):
    """Tests if the datastore handles suggest ops correctly."""
    study_resource = resources.StudyResource.from_name(study.name)

    ds.create_study(study)
    for operation in suggestion_ops:
      ds.create_suggestion_operation(operation)

    self.assertLen(
        suggestion_ops,
        ds.max_suggestion_operation_number(
            resources.OwnerResource(study_resource.owner_id).name, client_id))

    list_of_operations = ds.list_suggestion_operations(
        resources.OwnerResource(study_resource.owner_id).name, client_id)
    self.assertEqual(list_of_operations, suggestion_ops)

    output_op = ds.get_suggestion_operation(
        resources.SuggestionOperationResource(
            study_resource.owner_id, client_id, operation_number=1).name)
    self.assertEqual(output_op, suggestion_ops[0])
    self.assertIsNot(output_op, suggestion_ops[0])  # Check pass-by-value.

    output_op.metadata.type_url = make_random_string()
    ds.update_suggestion_operation(output_op)
    new_output_op = ds.get_suggestion_operation(output_op.name)
    self.assertEqual(output_op, new_output_op)

  def assertEarlyStoppingAPI(
      self, ds: datastore.DataStore, study: study_pb2.Study,
      trials: List[study_pb2.Trial],
      early_stopping_ops: List[vizier_oss_pb2.EarlyStoppingOperation]):
    """Tests if the datastore handles early stopping ops correctly."""
    study_resource = resources.StudyResource.from_name(study.name)
    ds.create_study(study)

    for trial in trials:
      ds.create_trial(trial)

    for operation in early_stopping_ops:
      ds.create_early_stopping_operation(operation)

    output_op = ds.get_early_stopping_operation(
        resources.EarlyStoppingOperationResource(study_resource.owner_id,
                                                 study_resource.study_id,
                                                 1).name)
    self.assertEqual(output_op, early_stopping_ops[0])
    self.assertIsNot(output_op, early_stopping_ops[0])  # Check pass-by-value.

    output_op.failure_message = make_random_string()
    ds.update_early_stopping_operation(output_op)
    new_output_op = ds.get_early_stopping_operation(output_op.name)
    self.assertEqual(output_op, new_output_op)

  def assertUpdateMetadataAPI(self, ds: datastore.DataStore,
                              study: study_pb2.Study,
                              trials: List[study_pb2.Trial]):
    """Tests if the datastore handles metadata updates properly."""
    # TODO: Make this test more general.
    ds.create_study(study)
    for trial in trials:
      ds.create_trial(trial)
    study_metadata = [key_value_pb2.KeyValue(key='a', ns='b', value='C')]
    trial_metadata = [
        _KeyValuePlus(
            trial_id='1',
            k_v=key_value_pb2.KeyValue(key='d', ns='e', value='F'))
    ]
    ds.update_metadata(study.name, study_metadata, trial_metadata)
    mutated_study_config = ds.load_study(study.name).study_spec
    self.assertEqual(list(mutated_study_config.metadata), study_metadata)
    mutated_trial = ds.get_trial(trials[0].name)
    self.assertEqual(mutated_trial.id, str(trial_metadata[0].trial_id))
    self.assertEqual(list(mutated_trial.metadata), [trial_metadata[0].k_v])