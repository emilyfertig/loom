# Copyright (c) 2014, Salesforce.com, Inc.  All rights reserved.
# Copyright (c) 2015, Google, Inc.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# - Redistributions of source code must retain the above copyright
#   notice, this list of conditions and the following disclaimer.
# - Redistributions in binary form must reproduce the above copyright
#   notice, this list of conditions and the following disclaimer in the
#   documentation and/or other materials provided with the distribution.
# - Neither the name of Salesforce.com nor the names of its contributors
#   may be used to endorse or promote products derived from this
#   software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
# OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR
# TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
# USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import numpy
from math import log
from nose.tools import assert_almost_equal
from nose.tools import assert_greater
from nose.tools import assert_less
from nose.tools import assert_less_equal
from distributions.fileutil import tempdir
from goftests import density_goodness_of_fit
from goftests import discrete_goodness_of_fit
import loom.datasets
import loom.preql
import loom.query
from loom.test.util import get_test_kwargs, load_rows
import pytest


MIN_GOODNESS_OF_FIT = 1e-4
SCORE_PLACES = 3
SCORE_TOLERANCE = 10.0**-SCORE_PLACES

SAMPLE_COUNT = 500

# tests are inaccurate with highly imbalanced data
MIN_CATEGORICAL_PROB = 0.03

SEED = 1234


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_score_none(dataset):
    kwargs = get_test_kwargs(dataset)
    with loom.query.get_server(kwargs['root'], debug=True) as server:
        preql = loom.preql.PreQL(server, kwargs['encoding'])
        fnames = preql.feature_names
        assert_less(
            abs(server.score([None for _ in fnames])),
            SCORE_TOLERANCE)


def _check_marginal_samples_match_scores(server, row, fi):
    row = loom.query.protobuf_to_data_row(row.diff)
    row[fi] = None
    to_sample = [i == fi for i in range(len(row))]
    samples = server.sample(to_sample, row, SAMPLE_COUNT)
    val = samples[0][fi]
    base_score = server.score(row)
    if isinstance(val, bool) or isinstance(val, int):
        probs_dict = {}
        samples = [sample[fi] for sample in samples]
        for sample in set(samples):
            row[fi] = sample
            probs_dict[sample] = numpy.exp(
                server.score(row) - base_score)
        if len(probs_dict) == 1:
            assert_almost_equal(probs_dict[sample], 1., places=SCORE_PLACES)
            return
        if min(probs_dict.values()) < MIN_CATEGORICAL_PROB:
            return
        gof = discrete_goodness_of_fit(samples, probs_dict, plot=True)
    elif isinstance(val, float):
        probs = numpy.exp([
            server.score(sample) - base_score
            for sample in samples
        ])
        samples = [sample[fi] for sample in samples]
        gof = density_goodness_of_fit(samples, probs, plot=True)
    assert_greater(gof, MIN_GOODNESS_OF_FIT)


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_samples_match_scores(dataset):
    kwargs = get_test_kwargs(dataset)
    rows = load_rows(kwargs['rows'])
    rows = rows[:: len(rows) // 5]
    with tempdir():
        loom.config.config_dump({'seed': SEED}, 'config.pb.gz')
        with loom.query.get_server(kwargs['root'], 'config.pb.gz', debug=True) as server:
            for row in rows:
                _check_marginal_samples_match_scores(server, row, 0)


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_entropy(dataset):
    sample_count = 1000
    kwargs = get_test_kwargs(dataset)
    root = kwargs['root']
    paths = loom.store.get_paths(root)
    with loom.query.get_server(paths['root']) as server:
        rows = load_rows(paths['ingest']['rows'])
        rows = rows[:4]
        rows = [loom.query.protobuf_to_data_row(row.diff) for row in rows]
        rows = [[None] * len(rows[0])] + rows
        for row in rows:
            to_sample = [val is None for val in row]
            samples = server.sample(
                conditioning_row=row,
                to_sample=to_sample,
                sample_count=sample_count)
            base_score = server.score(row)
            scores = numpy.array(list(server.batch_score(samples)))
            py_estimate = loom.query.get_estimate(base_score - scores)

            feature_set = frozenset(i for i, ts in enumerate(to_sample) if ts)
            cpp_estimate = server.entropy(
                row_sets=[feature_set],
                col_sets=[feature_set],
                conditioning_row=row,
                sample_count=sample_count)[feature_set]

            assert_estimate_close(cpp_estimate, py_estimate)


def assert_estimate_close(actual, expected):
    print(actual.mean, expected.mean, actual.variance, expected.variance)
    sigma = (actual.variance + expected.variance) ** 0.5
    assert_less_equal(abs(actual.mean - expected.mean), 4.0 * sigma)
    assert_less(abs(log(actual.variance / expected.variance)), 1.0)
