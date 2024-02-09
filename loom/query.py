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

import uuid
from itertools import chain
from collections import namedtuple
import numpy
from distributions.io.stream import protobuf_stream_read
from distributions.io.stream import protobuf_stream_write
from loom.schema_pb2 import ProductValue
from loom.schema_pb2 import Row
from loom.schema_pb2 import Query
import loom.cFormat
import loom.runner

DEFAULTS = {
    'sample_sample_count': 10,
    'entropy_sample_count': 1000,
    'mutual_information_sample_count': 1000,
    'similar_row_limit': 1000,
    'tile_size': 500,
}
BUFFER_SIZE = 10

Estimate = namedtuple('Estimate', ['mean', 'variance'])


def get_estimate(samples):
    mean = numpy.mean(samples)
    variance = numpy.var(samples) / len(samples)
    return Estimate(mean, variance)


NONE = ProductValue.Observed.NONE
DENSE = ProductValue.Observed.DENSE
SPARSE = ProductValue.Observed.SPARSE


def none_to_protobuf(diff):
    assert isinstance(diff, ProductValue.Diff)
    diff.Clear()
    diff.neg.observed.sparsity = NONE
    diff.pos.observed.sparsity = NONE


def data_row_to_protobuf(data_row, diff):
    assert isinstance(diff, ProductValue.Diff)
    if all([value is None for value in data_row]):
        none_to_protobuf(diff)
        return
    diff.Clear()
    diff.neg.observed.sparsity = NONE
    diff.pos.observed.sparsity = DENSE
    mask = diff.pos.observed.dense
    fields = {
        bool: diff.pos.booleans,
        int: diff.pos.counts,
        float: diff.pos.reals,
    }
    for val in data_row:
        observed = val is not None
        mask.append(observed)
        if observed:
            fields[type(val)].append(val)


def protobuf_to_data_row(diff):
    assert isinstance(diff, ProductValue.Diff)
    assert diff.neg.observed.sparsity == NONE
    data = diff.pos
    packed = chain(data.booleans, data.counts, data.reals)
    return [
        next(packed) if observed else None
        for observed in data.observed.dense
    ]


def load_data_rows(filename):
    for row in loom.cFormat.row_stream_load(filename.encode('utf-8')):
        data = row.iter_data()
        packed = chain(data['booleans'], data['counts'], data['reals'])
        yield [next(packed) if observed else None for observed in data['observed']]


def feature_set_to_protobuf(feature_set, messages):
    message = messages.add()
    message.sparsity = SPARSE
    for i in sorted(feature_set):
        message.sparse.append(i)


