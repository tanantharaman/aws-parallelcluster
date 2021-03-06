# Copyright 2019 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License").
# You may not use this file except in compliance with the License.
# A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file.
# This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, express or implied.
# See the License for the specific language governing permissions and limitations under the License.
from collections import namedtuple

import boto3
import pytest

from assertpy import assert_that
from remote_command_executor import RemoteCommandExecutor
from tests.common.scaling_common import get_max_asg_capacity, watch_compute_nodes
from tests.common.schedulers_common import SlurmCommands
from time_utils import minutes

PclusterConfig = namedtuple("PclusterConfig", ["max_queue_size", "compute_instance"])


@pytest.mark.regions(["us-west-1"])
@pytest.mark.schedulers(["slurm"])
@pytest.mark.oss(["alinux"])
@pytest.mark.usefixtures("region", "os", "scheduler")
def test_update(region, pcluster_config_reader, clusters_factory):
    """
    Test 'pcluster update' command.

    Grouped all tests in a single function so that cluster can be reused for all of them.
    """
    init_config = PclusterConfig(max_queue_size=5, compute_instance="c5.xlarge")
    cluster, factory = _init_cluster(region, clusters_factory, pcluster_config_reader, init_config)

    updated_config = PclusterConfig(max_queue_size=10, compute_instance="c4.xlarge")
    _update_cluster(cluster, factory, updated_config)

    # test update
    _test_max_queue(region, cluster.cfn_name, updated_config.max_queue_size)
    _test_update_compute_instance_type(cluster, updated_config.compute_instance)


def _init_cluster(region, clusters_factory, pcluster_config_reader, config):
    # read configuration and create cluster
    cluster_config = pcluster_config_reader(
        max_queue_size=config.max_queue_size, compute_instance=config.compute_instance
    )
    cluster, factory = clusters_factory(cluster_config)

    # Verify initial settings
    _test_max_queue(region, cluster.cfn_name, config.max_queue_size)
    _test_compute_instance_type(cluster.cfn_name, config.compute_instance)

    return cluster, factory


def _update_cluster(cluster, factory, config):
    # change config settings
    _update_cluster_property(cluster, "max_queue_size", str(config.max_queue_size))
    _update_cluster_property(cluster, "compute_instance_type", config.compute_instance)
    # update configuration file
    cluster.update()
    # update cluster
    factory.update_cluster(cluster)


def _update_cluster_property(cluster, property_name, property_value):
    cluster.config.set("cluster default", property_name, property_value)


def _test_max_queue(region, stack_name, queue_size):
    asg_max_size = get_max_asg_capacity(region, stack_name)
    assert_that(asg_max_size).is_equal_to(queue_size)


def _test_update_compute_instance_type(cluster, new_compute_instance):
    # submit a job to perform a scaling up action and have a new instance
    number_of_nodes = 2
    remote_command_executor = RemoteCommandExecutor(cluster)
    slurm_commands = SlurmCommands(remote_command_executor)
    result = slurm_commands.submit_command("sleep 60", nodes=number_of_nodes)
    slurm_commands.assert_job_submitted(result.stdout)

    estimated_scaleup_time = 5
    watch_compute_nodes(
        scheduler_commands=slurm_commands,
        max_monitoring_time=minutes(estimated_scaleup_time),
        number_of_nodes=number_of_nodes,
    )
    _test_compute_instance_type(cluster.cfn_name, new_compute_instance)


def _test_compute_instance_type(stack_name, compute_instance_type):
    ec2_client = boto3.resource("ec2")
    instance_ids = []
    instance_types = []
    for instance in ec2_client.instances.filter(Filters=[{"Name": "tag:Application", "Values": [stack_name]}]):
        instance_ids.append(instance.instance_id)
        instance_types.append(instance.instance_type)

    assert_that(instance_types).contains(compute_instance_type)
