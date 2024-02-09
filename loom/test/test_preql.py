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

import os
import csv
import numpy
import pandas
from io import StringIO
from nose import SkipTest
from nose.tools import assert_almost_equal
from nose.tools import assert_equal
from nose.tools import assert_raises
from nose.tools import assert_true
from distributions.fileutil import tempdir
from distributions.io.stream import json_load
from distributions.io.stream import open_compressed
from distributions.io.stream import protobuf_stream_load
from distributions.tests.util import assert_close
import loom.preql
from loom.format import load_encoder
from loom.test.util import CLEANUP_ON_ERROR
from loom.test.util import get_test_kwargs
from loom.test.util import load_rows_csv
import pytest

COUNT = 10


def make_fully_observed_row(rows_csv):
    rows = iter(load_rows_csv(rows_csv))
    header = next(rows)
    try:
        id_pos = header.index('_id')
    except ValueError:
        id_pos = None
    dense_row = ['' for _ in header]
    for row in rows:
        if not any(condition == '' for condition in dense_row):
            if id_pos is not None:
                dense_row.pop(id_pos)
            return dense_row

        for i, (condition, x) in enumerate(zip(dense_row, row)):
            if condition == '':
                dense_row[i] = x
    raise SkipTest('no dense row could be constructed')


def _check_predictions(rows_in, result_out, encoding):
    encoders = json_load(encoding)
    name_to_encoder = {e['name']: load_encoder(e) for e in encoders}
    with open_compressed(rows_in, 'rt') as fin:
        with open(result_out, 'r') as fout:
            in_reader = csv.reader(fin)
            out_reader = csv.reader(fout)
            fnames = next(in_reader)
            next(out_reader)
            for in_row in in_reader:
                for i in range(COUNT):
                    out_row = next(out_reader)
                    bundle = zip(fnames, in_row, out_row)
                    for name, in_val, out_val in bundle:
                        if name == '_id':
                            assert_equal(in_val, out_val)
                            continue
                        encode = name_to_encoder[name]
                        observed = bool(in_val.strip())
                        if observed:
                            assert_almost_equal(encode(in_val), encode(out_val))
                        else:
                            assert_true(bool(out_val.strip()))


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_predict(dataset):
    with tempdir(cleanup_on_error=CLEANUP_ON_ERROR):
        kwargs = get_test_kwargs(dataset)
        root = kwargs['root']
        rows_csv = kwargs['rows_csv']
        encoding = kwargs['encoding']
        with loom.preql.get_server(root, debug=True) as preql:
            result_out = 'predictions_out.csv'
            rows_in = os.listdir(rows_csv)[0]
            rows_in = os.path.join(rows_csv, rows_in)
            preql.predict(rows_in, COUNT, result_out, id_offset=True)
            print('DEBUG', open_compressed(rows_in).read())
            print('DEBUG', open_compressed(result_out).read())
            _check_predictions(rows_in, result_out, encoding)


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_predict_pandas(dataset):
    kwargs = get_test_kwargs(dataset)
    schema = kwargs['schema']
    root = kwargs['root']
    rows_csv = kwargs['rows_csv']
    feature_count = len(json_load(schema))
    with loom.preql.get_server(root, debug=True) as preql:
        rows_filename = os.path.join(rows_csv, os.listdir(rows_csv)[0])
        with open_compressed(rows_filename) as f:
            rows_df = pandas.read_csv(
                f,
                converters=preql.converters,
                index_col='_id')
        print('rows_df =')
        print(rows_df)
        row_count = rows_df.shape[0]
        assert_equal(rows_df.shape[1], feature_count)
        rows_io = StringIO(rows_df.to_csv())
        result_string = preql.predict(rows_io, COUNT, id_offset=True)
        result_df = pandas.read_csv(StringIO(result_string), index_col=False)
        print('result_df =')
        print(result_df)
        assert_equal(result_df.ndim, 2)
        assert_equal(result_df.shape[0], row_count * COUNT)
        assert_equal(result_df.shape[1], 1 + feature_count)


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_relate(dataset):
    with tempdir(cleanup_on_error=CLEANUP_ON_ERROR):
        kwargs = get_test_kwargs(dataset)
        root = kwargs['root']
        with loom.preql.get_server(root, debug=True) as preql:
            result_out = 'related_out.csv'
            preql.relate(preql.feature_names, result_out, sample_count=10)
            with open(result_out, 'r') as f:
                reader = csv.reader(f)
                header = next(reader)
                columns = header[1:]
                assert_equal(columns, preql.feature_names)
                zmatrix = numpy.zeros((len(columns), len(columns)))
                for i, row in enumerate(reader):
                    column = row.pop(0)
                    assert_equal(column, preql.feature_names[i])
                    for j, score in enumerate(row):
                        score = float(score)
                        zmatrix[i][j] = score
                assert_close(zmatrix, zmatrix.T)


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_relate_pandas(dataset):
    kwargs = get_test_kwargs(dataset)
    schema = kwargs['schema']
    root = kwargs['root']
    feature_count = len(json_load(schema))
    with loom.preql.get_server(root, debug=True) as preql:
        result_string = preql.relate(preql.feature_names)
        result_df = pandas.read_csv(StringIO(result_string), index_col=0)
        print('result_df =')
        print(result_df)
        assert_equal(result_df.ndim, 2)
        assert_equal(result_df.shape[0], feature_count)
        assert_equal(result_df.shape[1], feature_count)


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_refine_with_conditions(dataset):
    kwargs = get_test_kwargs(dataset)
    root = kwargs['root']
    rows_csv = kwargs['rows_csv']
    with loom.preql.get_server(root, debug=True) as preql:
        features = preql.feature_names
        conditions = make_fully_observed_row(rows_csv)
        preql.refine(
            target_feature_sets=None,
            query_feature_sets=None,
            conditioning_row=None)
        target_feature_sets = [
            [features[0], features[1]],
            [features[2]]]
        query_feature_sets = [
            [features[0], features[1]],
            [features[2]],
            [features[3]]]
        assert_raises(
            ValueError,
            preql.refine,
            target_feature_sets,
            query_feature_sets,
            conditions)
        conditions[0] = None
        assert_raises(
            ValueError,
            preql.refine,
            target_feature_sets,
            query_feature_sets,
            conditions)
        conditions[1] = None
        conditions[2] = None
        conditions[3] = None
        preql.refine(
            target_feature_sets,
            query_feature_sets,
            conditions)


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_refine_shape(dataset):
    kwargs = get_test_kwargs(dataset)
    root = kwargs['root']
    with loom.preql.get_server(root, debug=True) as preql:
        features = preql.feature_names
        target_sets = [
            features[2 * i : 2 * (i + 1)] for i in range(len(features) // 2)
        ]
        query_sets = [
            features[2 * i : 2 * (i + 1)] for i in range(len(features) // 2)
        ]
        result = preql.refine(target_sets, query_sets, sample_count=10)
        reader = csv.reader(StringIO(result))
        header = next(reader)
        header.pop(0)
        assert_equal(header, list(map(min, query_sets)))
        for row, target_set in zip(reader, target_sets):
            label = row.pop(0)
            assert_equal(label, min(target_set))
            assert_equal(len(row), len(query_sets))


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_support_with_conditions(dataset):
    kwargs = get_test_kwargs(dataset)
    root = kwargs['root']
    rows_csv = kwargs['rows_csv']
    with loom.preql.get_server(root, debug=True) as preql:
        features = preql.feature_names
        conditions = make_fully_observed_row(rows_csv)
        target_feature_sets = [
            [features[0], features[1]],
            [features[2]]]
        observed_feature_sets = [
            [features[0], features[1]],
            [features[2]],
            [features[3]]]
        preql.support(
            target_feature_sets,
            observed_feature_sets,
            conditions)
        conditions[5] = None
        preql.support(
            target_feature_sets,
            observed_feature_sets,
            conditions)
        conditions[0] = None
        assert_raises(
            ValueError,
            preql.support,
            target_feature_sets,
            observed_feature_sets,
            conditions)


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_support_shape(dataset):
    kwargs = get_test_kwargs(dataset)
    root = kwargs['root']
    rows_csv = kwargs['rows_csv']
    with loom.preql.get_server(root, debug=True) as preql:
        features = preql.feature_names
        conditioning_row = make_fully_observed_row(rows_csv)
        target_sets = [
            features[2 * i : 2 * (i + 1)] for i in range(len(features) // 2)
        ]
        observed_sets = [
            features[2 * i : 2 * (i + 1)] for i in range(len(features) // 2)
        ]
        result = preql.support(
            target_sets,
            observed_sets,
            conditioning_row,
            sample_count=10)
        reader = csv.reader(StringIO(result))
        header = next(reader)
        header.pop(0)
        assert_equal(header, list(map(min, observed_sets)))
        for row, target_set in zip(reader, target_sets):
            label = row.pop(0)
            assert_equal(label, min(target_set))
            assert_equal(len(row), len(observed_sets))


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_group_runs(dataset):
    kwargs = get_test_kwargs(dataset)
    root = kwargs['root']
    encoding = kwargs['encoding']
    schema = kwargs['schema']
    with tempdir(cleanup_on_error=CLEANUP_ON_ERROR):
        with loom.preql.get_server(root, encoding, debug=True) as preql:
            test_columns = list(json_load(schema).keys())[:10]
            for column in test_columns:
                groupings_csv = 'group.{}.csv'.format(column)
                preql.group(column, result_out=groupings_csv)
                print(open(groupings_csv).read())


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_group_pandas(dataset):
    kwargs = get_test_kwargs(dataset)
    rows = kwargs['rows']
    root = kwargs['root']
    row_count = sum(1 for _ in protobuf_stream_load(rows))
    with loom.preql.get_server(root, debug=True) as preql:
        feature_names = preql.feature_names
        for feature in feature_names[:10]:
            result_string = preql.group(feature)
            result_df = pandas.read_csv(StringIO(result_string), index_col=0)
            print('result_df =')
            print(result_df)
            assert_equal(result_df.ndim, 2)
            assert_equal(result_df.shape[0], row_count)
            assert_equal(result_df.shape[1], 2)


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_search_runs(dataset):
    kwargs = get_test_kwargs(dataset)
    rows_csv = kwargs['rows_csv']
    root = kwargs['root']
    rows = load_rows_csv(rows_csv)
    header = rows.pop(0)
    try:
        id_pos = header.index('_id')
    except ValueError:
        id_pos = None
    rows = rows[0:10]
    with tempdir(cleanup_on_error=CLEANUP_ON_ERROR):
        with loom.preql.get_server(root, debug=True) as preql:
            for i, row in enumerate(rows):
                row.pop(id_pos)
                search_csv = 'search.{}.csv'.format(i)
                preql.search(row, result_out=search_csv)
                open(search_csv).read()


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_similar_runs(dataset):
    kwargs = get_test_kwargs(dataset)
    rows_csv = kwargs['rows_csv']
    root = kwargs['root']
    rows = load_rows_csv(rows_csv)
    header = rows.pop(0)
    try:
        id_pos = header.index('_id')
    except ValueError:
        id_pos = None
    rows = rows[0:10]
    for row in rows:
        row.pop(id_pos)
    with tempdir(cleanup_on_error=CLEANUP_ON_ERROR):
      with loom.preql.get_server(root, debug=True) as preql:
          search_csv = 'search.csv'
          preql.similar(rows, result_out=search_csv)
