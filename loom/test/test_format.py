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
from nose.tools import assert_equal
from distributions.fileutil import tempdir
from distributions.io.stream import protobuf_stream_load
from distributions.tests.util import assert_close
import loom.format
import loom.util
from loom.test.util import get_test_kwargs
from loom.test.util import CLEANUP_ON_ERROR
from loom.test.util import assert_found
from loom.test.util import load_rows
import pytest


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_make_schema(dataset):
    kwargs = get_test_kwargs(dataset)
    with tempdir(cleanup_on_error=CLEANUP_ON_ERROR):
        schema_out = os.path.abspath('schema.json.gz')
        loom.format.make_schema(
            model_in=kwargs['model'],
            schema_out=schema_out)
        assert_found(schema_out)


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_make_fake_encoding(dataset):
    kwargs = get_test_kwargs(dataset)
    with tempdir(cleanup_on_error=CLEANUP_ON_ERROR):
        encoding_out = os.path.abspath('encoding.json.gz')
        loom.format.make_fake_encoding(
            schema_in=kwargs['schema'],
            model_in=kwargs['model'],
            encoding_out=encoding_out)
        assert_found(encoding_out)


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_make_encoding(dataset):
    kwargs = get_test_kwargs(dataset)
    with tempdir(cleanup_on_error=CLEANUP_ON_ERROR):
        encoding = os.path.abspath('encoding.json.gz')
        rows = os.path.abspath('rows.pbs.gz')
        loom.format.make_encoding(
            schema_in=kwargs['schema'],
            rows_in=kwargs['rows_csv'],
            encoding_out=encoding)
        assert_found(encoding)
        loom.format.import_rows(
            encoding_in=encoding, 
            rows_csv_in=kwargs['rows_csv'], 
            rows_out=rows
        )
        assert_found(rows)


def test_load_encoder():
    encoder = loom.format.EXAMPLE_CATEGORICAL_ENCODER
    encode = loom.format.load_encoder(encoder)
    for key, value in encoder['symbols'].items():
        assert_equal(encode(key), value)


def test_load_decoder():
    encoder = loom.format.EXAMPLE_CATEGORICAL_ENCODER
    decode = loom.format.load_decoder(encoder)
    for key, value in encoder['symbols'].items():
        assert_equal(decode(value), key)


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_import_rows(dataset):
    kwargs = get_test_kwargs(dataset)
    with tempdir(cleanup_on_error=CLEANUP_ON_ERROR):
        rows_pbs = os.path.abspath('rows.pbs.gz')
        loom.format.import_rows(
            encoding_in=kwargs['encoding'],
            rows_csv_in=kwargs['rows_csv'],
            rows_out=rows_pbs,
        )
        assert_found(rows_pbs)
        expected_count = sum(1 for _ in protobuf_stream_load(kwargs['rows']))
        actual_count = sum(1 for _ in protobuf_stream_load(rows_pbs))
        assert_equal(actual_count, expected_count)


@pytest.mark.parametrize('dataset', loom.datasets.TEST_CONFIGS)
def test_export_rows(dataset):
    kwargs = get_test_kwargs(dataset)
    with tempdir(cleanup_on_error=CLEANUP_ON_ERROR):
        rows_csv = os.path.abspath('rows_csv')
        rows_pbs = os.path.abspath('rows.pbs.gz')
        loom.format.export_rows(
            encoding_in=kwargs['encoding'],
            rows_in=kwargs['rows'],
            rows_csv_out=rows_csv,
            chunk_size=51)
        assert_found(rows_csv)
        assert_found(os.path.join(rows_csv, 'rows.0.csv.gz'))
        loom.format.import_rows(
            encoding_in=kwargs['encoding'], rows_csv_in=rows_csv, rows_out=rows_pbs
        )
        assert_found(rows_pbs)
        expected = load_rows(kwargs['rows'])
        actual = load_rows(rows_pbs)
        assert_equal(len(actual), len(expected))
        actual.sort(key=lambda row: row.id)
        expected.sort(key=lambda row: row.id)
        expected_data = [row.diff for row in expected]
        actual_data = [row.diff for row in actual]
        assert_close(actual_data, expected_data)