class QueryServer(object):
    def __init__(self, protobuf_server):
        self.protobuf_server = protobuf_server

    @property
    def root(self):
        return self.protobuf_server.root

    def close(self):
        self.protobuf_server.close()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def request(self):
        request = Query.Request()
        request.id = str(uuid.uuid4())
        return request

    def sample(self, to_sample, conditioning_row=None, sample_count=None):
        if sample_count is None:
            sample_count = DEFAULTS['sample_sample_count']
        if conditioning_row is None:
            conditioning_row = [None for _ in to_sample]
        assert len(to_sample) == len(conditioning_row)
        request = self.request()
        data_row_to_protobuf(conditioning_row, request.sample.data)
        request.sample.to_sample.sparsity = DENSE
        request.sample.to_sample.dense[:] = to_sample
        request.sample.sample_count = sample_count
        self.protobuf_server.send(request)
        response = self.protobuf_server.receive()
        if response.error:
            raise Exception('\n'.join(response.error))
        samples = []
        for sample in response.sample.samples:
            data_out = protobuf_to_data_row(sample)
            for i, val in enumerate(data_out):
                if val is None:
                    assert to_sample[i] is False
                    data_out[i] = conditioning_row[i]
            samples.append(data_out)
        return samples

    def _send_score(self, row):
        request = self.request()
        data_row_to_protobuf(row, request.score.data)
        self.protobuf_server.send(request)

    def _receive_score(self):
        response = self.protobuf_server.receive()
        if response.error:
            raise Exception('\n'.join(response.error))
        return response.score.score

    def score(self, row):
        self._send_score(row)
        return self._receive_score()

    def batch_score(self, rows, buffer_size=BUFFER_SIZE):
        buffered = 0
        for row in rows:
            self._send_score(row)
            if buffered < buffer_size:
                buffered += 1
            else:
                yield self._receive_score()
        for _ in range(buffered):
            yield self._receive_score()

    def _entropy(
            self,
            row_sets,
            col_sets,
            conditioning_row=None,
            sample_count=None):
        row_sets = list(set(map(frozenset, row_sets)) | set([frozenset()]))
        col_sets = list(set(map(frozenset, col_sets)) | set([frozenset()]))
        if sample_count is None:
            sample_count = DEFAULTS['entropy_sample_count']
        request = self.request()
        if conditioning_row is None:
            none_to_protobuf(request.entropy.conditional)
        else:
            data_row_to_protobuf(conditioning_row, request.entropy.conditional)
        for feature_set in row_sets:
            feature_set_to_protobuf(feature_set, request.entropy.row_sets)
        for feature_set in col_sets:
            feature_set_to_protobuf(feature_set, request.entropy.col_sets)
        request.entropy.sample_count = sample_count
        self.protobuf_server.send(request)
        response = self.protobuf_server.receive()
        if response.error:
            raise Exception('\n'.join(response.error))
        means = response.entropy.means
        variances = response.entropy.variances
        size = len(row_sets) * len(col_sets)
        assert len(means) == size, means
        assert len(variances) == size, variances
        means = iter(means)
        variances = iter(variances)
        return {
            row_set | col_set: Estimate(next(means), next(variances))
            for row_set in row_sets
            for col_set in col_sets
        }

    def entropy(
            self,
            row_sets,
            col_sets,
            conditioning_row=None,
            sample_count=None,
            tile_size=None):
        if tile_size is None:
            tile_size = DEFAULTS['tile_size']
        min_size = max(1, min(tile_size, len(row_sets), len(col_sets)))
        tile_size = tile_size * tile_size // min_size
        assert tile_size > 0, tile_size
        result = {}
        for i in range(0, len(row_sets), tile_size):
            row_tile = row_sets[i: i + tile_size]
            for j in range(0, len(col_sets), tile_size):
                col_tile = col_sets[j: j + tile_size]
                result.update(self._entropy(
                    row_tile,
                    col_tile,
                    conditioning_row,
                    sample_count))
        return result

    def mutual_information(
            self,
            feature_set1,
            feature_set2,
            entropys=None,
            conditioning_row=None,
            sample_count=None):
        '''
        Estimate the mutual information between feature_set1
        and feature_set2 conditioned on conditioning_row
        '''
        if not isinstance(feature_set1, frozenset):
            feature_set1 = frozenset(feature_set1)
        if not isinstance(feature_set2, frozenset):
            feature_set2 = frozenset(feature_set2)

        if sample_count is None:
            sample_count = DEFAULTS['mutual_information_sample_count']
        feature_union = frozenset.union(feature_set1, feature_set2)

        if entropys is None:
            entropys = self.entropy(
                [feature_set1], [feature_set2], conditioning_row, sample_count)
        mi = (
            entropys[feature_set1].mean
            + entropys[feature_set2].mean
            - entropys[feature_union].mean
        )
        variance = (
            entropys[feature_set1].variance
            + entropys[feature_set2].variance
            + entropys[feature_union].variance
        )
        return Estimate(mi, variance)

    def score_derivative(
            self,
            update_row,
            score_rows=None,
            row_limit=None):
        row = Row()
        request = self.request()
        if row_limit is None:
            row_limit = DEFAULTS['similar_row_limit']
        if score_rows is not None:
            for i, data_row in enumerate(score_rows):
                data_row_to_protobuf(
                    data_row,
                    row.diff)
                added_diff = request.score_derivative.score_data.add()
                added_diff.MergeFrom(row.diff)

        request.score_derivative.row_limit = row_limit
        data_row_to_protobuf(
            update_row,
            row.diff)
        request.score_derivative.update_data.MergeFrom(row.diff)

        self.protobuf_server.send(request)
        response = self.protobuf_server.receive()
        if response.error:
            raise Exception('\n'.join(response.error))
        ids = response.score_derivative.ids
        score_diffs = response.score_derivative.score_diffs
        return zip(ids, score_diffs)


class ProtobufServer(object):
    def __init__(self, root, config=None, debug=False, profile=None):
        self.root = root
        self.proc = loom.runner.query(
            root_in=root,
            config_in=config,
            log_out=None,
            debug=debug,
            profile=profile,
            block=False)

    def send(self, request):
        assert isinstance(request, Query.Request), request
        request_string = request.SerializeToString()
        protobuf_stream_write(request_string, self.proc.stdin)
        self.proc.stdin.flush()

    def receive(self):
        response_string = protobuf_stream_read(self.proc.stdout)
        response = Query.Response()
        response.ParseFromString(response_string)
        return response

    def close(self):
        self.proc.stdin.close()
        self.proc.wait()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def get_server(root, config=None, debug=False, profile=None):
    protobuf_server = ProtobufServer(root, config, debug, profile)
    return QueryServer(protobuf_server)
